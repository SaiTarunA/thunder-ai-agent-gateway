from app.features.search.config import SearchConfig
from app.features.search.providers.interfaces import (
    BulkWriter,
    CheckpointStore,
    ClientProvider,
    Retriever,
)

_SUPPORTED_BACKENDS = ("redis", "opensearch")


def _require_supported(config: SearchConfig) -> None:
    if config.retriever_backend not in _SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unsupported retriever backend: {config.retriever_backend!r}. "
            f"Expected one of {_SUPPORTED_BACKENDS}."
        )


def build_client_provider(config: SearchConfig) -> ClientProvider:
    """
    Returns the singleton ClientProvider for the configured backend.

    This is the only place that decides which search backend is active -
    flipping `config.retriever_backend` (env var RETRIEVER_BACKEND) is
    enough to switch between Redis and OpenSearch.
    """
    _require_supported(config)

    if config.retriever_backend == "redis":
        from app.features.search.providers.retriever.redis.client import (
            redis_client_provider,
        )

        return redis_client_provider

    from app.features.search.providers.retriever.opensearch.client import (
        opensearch_client_provider,
    )

    return opensearch_client_provider


def build_retriever(
    config: SearchConfig,
    client_provider: ClientProvider,
) -> Retriever:
    _require_supported(config)

    if config.retriever_backend == "redis":
        from app.features.search.providers.retriever.redis.indexes.resolver import (
            RedisIndexResolver,
        )
        from app.features.search.providers.retriever.redis.query_builder import (
            RedisQueryBuilder,
        )
        from app.features.search.providers.retriever.redis.response_mapper import (
            RedisResponseMapper,
        )
        from app.features.search.providers.retriever.redis.retriever import (
            RedisRetriever,
        )

        return RedisRetriever(
            client=client_provider.client,
            query_builder=RedisQueryBuilder(rrf_constant=config.hybrid_rrf_k),
            response_mapper=RedisResponseMapper(),
            index_resolver=RedisIndexResolver(),
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
    from app.features.search.providers.retriever.opensearch.retriever import (
        OpenSearchRetriever,
    )

    return OpenSearchRetriever(
        client=client_provider.client,
        query_builder=OpenSearchQueryBuilder(),
        response_mapper=OpenSearchResponseMapper(),
        index_resolver=OpenSearchIndexResolver(),
        hybrid_search_pipeline=config.hybrid_search_pipeline,
    )


def build_bulk_writer(
    config: SearchConfig,
    client_provider: ClientProvider,
) -> BulkWriter:
    _require_supported(config)

    if config.retriever_backend == "redis":
        from app.features.search.providers.retriever.redis.bulk_indexer import (
            RedisBulkIndexer,
        )

        return RedisBulkIndexer(client=client_provider.client)

    from app.features.search.providers.retriever.opensearch.bulk_indexer import (
        OpenSearchBulkIndexer,
    )

    return OpenSearchBulkIndexer(client=client_provider.client)


def build_checkpoint_store(
    config: SearchConfig,
    client_provider: ClientProvider,
) -> CheckpointStore:
    _require_supported(config)

    if config.retriever_backend == "redis":
        from app.features.search.providers.retriever.redis.checkpoint_store import (
            RedisCheckpointStore,
        )

        return RedisCheckpointStore(client=client_provider.client)

    from app.features.search.providers.retriever.opensearch.checkpoint_store import (
        OpenSearchCheckpointStore,
    )

    return OpenSearchCheckpointStore(client=client_provider.client)
