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
from app.features.document_intelligence.extraction.pipelines.document import (
    DocumentExtractionPipeline,
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

    async def extract(
        self,
        *,
        source,
        content,
        options,
    ) -> ExtractionResult:
        self.calls += 1

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
        filename="example.docx",
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        extension=".docx",
        size_bytes=100,
        content_hash="test-hash",
    )


def make_document(
    source: SourceFile,
    *,
    backend: str = "docling",
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
async def test_pipeline_returns_result_unchanged_when_accepted():
    source = make_source()

    docling_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=make_document(source),
    )

    docling_backend = FakeBackend(docling_result)

    quality_evaluator = FakeQualityEvaluator(
        make_quality(),
    )

    policy = FakePolicy(
        ExtractionPolicyResult(
            decision=ExtractionDecision.ACCEPT,
            reasons=["Extraction quality is sufficient."],
        )
    )

    pipeline = DocumentExtractionPipeline(
        docling_backend=docling_backend,
        quality_evaluator=quality_evaluator,
        policy=policy,
    )

    result = await pipeline.extract(
        source=source,
        content=BytesIO(b"fake docx content"),
        options=ExtractionOptions(),
    )

    assert result is docling_result
    assert result.warnings == []

    assert docling_backend.calls == 1
    assert quality_evaluator.calls == 1
    assert policy.calls == 1


@pytest.mark.anyio
async def test_pipeline_annotates_result_with_policy_reasons_on_fallback():
    source = make_source()

    docling_result = ExtractionResult(
        status=ExtractionStatus.SUCCESS,
        document=make_document(source),
        warnings=["Existing warning."],
    )

    docling_backend = FakeBackend(docling_result)

    quality_evaluator = FakeQualityEvaluator(
        ExtractionQuality(
            overall_score=0.40,
            text_score=0.40,
            structure_score=0.50,
            table_score=0.0,
            provenance_score=1.0,
            warnings=["Text extraction quality is low."],
        )
    )

    policy = FakePolicy(
        ExtractionPolicyResult(
            decision=ExtractionDecision.FALLBACK,
            reasons=["Text extraction quality is insufficient."],
        )
    )

    pipeline = DocumentExtractionPipeline(
        docling_backend=docling_backend,
        quality_evaluator=quality_evaluator,
        policy=policy,
    )

    result = await pipeline.extract(
        source=source,
        content=BytesIO(b"fake docx content"),
        options=ExtractionOptions(),
    )

    # Same document is still returned; no retry, no richer backend to
    # fall back to yet. The policy's reasons are surfaced as warnings.
    assert result is not docling_result
    assert result.document is docling_result.document
    assert result.status == ExtractionStatus.SUCCESS

    assert result.warnings == [
        "Existing warning.",
        "Text extraction quality is insufficient.",
    ]

    assert docling_backend.calls == 1
    assert quality_evaluator.calls == 1
    assert policy.calls == 1


@pytest.mark.anyio
async def test_pipeline_returns_failed_result_without_evaluating_quality():
    source = make_source()

    docling_result = ExtractionResult(
        status=ExtractionStatus.FAILED,
        document=None,
    )

    docling_backend = FakeBackend(docling_result)

    quality_evaluator = FakeQualityEvaluator(
        make_quality(),
    )

    policy = FakePolicy(
        ExtractionPolicyResult(
            decision=ExtractionDecision.ACCEPT,
            reasons=["Unused."],
        )
    )

    pipeline = DocumentExtractionPipeline(
        docling_backend=docling_backend,
        quality_evaluator=quality_evaluator,
        policy=policy,
    )

    result = await pipeline.extract(
        source=source,
        content=BytesIO(b"fake docx content"),
        options=ExtractionOptions(),
    )

    assert result is docling_result

    assert docling_backend.calls == 1

    # Quality and policy should not run because extraction did not
    # produce a usable document.
    assert quality_evaluator.calls == 0
    assert policy.calls == 0
