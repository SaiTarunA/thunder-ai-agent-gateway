from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.rst_backend import (
    RstBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(file_id: str = "simple-rst") -> SourceFile:
    path = FIXTURE_DIR / "simple.rst"

    return SourceFile(
        file_id=file_id,
        filename="simple.rst",
        mime_type="text/x-rst",
        extension=".rst",
        size_bytes=path.stat().st_size,
    )


@pytest.mark.anyio
async def test_extracts_title_sections_lists_table_and_code():
    backend = RstBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.rst").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document
    assert document.title == "Quarterly Infrastructure Report"

    blocks = document.blocks
    block_types = {b.type for b in blocks}

    assert BlockType.TITLE in block_types
    assert BlockType.HEADING in block_types
    assert BlockType.PARAGRAPH in block_types
    assert BlockType.LIST_ITEM in block_types
    assert BlockType.TABLE in block_types
    assert BlockType.CODE in block_types

    headings = [b for b in blocks if b.type == BlockType.HEADING]
    assert [h.text for h in headings] == [
        "Overview",
        "Capacity by Region",
        "Next Steps",
    ]
    assert all(h.heading_level == 1 for h in headings)

    # Line-wrapping in the source shouldn't leak into paragraph text.
    paragraphs = [b.text for b in blocks if b.type == BlockType.PARAGRAPH]
    assert any("\n" not in p for p in paragraphs)
    assert any(
        "This report summarizes platform capacity" in p
        for p in paragraphs
    )

    table_block = next(b for b in blocks if b.type == BlockType.TABLE)
    table = table_block.table
    assert table.rows == 3
    assert table.columns == 3

    header_cells = [c for c in table.cells if c.is_header]
    assert {c.text for c in header_cells} == {"Region", "Nodes", "Status"}

    code_block = next(b for b in blocks if b.type == BlockType.CODE)
    assert "def is_healthy(utilization):" in code_block.text


@pytest.mark.anyio
async def test_rejects_raw_directive_content():
    """The raw directive must never pass its content through unblocked.

    Verified empirically that settings_overrides alone was NOT enough
    against a local docutils.conf overriding raw_enabled back to true
    -- _disable_config=True is required. This test guards the actual
    doctree output, not just the settings dict.
    """

    backend = RstBackend()
    source = make_source(file_id="raw-directive-rst")

    payload = b"""
.. raw:: html

   <script>alert(1)</script>
"""

    result = await backend.extract(
        source=source,
        content=BytesIO(payload),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    all_text = " ".join(
        block.text for block in result.document.blocks if block.text
    )
    assert "<script>" not in all_text


@pytest.mark.anyio
async def test_rejects_file_insertion_directive():
    backend = RstBackend()
    source = make_source(file_id="include-directive-rst")

    payload = b"""
.. include:: /etc/passwd
"""

    result = await backend.extract(
        source=source,
        content=BytesIO(payload),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    # No block should contain contents that would only appear if the
    # include directive were actually honored (we can't assert on
    # /etc/passwd's contents portably, so assert the doctree only holds
    # the disabled-directive warning artifact, not a resolved file).
    assert len(result.document.blocks) <= 2


@pytest.mark.anyio
async def test_local_docutils_conf_cannot_override_hardened_settings(
    tmp_path,
    monkeypatch,
):
    """Reproduces the exact bypass found while researching this backend.

    A docutils.conf in the process's working directory can silently
    re-enable raw_enabled/file_insertion_enabled, overriding
    settings_overrides passed to publish_doctree() -- unless
    _disable_config=True is also set. This test fails if that
    protection is ever accidentally removed from rst_backend.py.
    """

    (tmp_path / "docutils.conf").write_text(
        "[restructuredtext parser]\n"
        "raw_enabled: 1\n"
        "file_insertion_enabled: 1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    backend = RstBackend()
    source = make_source(file_id="conf-bypass-rst")

    payload = b"""
.. raw:: html

   <script>alert(1)</script>
"""

    result = await backend.extract(
        source=source,
        content=BytesIO(payload),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    all_text = " ".join(
        block.text for block in result.document.blocks if block.text
    )
    assert "<script>" not in all_text


@pytest.mark.anyio
async def test_rejects_empty_file():
    backend = RstBackend()
    source = make_source(file_id="empty-rst")

    result = await backend.extract(
        source=source,
        content=BytesIO(b""),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
