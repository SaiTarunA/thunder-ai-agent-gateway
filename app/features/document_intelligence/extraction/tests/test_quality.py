from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import ExtractionDecision
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.pymupdf import (
    PyMuPDFBackend,
)
from app.features.document_intelligence.extraction.quality import (
    ExtractionQualityEvaluator,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)
from app.features.document_intelligence.extraction.policy import (
    ExtractionPolicy,
)


BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"
EXPECTED_PATH = FIXTURE_DIR / "expected.json"

FIXTURES = [
    FIXTURE_DIR / "simple.md",
    FIXTURE_DIR / "simple.docx",
    FIXTURE_DIR / "simple.pdf",
]


@pytest.mark.anyio
async def test_pymupdf_quality_evaluation():
    pdf_path = Path(FIXTURE_DIR / "simple.pdf")

    source = SourceFile(
        file_id="simple-pdf",
        filename=pdf_path.name,
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=pdf_path.stat().st_size,
    )

    backend = PyMuPDFBackend()

    with pdf_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(enable_ocr=False),
        )

    assert result.document is not None

    evaluator = ExtractionQualityEvaluator()
    quality = evaluator.evaluate(result.document)

    print("\n--- Extraction quality ---")
    print(f"Overall:     {quality.overall_score}")
    print(f"Text:        {quality.text_score}")
    print(f"Structure:   {quality.structure_score}")
    print(f"Tables:      {quality.table_score}")
    print(f"Provenance:  {quality.provenance_score}")
    print(f"Warnings:    {quality.warnings}")

    assert quality.text_score is not None
    assert quality.provenance_score is not None

    assert quality.text_score >= 0.70
    assert quality.provenance_score >= 0.90
    assert quality.overall_score >= 0.60


@pytest.mark.anyio
async def test_pymupdf_result_is_accepted_by_policy():
    pdf_path = Path(FIXTURE_DIR / "simple.pdf")

    source = SourceFile(
        file_id="simple-pdf",
        filename=pdf_path.name,
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=pdf_path.stat().st_size,
    )

    backend = PyMuPDFBackend()

    with pdf_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(
                enable_ocr=False,
            ),
        )

    assert result.document is not None

    evaluator = ExtractionQualityEvaluator()

    quality = evaluator.evaluate(
        result.document,
    )

    policy = ExtractionPolicy()

    decision = policy.evaluate(
        quality,
    )

    print("\n--- Extraction policy ---")
    print(f"Decision: {decision.decision}")

    print("Reasons:")

    for reason in decision.reasons:
        print(f"- {reason}")

    assert decision.decision == ExtractionDecision.ACCEPT