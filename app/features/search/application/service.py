from datetime import datetime, timezone
import logging

from app.features.search.config import SearchConfig
from app.features.search.domain.cursor import (
    SearchCursorCodec,
    SearchQueryHasher,
)
from app.features.search.domain.enums import (
    RetrievalMethodType,
    RetrievalStatus,
    SortDirectionType,
    SortType,
)
from app.features.search.domain.models import (
    MessagesData,
    RetrievalPlan,
    RetrievalRequest,
    RetrievalResult,
    SearchContextRequest,
    SearchCursor,
)
from app.features.search.domain.response_models import (
    ContentTypeResultResponse,
    MessageSearchResult,
    SearchCandidateResponse,
    SearchResponse,
    SearchResponseMetadata,
)
from app.features.search.providers.interfaces import (
    EmbeddingProvider,
    Retriever,
)
from app.features.search.application.authorization_resolver import (
    AuthorizationResolver,
)
from app.features.search.application.filter_resolver import FilterResolver
from app.features.search.application.query_processor import QueryProcessor
from app.features.search.application.retrieval_planner import RetrievalPlanner

logger = logging.getLogger(__name__)


class SearchService:

    def __init__(
        self,
        *,
        config: SearchConfig,
        query_processor: QueryProcessor,
        authorization_resolver: AuthorizationResolver,
        filter_resolver: FilterResolver,
        retrieval_planner: RetrievalPlanner,
        embedding_provider: EmbeddingProvider,
        retriever: Retriever,
        cursor_codec: SearchCursorCodec,
        query_hasher: SearchQueryHasher,
    ):
        self._config = config
        self._query_processor = query_processor
        self._authorization_resolver = authorization_resolver
        self._filter_resolver = filter_resolver
        self._retrieval_planner = retrieval_planner
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._cursor_codec = cursor_codec
        self._query_hasher = query_hasher

    async def search(
        self,
        request: SearchContextRequest,
    ):
        logger.info(
            f"search :: siteid :: {request.siteid}, archiveid :: {request.archiveid}, "
            f"content_types :: {request.content_types}"
        )

        processed_query = self._query_processor.process(
            request.query,
        )

        site_id = int(request.siteid)

        if request.archiveid is None:
            logger.error("search :: archiveid is required for search authorization")
            raise ValueError("archiveid is required for search authorization")

        archive_id = int(request.archiveid)

        access = await self._authorization_resolver.resolve(
            site_id=site_id,
            archive_id=archive_id,
        )

        logger.debug(
            f"search :: resolved access :: allowed_sids :: {access.allowed_sids}, "
            f"contributor_thread_root_ids :: {access.contributor_thread_root_ids}"
        )

        filters = self._filter_resolver.resolve(
            access=access,
            context_request=request,
        )

        plan = self._retrieval_planner.plan(request)

        logger.debug(
            f"search :: retrieval plan :: methods :: {plan.methods}, "
            f"limit :: {plan.limit}, pagination_depth :: {plan.pagination_depth}"
        )

        query_hash = self._query_hasher.hash(
            request,
            normalized_query=processed_query.normalized_query,
        )

        cursor = None

        if request.cursor:
            cursor = self._cursor_codec.decode(
                request.cursor,
            )

            self._validate_cursor(
                cursor=cursor,
                query_hash=query_hash,
                plan=plan,
                request=request,
            )

            logger.debug("search :: cursor decoded and validated")

        query_vector = None

        if RetrievalMethodType.SEMANTIC in plan.methods:
            logger.debug("search :: embedding query for semantic retrieval")

            query_vector = await self._embedding_provider.embed_text(
                processed_query.embedding_query,
            )

        use_offset_pagination = (
            RetrievalMethodType.LEXICAL in plan.methods
            and RetrievalMethodType.SEMANTIC in plan.methods
            and request.sort == SortType.SCORE
            and request.sort_direction == SortDirectionType.DESC
        )

        results = {}
        next_search_after = {}

        for content_type in request.content_types or ():
            results[content_type] = None

            if cursor is not None:
                if content_type in cursor.search_after:
                    search_after = cursor.search_after[content_type]

                    if search_after is None:
                        logger.debug(
                            f"search :: content_type :: {content_type} exhausted, skipping"
                        )
                        continue
                else:
                    search_after = None
            else:
                search_after = None

            current_offset = (
                int(search_after[0])
                if use_offset_pagination and search_after is not None
                else 0
            )

            retrieval_request = RetrievalRequest(
                query=processed_query.lexical_query,
                query_vector=query_vector,
                filters=filters,
                methods=plan.methods,
                content_type=content_type,
                limit=plan.limit,
                pagination_depth=plan.pagination_depth,
                sort=request.sort,
                sort_direction=request.sort_direction,
                search_after=search_after,
            )

            logger.info(f"search :: retrieving content_type :: {content_type}")

            retrieval_result = await self._retriever.retrieve(
                retrieval_request,
            )

            logger.debug(
                f"search :: content_type :: {content_type}, "
                f"candidates :: {len(retrieval_result.candidates)}"
            )

            results[content_type] = retrieval_result

            if use_offset_pagination:
                next_search_after[content_type] = self._get_next_offset(
                    candidates=retrieval_result.candidates,
                    limit=plan.limit,
                    current_offset=current_offset,
                )
            else:
                next_search_after[content_type] = self._get_next_search_after(
                    candidates=retrieval_result.candidates,
                    limit=plan.limit,
                )

        next_cursor = self._build_next_cursor(
            request=request,
            query_hash=query_hash,
            plan=plan,
            search_after=next_search_after,
        )

        logger.info(
            f"search :: completed :: content_types :: {list(results.keys())}, "
            f"has_next_cursor :: {next_cursor is not None}"
        )

        return self._build_response(
            results=results,
            next_cursor=next_cursor,
        ).model_dump(mode="json")

    def _build_response(
        self,
        *,
        results: dict,
        next_cursor: str | None,
    ) -> SearchResponse:

        response_results = {
            content_type: self._build_content_type_result(retrieval_result)
            for content_type, retrieval_result in results.items()
        }

        return SearchResponse(
            ok=True,
            results=response_results,
            response_metadata=SearchResponseMetadata(
                next_cursor=next_cursor,
            ),
        )

    def _build_content_type_result(
        self,
        retrieval_result: RetrievalResult | None,
    ) -> ContentTypeResultResponse:

        if retrieval_result is None:
            return ContentTypeResultResponse(
                status=RetrievalStatus.SUCCESS,
                candidates=[],
            )

        candidates = [
            SearchCandidateResponse(
                document_id=candidate.document_id,
                score=candidate.raw_score,
                data=(
                    MessageSearchResult.model_validate(
                        candidate.data,
                        from_attributes=True,
                    )
                    if isinstance(candidate.data, MessagesData)
                    else None
                ),
            )
            for candidate in retrieval_result.candidates
        ]

        return ContentTypeResultResponse(
            status=retrieval_result.status,
            error=retrieval_result.error,
            candidates=candidates,
            total=retrieval_result.total,
        )

    def _get_next_search_after(
        self,
        *,
        candidates,
        limit: int,
    ):
        if not candidates:
            return None

        if len(candidates) < limit:
            return None

        last_candidate = candidates[-1]

        if not last_candidate.sort_values:
            return None

        return last_candidate.sort_values

    def _get_next_offset(
        self,
        *,
        candidates,
        limit: int,
        current_offset: int,
    ):
        if not candidates:
            return None

        if len(candidates) < limit:
            return None

        return (current_offset + limit,)

    def _validate_cursor(
        self,
        *,
        cursor: SearchCursor,
        query_hash: str,
        plan: RetrievalPlan,
        request: SearchContextRequest,
    ) -> None:
        if cursor.query_hash != query_hash:
            logger.warning("_validate_cursor :: cursor query_hash mismatch")
            raise ValueError("Search cursor does not match the current query")

        if cursor.sort != request.sort:
            logger.warning("_validate_cursor :: cursor sort mismatch")
            raise ValueError("Search cursor sort does not match the current request")

        if cursor.sort_direction != request.sort_direction:
            logger.warning("_validate_cursor :: cursor sort_direction mismatch")
            raise ValueError(
                "Search cursor sort direction does not match the current request"
            )

        if cursor.pagination_depth != plan.pagination_depth:
            logger.warning("_validate_cursor :: cursor pagination_depth mismatch")
            raise ValueError(
                "Search cursor pagination depth does not match the current request"
            )

    def _build_next_cursor(
        self,
        *,
        request: SearchContextRequest,
        query_hash: str,
        plan: RetrievalPlan,
        search_after: dict,
    ) -> str | None:

        if not search_after:
            return None

        has_more = any(value is not None for value in search_after.values())

        if not has_more:
            return None

        cursor = SearchCursor(
            query_hash=query_hash,
            sort=request.sort,
            sort_direction=request.sort_direction,
            pagination_depth=plan.pagination_depth,
            search_after=search_after,
        )

        return self._cursor_codec.encode(cursor)
