from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionErrorCode,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.text import (
    PlainTextBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(file_id: str = "simple-txt") -> SourceFile:
    path = FIXTURE_DIR / "simple.txt"

    return SourceFile(
        file_id=file_id,
        filename="simple.txt",
        mime_type="text/plain",
        extension=".txt",
        size_bytes=path.stat().st_size,
    )


@pytest.mark.anyio
async def test_extracts_paragraphs_from_utf8_text():
    backend = PlainTextBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.txt").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    blocks = result.document.blocks
    assert len(blocks) == 4
    assert all(block.type == BlockType.PARAGRAPH for block in blocks)
    assert "This report summarizes platform capacity" in blocks[1].text


@pytest.mark.anyio
async def test_falls_back_to_charset_normalizer_for_non_utf8_encoding():
    backend = PlainTextBackend()
    source = make_source(file_id="latin1-txt")

    text = (
        "Le café régional a connu une hausse de fréquentation. La "
        "capacité générale reste élevée, et les résultats démontrent "
        "une croissance équilibrée. Aucune panne notable n'a été "
        "enregistrée cette année."
    )
    data = text.encode("latin-1")

    result = await backend.extract(
        source=source,
        content=BytesIO(data),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None
    assert result.document.blocks[0].text == text


@pytest.mark.anyio
async def test_rejects_empty_file():
    backend = PlainTextBackend()
    source = make_source(file_id="empty-txt")

    result = await backend.extract(
        source=source,
        content=BytesIO(b""),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


@pytest.mark.anyio
async def test_rejects_pathological_paragraph_count():
    backend = PlainTextBackend()
    source = make_source(file_id="bomb-txt")

    # One character paragraphs separated by blank lines; cheap to
    # generate, but produces far more paragraphs than the limit allows.
    data = ("a\n\n" * 200_001).encode("utf-8")

    result = await backend.extract(
        source=source,
        content=BytesIO(data),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert (
        result.errors[0].code
        == ExtractionErrorCode.RESOURCE_LIMIT_EXCEEDED
    )
