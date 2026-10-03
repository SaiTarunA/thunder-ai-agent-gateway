import logging
from datetime import datetime, timezone
from typing import Any

from app.features.search.domain.enums import (
    ChannelType,
    ContentType,
    RetrievalMethodType,
    RetrievalStatus,
)
from app.features.search.domain.models import (
    MessagesData,
    RetrievalCandidate,
    RetrievalResult,
)
from app.features.search.providers.interfaces import ResponseMapper

logger = logging.getLogger(__name__)

# Reserved FT.HYBRID field carrying the document's key - see
# https://redis.io/docs/latest/commands/ft.hybrid/#reserved-fields
_HYBRID_KEY_FIELD = "__key"


class RedisResponseMapper(ResponseMapper):
    """
    Maps RediSearch `FT.SEARCH`/`FT.HYBRID` results into retriever-agnostic
    domain models.

    Lexical-only and semantic-only requests go through `FT.SEARCH`, whose
    results are `Document` objects (attribute access). Hybrid requests go
    through `FT.HYBRID`, which fuses the lexical and semantic legs
    server-side (RRF) and returns plain dicts. `_field` abstracts over
    both shapes so the rest of this mapper doesn't care which one it got.
    """

    def map_response(
        self,
        response: Any,
        retrieval_methods: tuple[RetrievalMethodType, ...],
        latency_ms: int,
    ) -> RetrievalResult:

        offset = response.get("offset", 0)

        if "hybrid" in response:
            return self._map_hybrid(
                response.get("hybrid"),
                retrieval_methods,
                latency_ms,
                offset,
            )

        return self._map_single(
            response.get("results"),
            retrieval_methods,
            latency_ms,
            offset,
        )

    def _map_single(
        self,
        result: Any,
        methods: tuple[RetrievalMethodType, ...],
        latency_ms: int,
        offset: int,
    ) -> RetrievalResult:

        if result is None:
            return RetrievalResult(
                methods=methods,
                status=RetrievalStatus.SUCCESS,
                candidates=[],
                latency_ms=latency_ms,
            )

        candidates = [
            self._map_doc(
                doc=doc,
                document_id=doc.id,
                rank=rank,
                offset=offset,
                # BM25 "score" is only populated when the query used
                # WITHSCORES (the lexical path) - a pure KNN query has no
                # relevance score, only the loaded "vector_score" distance.
                raw_score=(
                    self._field(doc, "score")
                    if self._field(doc, "score") is not None
                    else self._field(doc, "vector_score")
                ),
            )
            for rank, doc in enumerate(result.docs, start=1)
        ]

        return RetrievalResult(
            methods=methods,
            status=RetrievalStatus.SUCCESS,
            candidates=candidates,
            latency_ms=latency_ms,
        )

    def _map_hybrid(
        self,
        hybrid_result: Any,
        methods: tuple[RetrievalMethodType, ...],
        latency_ms: int,
        offset: int,
    ) -> RetrievalResult:

        if hybrid_result is None:
            return RetrievalResult(
                methods=methods,
                status=RetrievalStatus.SUCCESS,
                candidates=[],
                latency_ms=latency_ms,
            )

        candidates = [
            self._map_doc(
                doc=item,
                document_id=(
                    self._field(item, _HYBRID_KEY_FIELD)
                    or self._field(item, "message_id")
                ),
                rank=rank,
                offset=offset,
                raw_score=self._field(item, "combined_score"),
            )
            for rank, item in enumerate(hybrid_result.results, start=1)
        ]

        return RetrievalResult(
            methods=methods,
            status=RetrievalStatus.SUCCESS,
            candidates=candidates,
            latency_ms=latency_ms,
        )

    def _map_doc(
        self,
        *,
        doc: Any,
        document_id: Any,
        rank: int,
        offset: int,
        raw_score: Any,
    ) -> RetrievalCandidate:

        return RetrievalCandidate(
            document_id=str(document_id),
            content_type=ContentType.MESSAGES,
            data=self._map_message(doc),
            rank=rank,
            raw_score=float(raw_score) if raw_score is not None else None,
            # RediSearch has no search_after cursor - every candidate carries
            # its absolute position so SearchService's existing offset-based
            # cursor logic (_get_next_search_after / _get_next_offset) just
            # works, regardless of which of the two paths it takes.
            sort_values=(offset + rank,),
        )

    def _map_message(self, doc: Any) -> MessagesData:

        created_at_raw = self._field(doc, "created_at")
        updated_at_raw = self._field(doc, "updated_at")
        thread_root_id_raw = self._field(doc, "thread_root_id")
        parent_message_id_raw = self._field(doc, "parent_message_id")

        return MessagesData(
            message_id=int(self._field(doc, "message_id")),
            sid=int(self._field(doc, "sid")),
            text=self._field(doc, "text", "") or "",
            author_archive_id=int(self._field(doc, "author_archive_id")),
            author_name=self._field(doc, "author_name") or None,
            author_username=self._field(doc, "author_username") or None,
            channel_name=self._field(doc, "channel_name") or None,
            channel_type=(
                ChannelType(self._field(doc, "channel_type"))
                if self._field(doc, "channel_type")
                else None
            ),
            created_at=(
                datetime.fromtimestamp(float(created_at_raw), tz=timezone.utc)
                if created_at_raw
                else None
            ),
            updated_at=(
                datetime.fromtimestamp(float(updated_at_raw), tz=timezone.utc)
                if updated_at_raw
                else None
            ),
            thread_root_id=(
                int(thread_root_id_raw)
                if thread_root_id_raw not in (None, "")
                else None
            ),
            parent_message_id=(
                int(parent_message_id_raw)
                if parent_message_id_raw not in (None, "")
                else None
            ),
            is_thread_reply=self._field(doc, "is_thread_reply") == "true",
            is_edited=self._field(doc, "is_edited") == "true",
            is_pinned=self._field(doc, "is_pinned") == "true",
        )

    @staticmethod
    def _field(doc: Any, name: str, default: Any = None) -> Any:
        if isinstance(doc, dict):
            value = doc.get(name, default)
        else:
            value = getattr(doc, name, default)

        # FT.HYBRID's legacy RESP2 reply path doesn't honor the per-field
        # decode_field=True passed to HybridPostProcessingConfig.load(...),
        # so values can come back as raw bytes regardless. FT.SEARCH's
        # Document already decodes by default, so this is a no-op there.
        if isinstance(value, bytes):
            return value.decode("utf-8")

        return value
