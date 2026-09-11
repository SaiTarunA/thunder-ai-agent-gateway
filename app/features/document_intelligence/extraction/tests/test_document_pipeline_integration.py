from __future__ import annotations

from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    SourceFile,
)
from app.features.document_intelligence.extraction.pipelines.document import (
    DocumentExtractionPipeline,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"

FIXTURES = [
    (
        "simple.md",
        "text/markdown",
        ".md",
    ),
    (
        "simple.docx",
        (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        ".docx",
    ),
    (
        "simple.html",
        "text/html",
        ".html",
    ),
    (
        "simple.htm",
        "text/html",
        ".htm",
    ),
    (
        "simple.tex",
        "application/x-tex",
        ".tex",
    ),
    (
        "simple.pptx",
        (
            "application/vnd.openxmlformats-officedocument."
            "presentationml.presentation"
        ),
        ".pptx",
    ),
    (
        "simple.odt",
        "application/vnd.oasis.opendocument.text",
        ".odt",
    ),
    (
        "simple.epub",
        "application/epub+zip",
        ".epub",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "filename, mime_type, extension",
    FIXTURES,
)
async def test_document_pipeline_accepts_simple_fixtures(
    filename: str,
    mime_type: str,
    extension: str,
):
    fixture_path = FIXTURE_DIR / filename

    source = SourceFile(
        file_id=f"simple-{extension.lstrip('.')}",
        filename=filename,
        mime_type=mime_type,
        extension=extension,
        size_bytes=fixture_path.stat().st_size,
        content_hash=f"simple-{extension.lstrip('.')}-test",
    )

    pipeline = DocumentExtractionPipeline()

    with fixture_path.open("rb") as content:
        result = await pipeline.extract(
            source=source,
            content=content,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    assert len(result.document.blocks) > 0

    # These fixtures are clean enough to meet the quality policy, so no
    # fallback warnings should be attached.
    assert result.warnings == []
