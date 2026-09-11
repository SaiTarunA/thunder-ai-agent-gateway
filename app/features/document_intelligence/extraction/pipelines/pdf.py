from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from app.features.document_intelligence.canonical.enums import (
    ExtractionDecision,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    SourceFile,
)
from app.features.document_intelligence.extraction.backends.docling import (
    DoclingBackend,
)
from app.features.document_intelligence.extraction.backends.pymupdf import (
    PyMuPDFBackend,
)
from app.features.document_intelligence.extraction.interfaces import (
    ExtractionBackend,
)
from app.features.document_intelligence.extraction.policy import (
    ExtractionPolicy,
)
from app.features.document_intelligence.extraction.quality import (
    ExtractionQualityEvaluator,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionOptions,
    ExtractionResult,
)


class PdfExtractionPipeline(ExtractionBackend):
    """Adaptive PDF extraction pipeline.

    Extraction strategy:

        1. Try PyMuPDF native extraction.
        2. Evaluate extraction quality.
        3. Apply extraction policy.
        4. Return native result if accepted.
        5. Otherwise fall back to Docling.

    The pipeline owns orchestration only. Extraction, quality evaluation,
    and policy decisions remain delegated to their respective components.

    Implements ExtractionBackend so it can be registered directly with
    ExtractionBackendRegistry/ExtractionOrchestrator alongside raw
    backends.
    """

    _SUPPORTED_EXTENSIONS = {".pdf"}

    @property
    def name(self) -> str:
        return "pdf_pipeline"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"application/pdf"},
            supports_ocr=True,
            supports_tables=True,
            supports_images=True,
            supports_layout=True,
            supports_provenance=True,
        )

    def supports(self, source: SourceFile) -> bool:
        return (
            source.extension.lower()
            in self._SUPPORTED_EXTENSIONS
        )

    def __init__(
        self,
        *,
        pymupdf_backend: PyMuPDFBackend | None = None,
        docling_backend: DoclingBackend | None = None,
        quality_evaluator: ExtractionQualityEvaluator | None = None,
        policy: ExtractionPolicy | None = None,
    ) -> None:
        self._pymupdf = (
            pymupdf_backend
            if pymupdf_backend is not None
            else PyMuPDFBackend()
        )

        self._docling = (
            docling_backend
            if docling_backend is not None
            else DoclingBackend()
        )

        self._quality_evaluator = (
            quality_evaluator
            if quality_evaluator is not None
            else ExtractionQualityEvaluator()
        )

        self._policy = (
            policy
            if policy is not None
            else ExtractionPolicy()
        )

    async def extract(
        self,
        *,
        source: SourceFile,
        content: BinaryIO,
        options: ExtractionOptions,
    ) -> ExtractionResult:
        """Extract a PDF using an adaptive backend strategy."""

        # Read once so each backend receives a fresh stream.
        content_bytes = content.read()

        # ------------------------------------------------------------
        # Step 1: Try native PyMuPDF extraction
        # ------------------------------------------------------------

        native_result = await self._pymupdf.extract(
            source=source,
            content=BytesIO(content_bytes),
            options=options,
        )

        # ------------------------------------------------------------
        # Step 2: Evaluate successful native extraction
        # ------------------------------------------------------------

        if (
            native_result.status != ExtractionStatus.FAILED
            and native_result.document is not None
        ):
            quality = self._quality_evaluator.evaluate(
                native_result.document,
            )

            decision = self._policy.evaluate(
                quality,
            )

            # --------------------------------------------------------
            # Step 3: Accept native result
            # --------------------------------------------------------

            if decision.decision == ExtractionDecision.ACCEPT:
                return native_result

        # ------------------------------------------------------------
        # Step 4: Fall back to Docling
        # ------------------------------------------------------------

        return await self._docling.extract(
            source=source,
            content=BytesIO(content_bytes),
            options=options,
        )