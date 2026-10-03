import logging
from datetime import datetime
from typing import Any, Optional, Sequence

from app.features.search.indexing.message_mapper import map_message_row
from app.features.search.providers.interfaces import BulkWriter, CheckpointStore

logger = logging.getLogger(__name__)


class MessageIndexer:
    def __init__(
        self,
        db_handler,
        embedding_provider,
        bulk_indexer: BulkWriter,
        embedding_version: str = "bge-small-en-v1.5",
        batch_size: int = 500,
    ):
        self.db_handler = db_handler
        self.embedding_provider = embedding_provider
        self.bulk_indexer = bulk_indexer
        self.embedding_version = embedding_version
        self.batch_size = batch_size

    async def index_messages(
        self,
        site_id: int,
        sids: Sequence[int],
    ) -> dict[str, int]:
        if not sids:
            return {
                "fetched": 0,
                "indexed": 0,
                "failed": 0,
            }

        unique_sids = list(dict.fromkeys(sids))

        rows = await self.db_handler.get_messages_for_indexing(
            site_id=site_id,
            sids=unique_sids,
        )

        if rows is None:
            raise RuntimeError(
                "Database returned None while fetching messages. "
                "Check whether the database connection pool is initialized."
            )

        total_indexed = 0
        total_failed = 0

        for start in range(0, len(rows), self.batch_size):
            batch_rows = rows[start : start + self.batch_size]

            indexed, failed = await self._embed_and_index_batch(batch_rows)

            total_indexed += indexed
            total_failed += failed

        result = {
            "fetched": len(rows),
            "indexed": total_indexed,
            "failed": total_failed,
        }

        logger.info(
            "Message indexing completed: site_id=%s sids=%s result=%s",
            site_id,
            len(unique_sids),
            result,
        )

        return result

    async def index_message(
        self,
        site_id: int,
        sid: int,
        message_id: int,
    ) -> dict[str, int]:
        """Re-fetch a single message by id and upsert it. Used by the
        live create/edit indexing event - always reads the current DB row
        rather than trusting the caller's payload, so out-of-order delivery
        across worker processes still converges on the right state.

        No-ops (fetched=0) if the row is missing, not an indexable msgtype,
        or already soft-deleted - the caller doesn't need to pre-filter.
        """
        row = await self.db_handler.get_message_by_id(
            site_id=site_id,
            sid=sid,
            message_id=message_id,
        )

        if row is None:
            logger.info(
                "index_message :: no indexable row for site_id=%s sid=%s "
                "message_id=%s (missing, wrong msgtype, or deleted)",
                site_id,
                sid,
                message_id,
            )
            return {"fetched": 0, "indexed": 0, "failed": 0}

        indexed, failed = await self._embed_and_index_batch([row])

        return {"fetched": 1, "indexed": indexed, "failed": failed}

    async def index_since(
        self,
        since: datetime,
        *,
        checkpoint_store: Optional[CheckpointStore] = None,
        job_name: str = "messages-backfill",
        resume: bool = True,
    ) -> dict[str, Any]:
        """Backfill every message across all sites/streams from `since`
        onward, paginating the database via keyset (messagetime, smsgid)
        rather than OFFSET so it stays memory-bounded and stable on a table
        this large regardless of how far back `since` reaches.

        When `checkpoint_store` is given, progress is persisted after every
        batch so a crashed or killed run can resume from its last completed
        batch (`resume=True`, the default) instead of starting over. Pass
        `resume=False` to ignore any existing checkpoint and start fresh
        from `since` - the next successful batch overwrites it.
        """

        job_since = since
        after_messagetime = since
        after_smsgid = 0
        total_fetched = 0
        total_indexed = 0
        total_failed = 0

        if checkpoint_store is not None and resume:
            checkpoint = await checkpoint_store.get(job_name)

            if checkpoint is not None:
                job_since = datetime.fromisoformat(checkpoint["since"])
                after_messagetime = datetime.fromisoformat(
                    checkpoint["after_messagetime"]
                )
                after_smsgid = int(checkpoint["after_smsgid"])
                total_fetched = int(checkpoint["total_fetched"])
                total_indexed = int(checkpoint["total_indexed"])
                total_failed = int(checkpoint["total_failed"])

                if job_since != since:
                    logger.warning(
                        "index_since :: job_name=%s has an existing "
                        "checkpoint started with since=%s; ignoring the "
                        "since=%s passed to this run and resuming from "
                        "cursor=(%s, %s)",
                        job_name,
                        job_since,
                        since,
                        after_messagetime,
                        after_smsgid,
                    )
                else:
                    logger.info(
                        "index_since :: resuming job_name=%s from "
                        "cursor=(%s, %s), already fetched=%s indexed=%s "
                        "failed=%s",
                        job_name,
                        after_messagetime,
                        after_smsgid,
                        total_fetched,
                        total_indexed,
                        total_failed,
                    )

        while True:
            rows = await self.db_handler.get_messages_for_bulk_indexing(
                after_messagetime=after_messagetime,
                after_smsgid=after_smsgid,
                batch_size=self.batch_size,
            )

            if not rows:
                break

            indexed, failed = await self._embed_and_index_batch(rows)

            total_fetched += len(rows)
            total_indexed += indexed
            total_failed += failed

            last_row = rows[-1]
            after_messagetime = last_row["messagetime"]
            after_smsgid = int(last_row["smsgid"])

            logger.info(
                "Bulk indexing progress: fetched=%s indexed=%s failed=%s "
                "cursor=(%s, %s)",
                total_fetched,
                total_indexed,
                total_failed,
                after_messagetime,
                after_smsgid,
            )

            if checkpoint_store is not None:
                await checkpoint_store.save(
                    job_name,
                    since=job_since,
                    after_messagetime=after_messagetime,
                    after_smsgid=after_smsgid,
                    total_fetched=total_fetched,
                    total_indexed=total_indexed,
                    total_failed=total_failed,
                )

            if len(rows) < self.batch_size:
                break

        result = {
            "fetched": total_fetched,
            "indexed": total_indexed,
            "failed": total_failed,
            "last_messagetime": after_messagetime,
            "last_smsgid": after_smsgid,
        }

        logger.info(
            "Bulk indexing completed: since=%s result=%s",
            job_since,
            result,
        )

        if checkpoint_store is not None:
            await checkpoint_store.clear(job_name)

        return result

    async def _embed_and_index_batch(
        self,
        rows: Sequence[dict],
    ) -> tuple[int, int]:
        messages = [
            map_message_row(row)
            for row in rows
        ]

        texts = [message.text for message in messages]

        embeddings = await self.embedding_provider.embed_documents(
            texts,
            batch_size=self.batch_size,
        )

        if len(embeddings) != len(messages):
            raise RuntimeError(
                "Embedding count does not match message count"
            )

        for message, embedding in zip(messages, embeddings):
            message.embedding = embedding
            message.embedding_version = self.embedding_version

        return await self.bulk_indexer.bulk_index(
            messages,
            chunk_size=self.batch_size,
        )