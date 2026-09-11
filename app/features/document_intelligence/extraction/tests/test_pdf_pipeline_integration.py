from __future__ import annotations

from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    SourceFile,
)
from app.features.document_intelligence.extraction.pipelines.pdf import (
    PdfExtractionPipeline,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)


BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"
EXPECTED_PATH = FIXTURE_DIR / "expected.json"

FIXTURES = [
    FIXTURE_DIR / "simple.md",
    FIXTURE_DIR / "simple.docx",
    FIXTURE_DIR / "simple.pdf",
]


class TrackingDoclingBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(
        self,
        *,
        source,
        content,
        options,
    ):
        self.calls += 1

        raise AssertionError(
            "Docling should not be called for this fixture."
        )


@pytest.mark.anyio
async def test_pdf_pipeline_uses_native_extraction_for_simple_pdf():
    fixture_path = Path(FIXTURE_DIR / "simple.pdf")

    content_bytes = fixture_path.read_bytes()

    source = SourceFile(
        file_id="simple-pdf",
        filename="simple.pdf",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=len(content_bytes),
        content_hash="simple-pdf-test",
    )

    docling_backend = TrackingDoclingBackend()

    pipeline = PdfExtractionPipeline(
        docling_backend=docling_backend,
    )

    with fixture_path.open("rb") as content:
        result = await pipeline.extract(
            source=source,
            content=content,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS

    assert result.document is not None

    document = result.document

    assert document.extraction.backend == "pymupdf"

    assert document.title == "Quarterly Infrastructure Report"

    assert len(document.pages) == 1

    assert len(document.blocks) > 0

    texts = [
        block.text
        for block in document.blocks
        if block.text
    ]

    assert any(
        "Overview" in text
        for text in texts
    )

    assert any(
        "Next Steps" in text
        for text in texts
    )

    assert docling_backend.calls == 0