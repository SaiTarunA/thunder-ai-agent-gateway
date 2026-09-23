from abc import ABC, abstractmethod


class OpenSearchSearchPipelineDefinition(ABC):
    """
    Defines everything required to provision an OpenSearch search pipeline.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def definition(self) -> dict:
        pass