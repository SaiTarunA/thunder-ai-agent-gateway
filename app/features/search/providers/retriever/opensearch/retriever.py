import logging
import time
from typing import Any

from opensearchpy.exceptions import (
    ConnectionError,
    RequestError,
    TransportError,
)

from app.features.search.domain.enums import RetrievalStatus
from app.features.search.domain.models import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalStatus,
)
from app.features.search.providers.interfaces import (
    ResponseMapper,
    Retriever,
    SearchQueryBuilder,
)
from app.features.search.providers.retriever.opensearch.indexes.resolver import (
    OpenSearchIndexResolver,
)

logger = logging.getLogger(__name__)


class OpenSearchRetriever(Retriever):

    def __init__(
        self,
        *,
        client: Any,
        query_builder: SearchQueryBuilder,
        response_mapper: ResponseMapper,
        index_resolver: OpenSearchIndexResolver,
        hybrid_search_pipeline: str,
    ):
        self._client = client
        self._query_builder = query_builder
        self._response_mapper = response_mapper
        self._index_resolver = index_resolver
        self._hybrid_search_pipeline = hybrid_search_pipeline

    async def retrieve(
        self,
        request: RetrievalRequest,
    ) -> RetrievalResult:
        started_at = time.perf_counter()

        try:

            index = self._index_resolver.resolve(
                request.content_type,
            )

            logger.info(
                f"retrieve :: content_type :: {request.content_type}, "
                f"index :: {index}, methods :: {request.methods}"
            )

            body = self._query_builder.build(request)

            kwargs = {
                "index": index,
                "body": body,
            }

            if request.is_hybrid:
                kwargs["params"] = {
                    "search_pipeline": (
                        self._hybrid_search_pipeline
                    ),
                }

                logger.debug(
                    f"retrieve :: using hybrid search pipeline :: {self._hybrid_search_pipeline}"
                )

            response = await self._client.search(
                **kwargs,
            )

            logger.info(
                f"retrieve :: content_type :: {request.content_type}, "
                f"index :: {index}, response :: {response}"
            )

            latency_ms = int(
                (time.perf_counter() - started_at) * 1000
            )

            result = self._response_mapper.map_response(
                response=response,
                methods=request.methods,
                latency_ms=latency_ms,
            )

            logger.info(
                f"retrieve :: content_type :: {request.content_type}, "
                f"index :: {index}, status :: {result.status}, "
                f"candidates :: {len(result.candidates)}, latency_ms :: {latency_ms}"
            )

            return result

        except (
            ConnectionError,
            TransportError,
            RequestError,
        ) as exc:
            latency_ms = int(
                (time.perf_counter() - started_at) * 1000
            )

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