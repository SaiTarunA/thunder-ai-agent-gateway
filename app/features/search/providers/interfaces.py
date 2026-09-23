from abc import ABC, abstractmethod
from typing import Any

from app.features.search.domain.enums import RetrievalMethodType
from app.features.search.domain.models import (
    RetrievalRequest,
    RetrievalResult,
)


class EmbeddingProvider(ABC):
    @abstractmethod
    async def initialize_model(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    async def embed_documents(
        self,
        documents: list[str],
    ) -> list[list[float]]:
        raise NotImplementedError


class Retriever(ABC):
    """
    Abstraction over the search retriever.

    The search module does not know whether retrieval is performed
    using OpenSearch, Elasticsearch, a database, or another backend.

    The retrieval methods in the request determine whether the backend
    performs lexical, semantic, or hybrid retrieval.
    """

    @abstractmethod
    async def retrieve(
        self,
        request: RetrievalRequest,
    ) -> RetrievalResult:
        raise NotImplementedError


class SearchQueryBuilder(ABC):
    """
    Abstraction over search query construction.

    The search module should not know how lexical, semantic, or hybrid
    backend queries are constructed.
    """

    @abstractmethod
    def build(
        self,
        request: RetrievalRequest,
    ) -> Any:
        raise NotImplementedError


class ClientProvider(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class ResponseMapper(ABC):
    @abstractmethod
    def map_response(
        self,
        response: Any,
        retrieval_methods: tuple[RetrievalMethodType, ...],
        latency_ms: int,
    ) -> RetrievalResult:
        raise NotImplementedError