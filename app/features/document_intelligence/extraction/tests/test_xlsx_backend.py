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
from app.features.document_intelligence.extraction.backends.xlsx_backend import (
    XlsxBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(file_id: str = "simple-xlsx") -> SourceFile:
    path = FIXTURE_DIR / "simple.xlsx"

    return SourceFile(
        file_id=file_id,
        filename="simple.xlsx",
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        extension=".xlsx",
        size_bytes=path.stat().st_size,
    )


@pytest.mark.anyio
async def test_extracts_each_sheet_as_heading_and_table():
    backend = XlsxBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.xlsx").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    blocks = result.document.blocks

    headings = [b for b in blocks if b.type == BlockType.HEADING]
    tables = [b for b in blocks if b.type == BlockType.TABLE]

    assert [h.text for h in headings] == ["Regions", "Notes"]
    assert len(tables) == 2

    # First sheet has a merged title row spanning all 4 columns, then a
    # header row, then 3 data rows.
    regions_table = tables[0].table
    assert regions_table.rows == 5
    assert regions_table.columns == 4

    merged_cell = next(
        c for c in regions_table.cells
        if c.text == "Quarterly Capacity Report"
    )
    assert merged_cell.column_span == 4

    header_cells = [
        c for c in regions_table.cells
        if c.is_header and c.row == 1
    ]
    assert {c.text for c in header_cells} == {
        "Region", "Nodes", "Utilization", "Status",
    }

    # Second sheet: leading-zero code must survive as a literal string.
    notes_table = tables[1].table
    assert any(c.text == "007" for c in notes_table.cells)


@pytest.mark.anyio
async def test_extracts_xlsm_the_same_way_as_xlsx():
    backend = XlsxBackend()

    path = FIXTURE_DIR / "simple.xlsm"
    source = SourceFile(
        file_id="simple-xlsm",
        filename="simple.xlsm",
        mime_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        extension=".xlsm",
        size_bytes=path.stat().st_size,
    )

    with path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    blocks = result.document.blocks
    headings = [b for b in blocks if b.type == BlockType.HEADING]
    tables = [b for b in blocks if b.type == BlockType.TABLE]

    assert [h.text for h in headings] == ["Regions"]
    assert len(tables) == 1
    assert tables[0].table.rows == 3
    assert tables[0].table.columns == 4


@pytest.mark.anyio
async def test_rejects_non_zip_content():
    backend = XlsxBackend()
    source = make_source(file_id="not-a-zip")

    result = await backend.extract(
        source=source,
        content=BytesIO(b"this is not a zip file"),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


@pytest.mark.anyio
async def test_rejects_zip_bomb_style_container():
    backend = XlsxBackend()
    source = make_source(file_id="zip-bomb-xlsx")

    # A tiny, highly-compressible entry whose declared uncompressed
    # size vastly exceeds the ratio guard, mimicking a zip-bomb shape.
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", "0" * 50_000_000)
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
