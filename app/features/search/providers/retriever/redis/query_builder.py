from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.features.search.domain.enums import SortType
from app.features.search.domain.models import (
    RetrievalRequest,
    SearchAccess,
    SearchFilters,
    SearchModifiers,
)
from app.features.search.providers.interfaces import SearchQueryBuilder

logger = logging.getLogger(__name__)

# Characters RediSearch requires escaping with a backslash inside TAG values.
_TAG_ESCAPE_CHARS = set(",.<>{}[]\"':;!@#$%^&*()-+=~| \t\n")


def _escape_tag(value: Any) -> str:
    text = str(value)
    return "".join(
        f"\\{char}" if char in _TAG_ESCAPE_CHARS else char for char in text
    )


@dataclass(frozen=True)
class RedisQuerySpec:
    query_string: str
    sort_by: tuple[str, bool] | None
    offset: int
    limit: int
    match_none: bool = False
    vector_param: bytes | None = None
    extra_return_fields: tuple[str, ...] = ()
    with_scores: bool = False


@dataclass(frozen=True)
class RedisHybridSpec:
    """
    Inputs for a single `FT.HYBRID` call - Redis fuses the SEARCH (lexical)
    and VSIM (semantic) legs server-side via Reciprocal Rank Fusion, so
    unlike the lexical/semantic paths this isn't two round trips plus a
    client-side merge.
    """

    search_query: str
    vector_param: bytes | None
    filter_expr: str
    knn_k: int
    window: int
    rrf_constant: int
    offset: int
    limit: int
    sort_by: tuple[str, bool] | None = None
    match_none: bool = False


_TEXT_SEARCH_FIELDS: tuple[tuple[str, float], ...] = (
    ("text", 5.0),
)

# Relative to each field's base weight above: exact phrase ranks highest,
# then any-term match, then prefix (still-typing), then fuzzy (typo).
_PHRASE_WEIGHT_MULTIPLIER = 2.0
_PREFIX_WEIGHT_MULTIPLIER = 0.6
_FUZZY_WEIGHT_MULTIPLIER = 0.2

# No fuzzy below 3 chars (too many unrelated short-word collisions);
# distance-1 for 3-5 chars, distance-2 for 6+.
_FUZZY_MIN_LEN = 3
_FUZZY_DOUBLE_LEN = 6


def _fuzzy_token(token: str) -> str | None:
    length = len(token)

    if length < _FUZZY_MIN_LEN:
        return None

    if length < _FUZZY_DOUBLE_LEN:
        return f"%{token}%"

    return f"%%{token}%%"


class RedisQueryBuilder(SearchQueryBuilder):

    def __init__(self, rrf_constant: int = 60):
        self._rrf_constant = rrf_constant

    def build(
        self,
        request: RetrievalRequest,
    ) -> RedisQuerySpec | RedisHybridSpec:

        filter_expr = self._build_filter_expression(request.filters)

        if filter_expr is None:
            if request.is_hybrid:
                return RedisHybridSpec(
                    search_query="",
                    vector_param=None,
                    filter_expr="",
                    knn_k=0,
                    window=0,
                    rrf_constant=self._rrf_constant,
                    offset=request.offset,
                    limit=request.limit,
                    match_none=True,
                )

            return RedisQuerySpec(
                query_string="",
                sort_by=None,
                offset=request.offset,
                limit=request.limit,
                match_none=True,
            )

        if request.is_hybrid:
            return self._build_hybrid_spec(request, filter_expr)

        if request.is_lexical:
            return self._build_lexical_spec(request, filter_expr)

        if request.is_semantic:
            return self._build_semantic_spec(request, filter_expr)

        raise ValueError(f"Unsupported retrieval methods: {request.methods}")

    def _build_hybrid_spec(
        self,
        request: RetrievalRequest,
        filter_expr: str,
    ) -> RedisHybridSpec:

        if request.query_vector is None:
            raise ValueError("query_vector is required for hybrid retrieval")

        vector_bytes = struct.pack(
            f"{len(request.query_vector)}f",
            *request.query_vector,
        )

        text_clause = self._build_text_clause(request.query)

        search_query = (
            f"({filter_expr}) ({text_clause})" if text_clause else filter_expr
        )

        knn_k = min(max(request.limit // 4, 1), request.limit - 1)

        window = max(request.pagination_depth, request.limit)

        return RedisHybridSpec(
            search_query=search_query,
            vector_param=vector_bytes,
            filter_expr=filter_expr,
            knn_k=knn_k,
            window=window,
            rrf_constant=self._rrf_constant,
            offset=request.offset,
            limit=request.limit,
            sort_by=self._build_sort(request),
        )

    def _build_lexical_spec(
        self,
        request: RetrievalRequest,
        filter_expr: str,
    ) -> RedisQuerySpec:

        text_clause = self._build_text_clause(request.query)

        query_string = (
            f"({filter_expr}) ({text_clause})" if text_clause else filter_expr
        )

        return RedisQuerySpec(
            query_string=query_string,
            sort_by=self._build_sort(request),
            offset=request.offset,
            limit=request.limit,
            with_scores=True,
        )

    def _build_semantic_spec(
        self,
        request: RetrievalRequest,
        filter_expr: str,
    ) -> RedisQuerySpec:

        if request.query_vector is None:
            raise ValueError("query_vector is required for semantic retrieval")

        vector_bytes = struct.pack(
            f"{len(request.query_vector)}f",
            *request.query_vector,
        )

        k = request.limit * 2

        query_string = f"({filter_expr})=>[KNN {k} @embedding $vec AS vector_score]"

        return RedisQuerySpec(
            query_string=query_string,
            sort_by=self._build_sort(request, default=("vector_score", True)),
            offset=request.offset,
            limit=request.limit,
            vector_param=vector_bytes,
            extra_return_fields=("vector_score",),
        )

    @staticmethod
    def _build_text_clause(query: str) -> str:
        # Every token is escaped before it ever reaches the query string -
        # RediSearch's query parser treats characters like ) | * % @ " as
        # syntax, not literal text, and filter_expr (carrying the tenant/
        # authorization scoping) lives in the same raw query string. An
        # unescaped ")" in a user's own search text could otherwise alter
        # the query's structure around that filter.
        raw_tokens = query.strip().split()

        if not raw_tokens:
            return ""

        tokens = [_escape_tag(token) for token in raw_tokens]

        # RediSearch treats space-separated terms inside a field clause as
        # an implicit AND ("match every term"), unlike OpenSearch's
        # multi_match/best_fields which matches on ANY term and lets BM25
        # scoring reward documents that match more of them. OR-ing the
        # terms with "|" reproduces that "match any term" behavior instead
        # of requiring the full query text verbatim - this is the baseline
        # "any term" tier below; phrase/prefix/fuzzy tiers are additional,
        # more and less precise alternatives OR'd alongside it.
        any_term = "|".join(tokens)

        phrase_term = " ".join(tokens)

        # Prefix only the last token ("still typing" the final word) -
        # prefixing every token would widen the match far more aggressively
        # than intended for a simple truncated-word case.
        prefix_tokens = list(tokens)
        prefix_tokens[-1] = f"{prefix_tokens[-1]}*"
        prefix_term = "|".join(prefix_tokens)

        # Fuzzy is gated per-token by length (any position, unlike prefix -
        # a typo can land anywhere, not just the word being typed right now).
        fuzzy_tokens = [_fuzzy_token(token) or token for token in tokens]
        has_fuzzy = fuzzy_tokens != tokens
        fuzzy_term = "|".join(fuzzy_tokens)

        clauses = []

        for field, weight in _TEXT_SEARCH_FIELDS:
            clauses.append(
                f'(@{field}:"{phrase_term}")=>'
                f"{{$weight: {weight * _PHRASE_WEIGHT_MULTIPLIER}}}"
            )
            clauses.append(
                f"(@{field}:({any_term}))=>{{$weight: {weight}}}"
            )
            clauses.append(
                f"(@{field}:({prefix_term}))=>"
                f"{{$weight: {weight * _PREFIX_WEIGHT_MULTIPLIER}}}"
            )
            if has_fuzzy:
                clauses.append(
                    f"(@{field}:({fuzzy_term}))=>"
                    f"{{$weight: {weight * _FUZZY_WEIGHT_MULTIPLIER}}}"
                )

        return " | ".join(clauses)

    def _build_filter_expression(
        self,
        filters: SearchFilters,
    ) -> str | None:

        clauses = [
            f"@site_id:{{{_escape_tag(filters.access.site_id)}}}",
            "@is_deleted:{false}",
        ]

        auth_clause = self._build_authorization_clause(filters.access)

        if auth_clause is None:
            return None

        clauses.append(auth_clause)

        if filters.channel_types:
            values = "|".join(
                _escape_tag(channel_type.value)
                for channel_type in filters.channel_types
            )
            clauses.append(f"@channel_type:{{{values}}}")

        if filters.modifiers:
            clauses.extend(self._build_modifier_clauses(filters.modifiers))

        date_clause = self._build_date_clause(
            after=filters.after,
            before=filters.before,
        )

        if date_clause:
            clauses.append(date_clause)

        return " ".join(clauses)

    @staticmethod
    def _build_authorization_clause(access: SearchAccess) -> str | None:

        conditions: list[str] = []

        if access.allowed_sids:
            values = "|".join(_escape_tag(sid) for sid in access.allowed_sids)
            conditions.append(f"@sid:{{{values}}}")

        if access.contributor_thread_root_ids:
            values = "|".join(
                _escape_tag(thread_root_id)
                for thread_root_id in access.contributor_thread_root_ids
            )
            conditions.append(f"@thread_root_id:{{{values}}}")

        if not conditions:
            return None

        return "(" + " | ".join(conditions) + ")"

    @staticmethod
    def _build_modifier_clauses(modifiers: SearchModifiers) -> list[str]:

        clauses: list[str] = []

        if modifiers.sids:
            values = "|".join(_escape_tag(sid) for sid in modifiers.sids)
            clauses.append(f"@sid:{{{values}}}")

        if modifiers.author_ids:
            values = "|".join(
                _escape_tag(author_id) for author_id in modifiers.author_ids
            )
            clauses.append(f"@author_archive_id:{{{values}}}")

        return clauses

    @staticmethod
    def _build_date_clause(
        *,
        after: datetime | None,
        before: datetime | None,
    ) -> str | None:

        if after is None and before is None:
            return None

        lower = f"{after.timestamp()}" if after is not None else "-inf"
        upper = f"({before.timestamp()}" if before is not None else "+inf"

        return f"@created_at:[{lower} {upper}]"

    @staticmethod
    def _build_sort(
        request: RetrievalRequest,
        default: tuple[str, bool] | None = None,
    ) -> tuple[str, bool] | None:

        if request.sort == SortType.TIMESTAMP:
            return ("created_at", request.sort_direction.value == "asc")

        # SortType.SCORE uses each method's natural relevance ordering
        # (BM25 for lexical, vector distance for semantic, the RRF fused
        # score for hybrid) - `default` is None for lexical/hybrid since
        # RediSearch/FT.HYBRID already return that order without a SORTBY.
        return default
