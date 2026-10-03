from app.features.search.config import SearchConfig
from app.features.search.domain.cursor import (
    SearchCursorCodec,
    SearchQueryHasher,
)
from app.features.search.domain.models import SearchContextRequest
from app.features.search.providers.embedder.hugging_face import (
    HuggingFaceEmbeddingProvider,
)
from app.features.search.providers.retriever.factory import (
    build_bulk_writer,
    build_client_provider,
    build_retriever,
)
from app.features.search.application.authorization_resolver import (
    AuthorizationResolver,
)
from app.features.search.application.filter_resolver import FilterResolver
from app.features.search.application.query_processor import QueryProcessor
from app.features.search.application.retrieval_planner import RetrievalPlanner
from app.features.search.application.service import SearchService
from app.features.search.indexing.message_indexer import MessageIndexer
from app.features.search.providers.interfaces import BulkWriter
from app.db.mysql.repositories.streams_repo import streams_db_handler


class SearchModule:

    def __init__(self):
        self._config = SearchConfig()

        self._client_provider = build_client_provider(self._config)
        self._embedding = HuggingFaceEmbeddingProvider()

    async def initialize(self) -> None:
        await self._client_provider.initialize()
        await self._embedding.initialize_model()

    async def close(self) -> None:
        await self._client_provider.close()

    def get_bulk_writer(self) -> BulkWriter:
        """Construct a BulkWriter bound to the already-initialized client -
        cheap, used per indexing event rather than cached, since the only
        expensive singleton it wraps (the client connection) is reused."""
        return build_bulk_writer(self._config, self._client_provider)

    def get_message_indexer(self) -> MessageIndexer:
        """Construct a MessageIndexer bound to the shared, already-loaded
        embedding provider - must reuse self._embedding rather than
        constructing a fresh HuggingFaceEmbeddingProvider, or every
        indexing event would try to (re)load the model."""
        return MessageIndexer(
            db_handler=streams_db_handler,
            embedding_provider=self._embedding,
            bulk_indexer=self.get_bulk_writer(),
        )

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

        retriever = build_retriever(self._config, self._client_provider)

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
