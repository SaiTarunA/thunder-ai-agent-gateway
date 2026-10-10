import os
from typing import Iterable

from dotenv import load_dotenv
from redis.asyncio.sentinel import Sentinel
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.exceptions import ResponseError

from app.features.search.providers.interfaces import ClientProvider
from app.features.search.providers.retriever.redis.indexes.base import (
    RedisIndexDefinition,
)
from app.features.search.providers.retriever.redis.indexes.messages import (
    MessagesIndex,
)

load_dotenv()


class RedisClientProvider(ClientProvider):

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)

            sentinel_hosts = [
                cls._parse_sentinel_host(entry)
                for entry in os.environ.get(
                    "REDIS_SENTINEL_HOSTS",
                    "192.168.0.107:26379",
                ).split(",")
                if entry.strip()
            ]

            auth = cls._build_auth()

            cls._instance._sentinel = Sentinel(
                sentinel_hosts,
                socket_timeout=float(
                    os.environ.get(
                        "REDIS_SOCKET_TIMEOUT_SECONDS",
                        "10",
                    )
                ),
                sentinel_kwargs=auth,
            )

            cls._instance.client = cls._instance._sentinel.master_for(
                os.environ.get(
                    "REDIS_SENTINEL_MASTER_NAME",
                    "mymaster",
                ),
                db=int(os.environ.get("REDIS_DB", "0")),
                **auth,
            )

            cls._instance._initialized = False

        return cls._instance

    @staticmethod
    def _parse_sentinel_host(entry: str) -> tuple[str, int]:
        host, _, port = entry.strip().partition(":")
        return (host, int(port) if port else 26379)

    @staticmethod
    def _build_auth() -> dict:
        auth: dict = {}

        password = os.environ.get("REDIS_PASSWORD")
        user = os.environ.get("REDIS_USER")

        if password:
            auth["password"] = password

        if user:
            auth["username"] = user

        return auth

    async def initialize(self) -> None:
        if self._initialized:
            return

        await self.client.ping()

        indexes: Iterable[RedisIndexDefinition] = [
            MessagesIndex(),
        ]

        for index_definition in indexes:
            await self._ensure_index(index_definition)

        self._initialized = True

    async def _ensure_index(
        self,
        definition: RedisIndexDefinition,
    ) -> None:
        try:
            await self.client.ft(definition.name).create_index(
                definition.fields,
                definition=IndexDefinition(
                    prefix=[definition.key_prefix],
                    index_type=IndexType.HASH,
                ),
            )
        except ResponseError as exc:
            if "Index already exists" not in str(exc):
                raise

    async def close(self) -> None:
        await self.client.aclose()


redis_client_provider = RedisClientProvider()
