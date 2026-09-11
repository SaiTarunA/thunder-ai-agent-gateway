from __future__ import annotations

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


class DocumentExtractionPipeline(ExtractionBackend):
    """Extraction pipeline for DOCX, Markdown, HTML, LaTeX, PPTX, ODT,
    and EPUB.

    All of these route through Docling's SimplePipeline (verified per
    format before adding it here, e.g. no OCR option, same as
    DOCX/MD/HTML), so they share this one pipeline rather than each
    getting a bespoke one.

    Extraction strategy:

        1. Extract with Docling.
        2. Evaluate extraction quality.
        3. Apply extraction policy.
        4. Return the result, annotated with the policy's reasons
           whenever it falls short of the quality bar.

    Unlike the PDF pipeline, these formats have no separate cheap native
    backend to try first, and Docling's non-PDF pipeline exposes no OCR
    option, since there is no scanned-page layer to recover. The only
    non-picture-semantic "richer extraction" lever it exposes (chart
    extraction) requires a multi-gigabyte vision-language model, so it
    is not something this pipeline can reach for automatically on every
    low-quality result. For now, a FALLBACK decision is only surfaced
    as a warning; there is no richer backend to retry with yet.

    The pipeline owns orchestration only. Extraction, quality evaluation,
    and policy decisions remain delegated to their respective components.

    Implements ExtractionBackend so it can be registered directly with
    ExtractionBackendRegistry/ExtractionOrchestrator alongside raw
    backends.
    """

    _SUPPORTED_EXTENSIONS = {
        ".docx", ".md", ".html", ".htm",
        ".tex", ".pptx", ".odt", ".epub",
    }

    @property
    def name(self) -> str:
        return "document_pipeline"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={
                (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "text/markdown",
                "text/html",
                "application/x-tex",
                (
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                "application/vnd.oasis.opendocument.text",
                "application/epub+zip",
            },
            supports_ocr=False,
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
        docling_backend: DoclingBackend | None = None,
        quality_evaluator: ExtractionQualityEvaluator | None = None,
        policy: ExtractionPolicy | None = None,
    ) -> None:
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
        """Extract a DOCX/Markdown/HTML file with Docling."""

        result = await self._docling.extract(
            source=source,
            content=content,
            options=options,
        )

        # ------------------------------------------------------------
        # Evaluate extraction quality
        # ------------------------------------------------------------

        if (
            result.status == ExtractionStatus.FAILED
            or result.document is None
        ):
            return result

        quality = self._quality_evaluator.evaluate(
            result.document,
        )

        decision = self._policy.evaluate(
            quality,
        )

        # ------------------------------------------------------------
        # Surface a FALLBACK decision as a warning. There is no richer
        # backend to retry with yet, so the result is still returned.
        # ------------------------------------------------------------

        if decision.decision == ExtractionDecision.FALLBACK:
            return result.model_copy(
                update={
                    "warnings": [
                        *result.warnings,
                        *decision.reasons,
                    ],
                },
            )

        return result
