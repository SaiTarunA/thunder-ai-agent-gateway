from __future__ import annotations

from io import BytesIO

import pytest

from app.features.document_intelligence.canonical.enums import (
    ExtractionDecision,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    CanonicalDocument,
    ExtractionMetadata,
    SourceFile,
)
from app.features.document_intelligence.extraction.pipelines.pdf import (
    PdfExtractionPipeline,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
    ExtractionPolicyResult,
    ExtractionResult,
    ExtractionQuality,
)


class FakeBackend:
    """Simple fake extraction backend."""

    def __init__(
        self,
        result: ExtractionResult,
    ) -> None:
        self.result = result
        self.calls = 0
        self.received_content: bytes | None = None

    async def extract(
        self,
        *,
        source,
        content,
        options,
    ) -> ExtractionResult:
        self.calls += 1

        self.received_content = content.read()

        return self.result


class FakeQualityEvaluator:
    """Returns a predetermined quality result."""

    def __init__(
        self,
        quality: ExtractionQuality,
    ) -> None:
        self.quality = quality
        self.calls = 0

    def evaluate(
        self,
        document,
    ) -> ExtractionQuality:
        self.calls += 1

        return self.quality


class FakePolicy:
    """Returns a predetermined extraction decision."""

    def __init__(
        self,
        result: ExtractionPolicyResult,
    ) -> None:
        self.result = result
        self.calls = 0

    def evaluate(
        self,
        quality,
    ) -> ExtractionPolicyResult:
        self.calls += 1

        return self.result


def make_source() -> SourceFile:
    return SourceFile(
        file_id="test-file",
        filename="example.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=100,
        content_hash="test-hash",
    )


def make_document(
    source: SourceFile,
    *,
    backend: str = "test",
) -> CanonicalDocument:
    return CanonicalDocument(
        id="document:test-hash",
        source=source,
        extraction=ExtractionMetadata(
            backend=backend,
            status=ExtractionStatus.SUCCESS,
        ),
    )


def make_quality() -> ExtractionQuality:
    return ExtractionQuality(
        overall_score=0.90,
        text_score=0.90,
        structure_score=0.80,
        table_score=0.0,
        provenance_score=1.0,
        warnings=[],
    )


@pytest.mark.anyio
async def test_pipeline_accepts_native_result():
    source = make_source()

    document = make_document(
        source,
        backend="pymupdf",
    )

    native_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=document,
    )

    fallback_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=make_document(
            source,
            backend="docling",
        ),
    )

    pymupdf_backend = FakeBackend(native_result)
    docling_backend = FakeBackend(fallback_result)

    quality_evaluator = FakeQualityEvaluator(
        make_quality(),
    )

    policy = FakePolicy(
        ExtractionPolicyResult(
            decision=ExtractionDecision.ACCEPT,
            reasons=[
                "Extraction quality is sufficient.",
            ],
        )
    )

    pipeline = PdfExtractionPipeline(
        pymupdf_backend=pymupdf_backend,
        docling_backend=docling_backend,
        quality_evaluator=quality_evaluator,
        policy=policy,
    )

    result = await pipeline.extract(
        source=source,
        content=BytesIO(b"fake pdf content"),
        options=ExtractionOptions(),
    )

    assert result is native_result

    assert result.document is not None
    assert result.document.extraction.backend == "pymupdf"

    assert pymupdf_backend.calls == 1
    assert docling_backend.calls == 0

    assert quality_evaluator.calls == 1
    assert policy.calls == 1


@pytest.mark.anyio
async def test_pipeline_falls_back_when_policy_rejects_native_result():
    source = make_source()

    native_document = make_document(source, backend="pymupdf")
    fallback_document = make_document(source, backend="docling")

    native_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=native_document,
    )

    fallback_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=fallback_document,
    )

    pymupdf_backend = FakeBackend(
        native_result,
    )

    docling_backend = FakeBackend(
        fallback_result,
    )

    quality_evaluator = FakeQualityEvaluator(
        ExtractionQuality(
            overall_score=0.40,
            text_score=0.40,
            structure_score=0.50,
            table_score=0.0,
            provenance_score=1.0,
            warnings=[
                "Text extraction quality is low.",
            ],
        )
    )

    policy = FakePolicy(
        ExtractionPolicyResult(
            decision=ExtractionDecision.FALLBACK,
            reasons=[
                "Text extraction quality is insufficient.",
            ],
        )
    )

    pipeline = PdfExtractionPipeline(
        pymupdf_backend=pymupdf_backend,
        docling_backend=docling_backend,
        quality_evaluator=quality_evaluator,
        policy=policy,
    )

    result = await pipeline.extract(
        source=source,
        content=BytesIO(b"fake pdf content"),
        options=ExtractionOptions(),
    )

    assert result is fallback_result

    assert result.document is not None
    assert result.document.extraction.backend == "docling"

    assert pymupdf_backend.calls == 1
    assert docling_backend.calls == 1

    assert quality_evaluator.calls == 1
    assert policy.calls == 1


@pytest.mark.anyio
async def test_pipeline_falls_back_when_native_extraction_fails():
    source = make_source()

    native_result = ExtractionResult(
        status=ExtractionStatus.FAILED,
        document=None,
    )

    fallback_document = make_document(source)

    fallback_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=fallback_document,
    )

    pymupdf_backend = FakeBackend(
        native_result,
    )

    docling_backend = FakeBackend(
        fallback_result,
    )

    quality_evaluator = FakeQualityEvaluator(
        make_quality(),
    )

    policy = FakePolicy(
        ExtractionPolicyResult(
            decision=ExtractionDecision.ACCEPT,
            reasons=["Unused."],
        )
    )

    pipeline = PdfExtractionPipeline(
        pymupdf_backend=pymupdf_backend,
        docling_backend=docling_backend,
        quality_evaluator=quality_evaluator,
        policy=policy,
    )

    result = await pipeline.extract(
        source=source,
        content=BytesIO(b"fake pdf content"),
        options=ExtractionOptions(),
    )

    assert result is fallback_result

    assert pymupdf_backend.calls == 1
    assert docling_backend.calls == 1

    # Quality and policy should not run because
    # native extraction did not produce a usable document.
    assert quality_evaluator.calls == 0
    assert policy.calls == 0

    assert pymupdf_backend.received_content == b"fake pdf content"
    assert docling_backend.received_content == b"fake pdf content"