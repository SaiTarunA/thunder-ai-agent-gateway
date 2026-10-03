"""
One-off bulk backfill: indexes every message across all sites/streams from
a given point in time onward.

Run as a module:
    python -m app.features.search.indexing.backfill --since 2026-01-01T00:00:00
"""
import argparse
import asyncio
from datetime import datetime

from app.features.search.config import SearchConfig
from app.features.search.indexing.message_indexer import MessageIndexer
from app.features.search.providers.embedder.hugging_face import (
    HuggingFaceEmbeddingProvider,
)
from app.features.search.providers.retriever.factory import (
    build_bulk_writer,
    build_checkpoint_store,
    build_client_provider,
)
from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.db.mysql.connection.db_pool import DBPool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill the search index with every message from a given time onward.",
    )
    parser.add_argument(
        "--since",
        required=True,
        type=datetime.fromisoformat,
        help="ISO 8601 timestamp; every message at or after this time is indexed.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows fetched/embedded/indexed per batch (default: 500).",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help=(
            "Ignore any existing checkpoint for this job and start fresh "
            "from --since, instead of resuming a crashed/killed run."
        ),
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    await DBPool().init_db_pool()

    config = SearchConfig()

    client_provider = build_client_provider(config)

    # Ensures the messages index, the checkpoint store, and (for OpenSearch)
    # the hybrid search pipeline all exist - this script doesn't depend on
    # the API server having been started first.
    await client_provider.initialize()

    embedding_provider = HuggingFaceEmbeddingProvider()

    bulk_indexer = build_bulk_writer(config, client_provider)
    checkpoint_store = build_checkpoint_store(config, client_provider)

    message_indexer = MessageIndexer(
        db_handler=streams_db_handler,
        embedding_provider=embedding_provider,
        bulk_indexer=bulk_indexer,
        embedding_version="bge-small-en-v1.5",
        batch_size=args.batch_size,
    )

    result = await message_indexer.index_since(
        since=args.since,
        checkpoint_store=checkpoint_store,
        resume=not args.restart,
    )

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
