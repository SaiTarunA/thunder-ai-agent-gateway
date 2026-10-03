from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Sequence

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


class BulkWriter(ABC):
    """
    Abstraction over the search index write path.

    The indexing pipeline does not know whether documents are persisted
    to OpenSearch, Redis, or another backend.
    """

    @abstractmethod
    async def bulk_index(
        self,
        messages: Sequence[Any],
        chunk_size: int = 500,
    ) -> tuple[int, int]:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, message_id: Any) -> bool:
        """Remove a single document by its message id. Returns True if a
        document was found and removed, False if it was already absent."""
        raise NotImplementedError

    @abstractmethod
    async def delete_by_sid(self, site_id: int, sid: int) -> int:
        """Remove every document for the given stream. Returns the number
        of documents deleted."""
        raise NotImplementedError


class CheckpointStore(ABC):
    """Abstraction over backfill checkpoint persistence."""

    @abstractmethod
    async def get(self, job_name: str) -> dict | None:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    async def clear(self, job_name: str) -> None:
        raise NotImplementedError
