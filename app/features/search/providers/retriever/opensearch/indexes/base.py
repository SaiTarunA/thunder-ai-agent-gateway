from abc import ABC, abstractmethod


class OpenSearchIndexDefinition(ABC):
    """
    Defines everything required to provision an OpenSearch index.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def alias(self) -> str | None:
        return None

    @property
    @abstractmethod
    def settings(self) -> dict:
        pass

    @property
    @abstractmethod
    def mappings(self) -> dict:
        pass
