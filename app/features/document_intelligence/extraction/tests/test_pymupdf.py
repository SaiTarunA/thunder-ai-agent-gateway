from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import BlockType
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.pymupdf import (
    PyMuPDFBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


@pytest.mark.anyio
async def test_pymupdf_extracts_native_pdf():
    pdf_path = FIXTURE_DIR / "simple.pdf"

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
                enable_tables=False,
                enable_images=False,
                enable_layout=False,
            ),
        )

    assert result.status.value == "success"
    assert result.document is not None

    document = result.document

    assert len(document.pages) == 1
    assert len(document.blocks) > 0

    for block in document.blocks:
        assert block.text
        assert block.provenance
        assert block.provenance[0].page_number == 1
        assert block.provenance[0].bbox is not None


@pytest.mark.anyio
async def test_pymupdf_extracts_expected_content():
    pdf_path = FIXTURE_DIR / "simple.pdf"

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

    assert result.status.value == "success"
    assert result.document is not None

    document = result.document

    print("\n--- PyMuPDF extraction ---")
    print(f"Pages:  {len(document.pages)}")
    print(f"Blocks: {len(document.blocks)}")

    for block in document.blocks:
        print(
            f"[{block.type.value}] "
            f"[page={block.provenance[0].page_number}] "
            f"{block.text!r}"
        )

    extracted_text = "\n".join(
        block.text or ""
        for block in document.blocks
    )

    assert "Quarterly Infrastructure Report" in extracted_text


@pytest.mark.anyio
async def test_pymupdf_detects_basic_headings():
    pdf_path = FIXTURE_DIR / "simple.pdf"

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
                enable_tables=False,
                enable_images=False,
                enable_layout=False,
            ),
        )

    assert result.status.value == "success"
    assert result.document is not None

    document = result.document

    headings = [
        block
        for block in document.blocks
        if block.type == BlockType.HEADING
    ]

    print("\n--- Detected headings ---")

    for heading in headings:
        print(
            f"[level={heading.heading_level}] "
            f"[font={heading.metadata.get('font_size')}] "
            f"{heading.text!r}"
        )

    assert len(headings) >= 3

    heading_by_text = {
        heading.text: heading
        for heading in headings
    }

    assert "Overview" in heading_by_text
    assert "Scope" in heading_by_text
    assert "Capacity by Region" in heading_by_text
    assert "Next Steps" in heading_by_text

    assert heading_by_text["Overview"].heading_level == 2
    assert heading_by_text["Scope"].heading_level == 3
    assert heading_by_text["Capacity by Region"].heading_level == 2
    assert heading_by_text["Next Steps"].heading_level == 2


@pytest.mark.anyio
async def test_pymupdf_detects_document_title():
    pdf_path = FIXTURE_DIR / "simple.pdf"

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

    assert result.status.value == "success"
    assert result.document is not None

    document = result.document

    assert document.title == "Quarterly Infrastructure Report"

    title_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.TITLE
    ]

    assert len(title_blocks) == 1

    title_block = title_blocks[0]

    assert title_block.text == "Quarterly Infrastructure Report"
    assert title_block.heading_level is None


@pytest.mark.anyio
async def test_pymupdf_detects_numbered_list_items():
    pdf_path = FIXTURE_DIR / "simple.pdf"

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

    assert result.status.value == "success"
    assert result.document is not None

    document = result.document

    numbered_items = [
        block
        for block in document.blocks
        if block.type == BlockType.LIST_ITEM
        and block.text.startswith(("1.", "2.", "3."))
    ]

    assert len(numbered_items) == 3

    assert numbered_items[0].text.startswith("1.")
    assert numbered_items[1].text.startswith("2.")
    assert numbered_items[2].text.startswith("3.")