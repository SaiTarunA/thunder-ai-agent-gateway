from app.features.document_intelligence.extraction.interfaces import ExtractionBackend
from app.features.document_intelligence.canonical.schemas import SourceFile


class ExtractionBackendRegistry:
    """
    Holds available extraction backends and discovers candidates
    for a given source file.
    """

    def __init__(self, backends: list[ExtractionBackend]):
        self._backends = backends

    def get_candidates(
        self,
        source: SourceFile,
    ) -> list[ExtractionBackend]:
        return [
            backend
            for backend in self._backends
            if backend.supports(source)
        ]