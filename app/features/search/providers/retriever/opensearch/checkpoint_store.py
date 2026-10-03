import logging
from datetime import datetime, timezone
from typing import Any, Optional

from opensearchpy.exceptions import NotFoundError

from app.features.search.providers.interfaces import CheckpointStore

logger = logging.getLogger(__name__)


class OpenSearchCheckpointStore(CheckpointStore):
    """Persists bulk-backfill progress in OpenSearch so a crashed or killed
    run can resume from its last completed batch instead of restarting."""

    def __init__(
        self,
        client: Any,
        index: str = "streams-search-backfill-checkpoints",
    ):
        self._client = client
        self._index = index

    async def get(self, job_name: str) -> Optional[dict]:
        try:
            response = await self._client.get(
                index=self._index,
                id=job_name,
            )
        except NotFoundError:
            return None

        return response["_source"]

    async def save(
        self,
        job_name: str,
        *,
        since: datetime,
        after_messagetime: datetime,
        after_smsgid: int,
        total_fetched: int,
        total_indexed: int,
        total_failed: int,
        status: str = "in_progress",
    ) -> None:
        await self._client.index(
            index=self._index,
            id=job_name,
            body={
                "job_name": job_name,
                "since": since.isoformat(),
                "after_messagetime": after_messagetime.isoformat(),
                "after_smsgid": after_smsgid,
                "total_fetched": total_fetched,
                "total_indexed": total_indexed,
                "total_failed": total_failed,
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            refresh=True,
        )

    async def clear(self, job_name: str) -> None:
        try:
            await self._client.delete(
                index=self._index,
                id=job_name,
            )
        except NotFoundError:
            pass
