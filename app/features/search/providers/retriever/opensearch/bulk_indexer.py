import logging
from typing import Any, Sequence

from opensearchpy.exceptions import NotFoundError
from opensearchpy.helpers import async_bulk

from app.features.search.indexing.models import SearchMessage
from app.features.search.providers.interfaces import BulkWriter

logger = logging.getLogger(__name__)


class OpenSearchBulkIndexer(BulkWriter):
    def __init__(
        self,
        client,
        index_alias: str = "streams-messages",
    ):
        self.client = client
        self.index_alias = index_alias

    async def bulk_index(
        self,
        messages: Sequence[SearchMessage],
        chunk_size: int = 500,
    ) -> tuple[int, int]:
        if not messages:
            return 0, 0

        actions = [
            {
                "_op_type": "index",
                "_index": self.index_alias,
                "_id": str(message.message_id),
                "_source": self._to_document(message),
            }
            for message in messages
        ]

        success_count, errors = await async_bulk(
            self.client,
            actions,
            chunk_size=chunk_size,
            raise_on_error=False,
            raise_on_exception=False,
            stats_only=False,
        )

        failure_count = len(errors)

        if failure_count:
            logger.error(
                "Bulk indexing completed with failures: success=%s failures=%s",
                success_count,
                failure_count,
            )

        return success_count, failure_count

    async def delete(self, message_id: Any) -> bool:
        try:
            await self.client.delete(
                index=self.index_alias,
                id=str(message_id),
            )
            return True
        except NotFoundError:
            return False

    async def delete_by_sid(self, site_id: int, sid: int) -> int:
        response = await self.client.delete_by_query(
            index=self.index_alias,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"site_id": str(site_id)}},
                            {"term": {"sid": sid}},
                        ]
                    }
                }
            },
        )
        return response.get("deleted", 0)

    @staticmethod
    def _to_document(message: SearchMessage) -> dict:
        return {
            "site_id": str(message.site_id),
            "sid": message.sid,
            "channel_type": message.channel_type,
            "message_id": message.message_id,
            "parent_message_id": message.parent_message_id,
            "thread_root_id": message.thread_root_id,
            "text": message.text,
            "message_type": message.message_type,
            "author_archive_id": message.author_archive_id,
            "created_at": message.created_at.isoformat(),
            "updated_at": message.updated_at.isoformat(),
            "is_thread_reply": message.is_thread_reply,
            "is_deleted": message.is_deleted,
            "is_edited": message.is_edited,
            "embedding": message.embedding,
            "embedding_version": message.embedding_version,
        }
