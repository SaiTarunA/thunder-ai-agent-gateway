from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionErrorCode,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.docling import (
    DoclingBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(filename: str, extension: str) -> SourceFile:
    path = FIXTURE_DIR / filename

    return SourceFile(
        file_id=f"simple-{extension.lstrip('.')}",
        filename=filename,
        extension=extension,
        size_bytes=path.stat().st_size,
    )


@pytest.mark.anyio
async def test_docling_extracts_latex():
    source = make_source("simple.tex", ".tex")
    backend = DoclingBackend()

    with (FIXTURE_DIR / "simple.tex").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document
    assert document.title == "Quarterly Infrastructure Report"

    block_types = {b.type for b in document.blocks}
    assert BlockType.TITLE in block_types
    assert BlockType.HEADING in block_types
    assert BlockType.PARAGRAPH in block_types
    assert BlockType.LIST_ITEM in block_types

    assert any(
        b.type == BlockType.HEADING and b.text == "Overview"
        for b in document.blocks
    )


@pytest.mark.anyio
async def test_docling_extracts_pptx():
    source = make_source("simple.pptx", ".pptx")
    backend = DoclingBackend()

    with (FIXTURE_DIR / "simple.pptx").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document
    assert document.title == "Quarterly Infrastructure Report"

    # Docling gives each slide its own TitleItem (slides map to pages,
    # each with its own title placeholder) -- not a document defect,
    # so assert on presence of all three slide titles rather than
    # exactly one TITLE block.
    titles = [b.text for b in document.blocks if b.type == BlockType.TITLE]
    assert titles == [
        "Quarterly Infrastructure Report",
        "Overview",
        "Capacity by Region",
    ]

    tables = [b.table for b in document.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 1
    assert tables[0].rows == 3
    assert tables[0].columns == 3
    assert any(c.text == "us-east-1" for c in tables[0].cells)


@pytest.mark.anyio
async def test_docling_extracts_odt():
    source = make_source("simple.odt", ".odt")
    backend = DoclingBackend()

    with (FIXTURE_DIR / "simple.odt").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document
    assert document.title == "Quarterly Infrastructure Report"

    block_types = {b.type for b in document.blocks}
    assert BlockType.HEADING in block_types
    assert BlockType.PARAGRAPH in block_types
    assert BlockType.LIST_ITEM in block_types
    assert BlockType.TABLE in block_types

    table_block = next(
        b for b in document.blocks if b.type == BlockType.TABLE
    )
    assert table_block.table.rows == 3
    assert table_block.table.columns == 3

    # Regression guard: table cell content must not also appear as
    # duplicate top-level PARAGRAPH blocks (Docling's ODT backend
    # represents each cell's content twice internally -- once via
    # TableData.table_cells, once via a "rich cell group" parented
    # under the table -- and DoclingBackend must filter the latter).
    paragraph_texts = {
        b.text for b in document.blocks if b.type == BlockType.PARAGRAPH
    }
    assert "Region" not in paragraph_texts
    assert "us-east-1" not in paragraph_texts


@pytest.mark.anyio
async def test_docling_extracts_epub():
    source = make_source("simple.epub", ".epub")
    backend = DoclingBackend()

    with (FIXTURE_DIR / "simple.epub").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document
    assert document.title == "Quarterly Infrastructure Report"

    headings = [b.text for b in document.blocks if b.type == BlockType.HEADING]
    assert headings == ["Overview", "Next Steps"]

    assert any(
        b.type == BlockType.LIST_ITEM
        and "Managed compute" in (b.text or "")
        for b in document.blocks
    )


@pytest.mark.anyio
async def test_docling_rejects_zip_bomb_style_odt():
    backend = DoclingBackend()
    source = SourceFile(
        file_id="zip-bomb-odt",
        filename="bomb.odt",
        extension=".odt",
        size_bytes=100,
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.xml", "0" * 50_000_000)
    buffer.seek(0)

    result = await backend.extract(
        source=source,
        content=buffer,
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert (
        result.errors[0].code
        == ExtractionErrorCode.RESOURCE_LIMIT_EXCEEDED
    )
