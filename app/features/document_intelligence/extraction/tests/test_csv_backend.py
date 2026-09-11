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
from app.features.document_intelligence.extraction.backends.csv_backend import (
    CsvBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(file_id: str = "simple-csv") -> SourceFile:
    return SourceFile(
        file_id=file_id,
        filename="simple.csv",
        mime_type="text/csv",
        extension=".csv",
        size_bytes=100,
    )


@pytest.mark.anyio
async def test_extracts_csv_as_single_table_with_string_fidelity():
    backend = CsvBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.csv").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    blocks = result.document.blocks
    assert len(blocks) == 1
    assert blocks[0].type == BlockType.TABLE

    table = blocks[0].table
    assert table is not None
    assert table.rows == 5  # header + 4 data rows
    assert table.columns == 4

    header_cells = [c for c in table.cells if c.is_header]
    assert {c.text for c in header_cells} == {
        "Region", "Nodes", "Utilization", "Status",
    }

    # Leading-zero value must survive as a literal string, not become 7.
    nodes_values = {
        c.text for c in table.cells if c.row > 0 and c.column == 1
    }
    assert "007" in nodes_values


@pytest.mark.anyio
async def test_rejects_empty_file():
    backend = CsvBackend()
    source = make_source(file_id="empty-csv")

    result = await backend.extract(
        source=source,
        content=BytesIO(b""),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


@pytest.mark.anyio
async def test_rejects_malformed_csv():
    backend = CsvBackend()
    source = make_source(file_id="malformed-csv")

    # Header declares 2 columns; second data row has 4 fields.
    data = b"a,b\n1,2\n1,2,3,4\n"

    result = await backend.extract(
        source=source,
        content=BytesIO(data),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT
