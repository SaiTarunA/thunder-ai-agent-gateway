import os
from typing import Iterable

from dotenv import load_dotenv
from opensearchpy import AsyncOpenSearch
from opensearchpy.exceptions import RequestError

from app.features.search.providers.interfaces import ClientProvider
from app.features.search.providers.retriever.opensearch.indexes.base import (
    OpenSearchIndexDefinition,
)
from app.features.search.providers.retriever.opensearch.indexes.hybrid_pipeline import (
    HybridSearchPipeline,
)
from app.features.search.providers.retriever.opensearch.indexes.messages import (
    MessagesIndex,
)
from app.features.search.providers.retriever.opensearch.indexes.search_pipeline import (
    OpenSearchSearchPipelineDefinition,
)

load_dotenv()


class OpenSearchClientProvider(ClientProvider):

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)

            cls._instance.client = AsyncOpenSearch(
                hosts=[
                    {
                        "host": os.environ.get(
                            "OPENSEARCH_HOST",
                            "localhost",
                        ),
                        "port": int(
                            os.environ.get(
                                "OPENSEARCH_PORT",
                                "9200",
                            )
                        ),
                    }
                ],
                http_auth=cls._build_auth(),
                use_ssl=(
                    os.environ.get(
                        "OPENSEARCH_USE_SSL",
                        "true",
                    ).lower()
                    == "true"
                ),
                verify_certs=(
                    os.environ.get(
                        "OPENSEARCH_VERIFY_CERTS",
                        "false",
                    ).lower()
                    == "true"
                ),
                timeout=int(
                    os.environ.get(
                        "OPENSEARCH_TIMEOUT_SECONDS",
                        "10",
                    )
                ),
            )

            cls._instance._initialized = False

        return cls._instance

    @staticmethod
    def _build_auth():
        user = os.environ.get(
            "OPENSEARCH_USER",
            "admin",
        )

        password = os.environ.get("OPENSEARCH_PASSWORD")

        if user and password:
            return (user, password)

        return None

    async def initialize(self) -> None:
        if self._initialized:
            return

        await self.client.cluster.health()

        indexes: Iterable[OpenSearchIndexDefinition] = [
            MessagesIndex(),
        ]

        for index_definition in indexes:
            await self._ensure_index(index_definition)

        pipelines: Iterable[OpenSearchSearchPipelineDefinition] = [
            HybridSearchPipeline(),
        ]

        for pipeline_definition in pipelines:
            await self._ensure_search_pipeline(pipeline_definition)

        self._initialized = True

    async def _ensure_index(
        self,
        definition: OpenSearchIndexDefinition,
    ) -> None:
        index_name = definition.name

        if await self.client.indices.exists(index=index_name):
            if definition.alias:
                await self._ensure_alias(
                    index_name=index_name,
                    alias=definition.alias,
                )
            return

        try:
            await self.client.indices.create(
                index=index_name,
                body={
                    "settings": definition.settings,
                    "mappings": definition.mappings,
                },
            )
        except RequestError as exc:
            if exc.error != "resource_already_exists_exception":
                raise

        if definition.alias:
            await self._ensure_alias(
                index_name=index_name,
                alias=definition.alias,
            )

    async def _ensure_alias(
        self,
        *,
        index_name: str,
        alias: str,
    ) -> None:
        exists = await self.client.indices.exists_alias(name=alias)

        if exists:
            return

        await self.client.indices.put_alias(
            index=index_name,
            name=alias,
        )

    async def _ensure_search_pipeline(
        self,
        definition: OpenSearchSearchPipelineDefinition,
    ) -> None:
        pipeline_name = definition.name

        await self.client.transport.perform_request(
            "PUT",
            f"/_search/pipeline/{pipeline_name}",
            body=definition.definition,
        )

    async def close(self) -> None:
        await self.client.close()


opensearch_client_provider = OpenSearchClientProvider()