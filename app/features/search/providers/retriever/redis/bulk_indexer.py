import logging
import struct
from typing import Sequence

from app.features.search.indexing.models import SearchMessage
from app.features.search.providers.interfaces import BulkWriter

logger = logging.getLogger(__name__)


class RedisBulkIndexer(BulkWriter):
    def __init__(
        self,
        client,
        key_prefix: str = "search:messages:",
    ):
        self.client = client
        self.key_prefix = key_prefix

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
