import logging
import time
from typing import Any

from redis.commands.search.hybrid_query import (
    CombinationMethods,
    CombineResultsMethod,
    HybridFilter,
    HybridPostProcessingConfig,
    HybridQuery,
    HybridSearchQuery,
    HybridVsimQuery,
    VectorSearchMethods,
)
from redis.commands.search.query import Query, SortbyField
from redis.exceptions import RedisError

from app.features.search.domain.enums import RetrievalStatus
from app.features.search.domain.models import RetrievalRequest, RetrievalResult
from app.features.search.providers.interfaces import (
    ResponseMapper,
    Retriever,
    SearchQueryBuilder,
)
from app.features.search.providers.retriever.redis.indexes.resolver import (
    RedisIndexResolver,
)
from app.features.search.providers.retriever.redis.query_builder import (
    RedisHybridSpec,
    RedisQuerySpec,
)

logger = logging.getLogger(__name__)

_RETURN_FIELDS = (
    "message_id",
    "sid",
    "text",
    "author_archive_id",
    "channel_type",
    "created_at",
    "updated_at",
    "thread_root_id",
    "parent_message_id",
    "is_thread_reply",
    "is_edited",
)

_COMBINED_SCORE_ALIAS = "combined_score"


class RedisRetriever(Retriever):

    def __init__(
        self,
        *,
        client: Any,
        query_builder: SearchQueryBuilder,
        response_mapper: ResponseMapper,
        index_resolver: RedisIndexResolver,
    ):
        self._client = client
        self._query_builder = query_builder
        self._response_mapper = response_mapper
        self._index_resolver = index_resolver

    async def retrieve(
        self,
        request: RetrievalRequest,
    ) -> RetrievalResult:
        started_at = time.perf_counter()

        try:
            index_name = self._index_resolver.resolve(request.content_type)

            logger.info(
                f"retrieve :: content_type :: {request.content_type}, "
                f"index :: {index_name}, methods :: {request.methods}"
            )

            spec = self._query_builder.build(request)

            ft = self._client.ft(index_name)

            response: dict[str, Any] = {"offset": request.offset}

            if isinstance(spec, RedisHybridSpec):
                response["hybrid"] = (
                    None if spec.match_none else await self._hybrid_search(ft, spec)
                )
            else:
                response["results"] = (
                    None if spec.match_none else await self._search(ft, spec)
                )

            logger.info(
                f"retrieve :: content_type :: {request.content_type}, "
                f"index :: {index_name}, response :: {response}"
            )

            latency_ms = int((time.perf_counter() - started_at) * 1000)

            result = self._response_mapper.map_response(
                response=response,
                retrieval_methods=request.methods,
                latency_ms=latency_ms,
            )

            logger.info(
                f"retrieve :: content_type :: {request.content_type}, "
                f"index :: {index_name}, status :: {result.status}, "
                f"candidates :: {len(result.candidates)}, latency_ms :: {latency_ms}"
            )

            return result

        except RedisError as exc:
            latency_ms = int((time.perf_counter() - started_at) * 1000)

            logger.error(
                f"retrieve :: content_type :: {request.content_type} failed after "
                f"{latency_ms}ms :: {exc}"
            )

            return RetrievalResult(
                methods=request.methods,
                status=RetrievalStatus.FAILED,
                candidates=[],
                latency_ms=latency_ms,
                error=str(exc),
            )

    @staticmethod
    async def _search(ft: Any, spec: RedisQuerySpec):
        query = (
            Query(spec.query_string)
            .paging(spec.offset, spec.limit)
            .dialect(2)
            .return_fields(*_RETURN_FIELDS, *spec.extra_return_fields)
        )

        if spec.with_scores:
            query = query.with_scores()

        if spec.sort_by is not None:
            field_name, asc = spec.sort_by
            query = query.sort_by(field_name, asc=asc)

        if spec.vector_param is not None:
            return await ft.search(query, query_params={"vec": spec.vector_param})

        return await ft.search(query)

    @staticmethod
    async def _hybrid_search(ft: Any, spec: RedisHybridSpec):
        search_query = HybridSearchQuery(spec.search_query)

        vsim_query = HybridVsimQuery(
            vector_field_name="@embedding",
            vector_data="$vec",
            vsim_search_method=VectorSearchMethods.KNN,
            vsim_search_method_params={"K": spec.knn_k},
            filter=HybridFilter(spec.filter_expr),
        )

        combine_method = CombineResultsMethod(
            CombinationMethods.RRF,
            WINDOW=spec.window,
            CONSTANT=spec.rrf_constant,
            YIELD_SCORE_AS=_COMBINED_SCORE_ALIAS,
        )

        post_processing = (
            HybridPostProcessingConfig()
            .load(*(f"@{field}" for field in _RETURN_FIELDS), decode_field=True)
            .limit(spec.offset, spec.limit)
        )

        if spec.sort_by is not None:
            field_name, asc = spec.sort_by
            # Unlike FT.SEARCH's sort_by (bare field name), FT.HYBRID's
            # post-processing SORTBY is an aggregation-pipeline step and
            # requires the "@" prefix.
            post_processing = post_processing.sort_by(
                SortbyField(f"@{field_name}", asc=asc)
            )

        return await ft.hybrid_search(
            HybridQuery(search_query, vsim_query),
            combine_method=combine_method,
            post_processing=post_processing,
            params_substitution={"vec": spec.vector_param},
        )
