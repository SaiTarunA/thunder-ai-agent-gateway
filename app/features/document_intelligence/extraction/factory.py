from __future__ import annotations

from app.features.document_intelligence.extraction.backends.csv_backend import (
    CsvBackend,
)
from app.features.document_intelligence.extraction.backends.docling import (
    DoclingBackend,
)
from app.features.document_intelligence.extraction.backends.json_backend import (
    JsonBackend,
)
from app.features.document_intelligence.extraction.backends.rst_backend import (
    RstBackend,
)
from app.features.document_intelligence.extraction.backends.source_code import (
    SourceCodeBackend,
)
from app.features.document_intelligence.extraction.backends.text import (
    PlainTextBackend,
)
from app.features.document_intelligence.extraction.backends.xlsx_backend import (
    XlsxBackend,
)
from app.features.document_intelligence.extraction.backends.xml_backend import (
    XmlBackend,
)
from app.features.document_intelligence.extraction.backends.yaml_backend import (
    YamlBackend,
)
from app.features.document_intelligence.extraction.pipelines.document import (
    DocumentExtractionPipeline,
)
from app.features.document_intelligence.extraction.pipelines.pdf import (
    PdfExtractionPipeline,
)
from app.features.document_intelligence.extraction.registry import (
    ExtractionBackendRegistry,
)


def build_default_extraction_registry() -> ExtractionBackendRegistry:
    """Build the registry used to route real extraction requests.

    Order matters: ExtractionOrchestrator tries candidates in order and
    stops at the first non-failed result, so pipelines are listed first
    to own the formats they have adaptive quality/fallback logic for.

    DoclingBackend is listed after the two pipelines. It is the only
    candidate for image formats (.png/.jpg/.jpeg/.webp/.bmp), which have
    no dedicated pipeline yet. For PDF/DOCX/MD/HTML it is redundant in
    the common case, since both pipelines already call DoclingBackend
    internally, but it still serves as a last-resort candidate if a
    pipeline raises rather than returning a FAILED result.

    The remaining native-parser backends (Phase 2) each own a single,
    disjoint set of extensions with no other backend competing for them,
    so their position in the list relative to each other doesn't matter.
    """

    return ExtractionBackendRegistry(
        [
            PdfExtractionPipeline(),
            DocumentExtractionPipeline(),
            DoclingBackend(),
            PlainTextBackend(),
            JsonBackend(),
            YamlBackend(),
            CsvBackend(),
            XmlBackend(),
            XlsxBackend(),
            SourceCodeBackend(),
            RstBackend(),
        ]
    )
