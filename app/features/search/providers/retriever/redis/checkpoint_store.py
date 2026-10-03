import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.features.search.providers.interfaces import CheckpointStore

logger = logging.getLogger(__name__)


class RedisCheckpointStore(CheckpointStore):
    """Persists bulk-backfill progress as a plain Redis hash so a crashed
    or killed run can resume from its last completed batch instead of
    restarting."""

    def __init__(
        self,
        client: Any,
        key_prefix: str = "search:backfill-checkpoint:",
    ):
        self._client = client
        self._key_prefix = key_prefix

    async def get(self, job_name: str) -> Optional[dict]:
        data = await self._client.hgetall(f"{self._key_prefix}{job_name}")

        if not data:
            return None

        return {
            (key.decode() if isinstance(key, bytes) else key): (
                value.decode() if isinstance(value, bytes) else value
            )
            for key, value in data.items()
        }

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
        await self._client.hset(
            f"{self._key_prefix}{job_name}",
            mapping={
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
        )

    async def clear(self, job_name: str) -> None:
        await self._client.delete(f"{self._key_prefix}{job_name}")
