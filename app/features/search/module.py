from app.features.search.config import SearchConfig
from app.features.search.domain.cursor import (
    SearchCursorCodec,
    SearchQueryHasher,
)
from app.features.search.domain.models import SearchContextRequest
from app.features.search.providers.embedder.hugging_face import (
    HuggingFaceEmbeddingProvider,
)
from app.features.search.providers.retriever.opensearch.client import (
    OpenSearchClientProvider,
)
from app.features.search.providers.retriever.opensearch.indexes.resolver import (
    OpenSearchIndexResolver,
)
from app.features.search.providers.retriever.opensearch.query_builder import (
    OpenSearchQueryBuilder,
)
from app.features.search.providers.retriever.opensearch.response_mapper import (
    OpenSearchResponseMapper,
)
from app.features.search.application.authorization_resolver import (
    AuthorizationResolver,
)
from app.features.search.application.filter_resolver import FilterResolver
from app.features.search.application.query_processor import QueryProcessor
from app.features.search.application.retrieval_planner import RetrievalPlanner
from app.features.search.application.service import SearchService
from app.features.search.providers.retriever.opensearch.retriever import OpenSearchRetriever


class SearchModule:

    def __init__(self):
        self._config = SearchConfig()

        self._search_client = OpenSearchClientProvider()
        self._embedding = HuggingFaceEmbeddingProvider()

    async def initialize(self) -> None:
        await self._search_client.initialize()
        await self._embedding.initialize_model()

    async def close(self) -> None:
        await self._search_client.close()

    async def search(
        self,
        request: SearchContextRequest,
    ):
        query_processor = QueryProcessor()
        authorization_resolver = AuthorizationResolver()
        filter_resolver = FilterResolver()
        retrieval_planner = RetrievalPlanner(
            self._config,
        )

        index_resolver = OpenSearchIndexResolver()
        query_builder = OpenSearchQueryBuilder()
        response_mapper = OpenSearchResponseMapper()
        retriever = OpenSearchRetriever(
            client=self._search_client.client,
            query_builder=query_builder,
            response_mapper=response_mapper,
            index_resolver=index_resolver,
            hybrid_search_pipeline=(
                self._config.hybrid_search_pipeline
            ),
        )

        cursor_codec = SearchCursorCodec()
        query_hasher = SearchQueryHasher()

        search_service = SearchService(
            config=self._config,
            query_processor=query_processor,
            authorization_resolver=authorization_resolver,
            filter_resolver=filter_resolver,
            retrieval_planner=retrieval_planner,
            embedding_provider=self._embedding,
            retriever=retriever,
            cursor_codec=cursor_codec,
            query_hasher=query_hasher,
        )

        return await search_service.search(request)

search_module = SearchModule()
