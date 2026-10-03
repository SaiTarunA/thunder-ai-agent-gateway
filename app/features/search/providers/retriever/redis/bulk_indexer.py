import logging
import struct
from typing import Any, Sequence

from redis.commands.search.query import Query

from app.features.search.indexing.models import SearchMessage
from app.features.search.providers.interfaces import BulkWriter
from app.features.search.providers.retriever.redis.query_builder import _escape_tag

logger = logging.getLogger(__name__)

_DELETE_BATCH_SIZE = 500


class RedisBulkIndexer(BulkWriter):
    def __init__(
        self,
        client,
        key_prefix: str = "search:messages:",
        index_name: str = "streams-messages-v1",
    ):
        self.client = client
        self.key_prefix = key_prefix
        self.index_name = index_name

    async def bulk_index(
        self,
        messages: Sequence[SearchMessage],
        chunk_size: int = 500,
    ) -> tuple[int, int]:
        if not messages:
            return 0, 0

        success_count = 0
        failure_count = 0

        for start in range(0, len(messages), chunk_size):
            chunk = messages[start : start + chunk_size]

            pipeline = self.client.pipeline(transaction=False)

            for message in chunk:
                key = f"{self.key_prefix}{message.message_id}"
                pipeline.hset(key, mapping=self._to_document(message))

            try:
                results = await pipeline.execute()
                success_count += len(results)
            except Exception as exc:
                failure_count += len(chunk)
                logger.error(
                    "Bulk indexing chunk failed: %s",
                    exc,
                )

        if failure_count:
            logger.error(
                "Bulk indexing completed with failures: success=%s failures=%s",
                success_count,
                failure_count,
            )

        return success_count, failure_count

    async def delete(self, message_id: Any) -> bool:
        key = f"{self.key_prefix}{message_id}"
        deleted = await self.client.delete(key)
        return bool(deleted)

    async def delete_by_sid(self, site_id: int, sid: int) -> int:
        filter_expr = (
            f"@site_id:{{{_escape_tag(site_id)}}} @sid:{{{_escape_tag(sid)}}}"
        )

        ft = self.client.ft(self.index_name)
        deleted = 0

        while True:
            query = (
                Query(filter_expr)
                .paging(0, _DELETE_BATCH_SIZE)
                .no_content()
                .dialect(2)
            )

            result = await ft.search(query)

            if not result.docs:
                break

            keys = [doc.id for doc in result.docs]
            await self.client.delete(*keys)
            deleted += len(keys)

            if len(keys) < _DELETE_BATCH_SIZE:
                break

        return deleted

    @staticmethod
    def _to_document(message: SearchMessage) -> dict:
        document = {
            "site_id": str(message.site_id),
            "sid": str(message.sid),
            "channel_type": message.channel_type or "",
            "message_id": message.message_id,
            "message_type": message.message_type,
            "text": message.text,
            "author_archive_id": str(message.author_archive_id),
            "created_at": message.created_at.timestamp(),
            "updated_at": message.updated_at.timestamp(),
            "thread_root_id": str(message.thread_root_id),
            "parent_message_id": (
                str(message.parent_message_id)
                if message.parent_message_id is not None
                else ""
            ),
            "is_thread_reply": "true" if message.is_thread_reply else "false",
            "is_deleted": "true" if message.is_deleted else "false",
            "is_edited": "true" if message.is_edited else "false",
            "embedding_version": message.embedding_version or "",
        }

        if message.embedding is not None:
            document["embedding"] = struct.pack(
                f"{len(message.embedding)}f",
                *message.embedding,
            )

        return document
