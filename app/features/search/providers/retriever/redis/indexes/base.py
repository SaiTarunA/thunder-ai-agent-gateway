from abc import ABC, abstractmethod

from redis.commands.search.field import Field


class RedisIndexDefinition(ABC):
    """
    Defines everything required to provision a RediSearch index.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def key_prefix(self) -> str:
        pass

    @property
    @abstractmethod
    def fields(self) -> list[Field]:
        pass
