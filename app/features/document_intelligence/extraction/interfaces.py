from abc import ABC, abstractmethod
from typing import BinaryIO

from app.features.document_intelligence.canonical.schemas import SourceFile

from .schemas import (
    ExtractionCapabilities,
    ExtractionOptions,
    ExtractionResult,
)


class DocumentSource(ABC):
    """
    Abstraction over where uploaded files are stored.

    The extraction layer should not know whether files come from
    S3, local storage, another service, or the Gatekeeper API.
    """

    @abstractmethod
    async def get_metadata(self, file_id: str) -> SourceFile:
        """Return metadata for an uploaded file."""
        raise NotImplementedError

    @abstractmethod
    async def download(self, file_id: str) -> BinaryIO:
        """Return the file content as a readable binary stream."""
        raise NotImplementedError


class ExtractionBackend(ABC):
    """
    Contract implemented by every document extraction backend.

    Backends describe their capabilities and perform extraction.
    Routing and backend priority belong to the orchestrator/policy layer.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable backend name used in metadata and observability."""
        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> ExtractionCapabilities:
        """Describe the formats and features supported by this backend."""
        raise NotImplementedError

    @abstractmethod
    def supports(self, source: SourceFile) -> bool:
        """Return whether this backend can attempt the given source."""
        raise NotImplementedError

    @abstractmethod
    async def extract(
        self,
        source: SourceFile,
        content: BinaryIO,
        options: ExtractionOptions,
    ) -> ExtractionResult:
        """Extract a source file into the canonical document model."""
        raise NotImplementedError