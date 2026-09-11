from io import BytesIO
from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    BlockType,
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


def print_document(document):
    print("\n=== CANONICAL DOCUMENT ===")
    print(f"ID: {document.id}")
    print(f"Title: {document.title}")
    print(f"Language: {document.language}")
    print(f"Pages: {len(document.pages)}")
    print(f"Blocks: {len(document.blocks)}")
    print()

    for block in document.blocks:
        print(f"[{block.ordinal}] " f"{block.type.value}: " f"{block.text!r}")


@pytest.mark.anyio
async def test_docling_extracts_markdown():
    md_path = FIXTURE_DIR / "simple.md"

    source = SourceFile(
        file_id="simple-markdown",
        filename=md_path.name,
        mime_type="text/markdown",
        extension=".md",
        size_bytes=md_path.stat().st_size,
    )

    backend = DoclingBackend()

    with md_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    print("\n=== EXTRACTION RESULT ===")
    print(f"Status: {result.status}")

    for error in result.errors:
        print(
            f"Error: code={error.code}, "
            f"backend={error.backend}, "
            f"message={error.message}, "
            f"details={error.details}",
        )

    for warning in result.warnings:
        print(f"Warning: {warning}")

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document

    print_document(document)

    assert document.source.file_id == "simple-markdown"

    assert len(document.blocks) > 0

    block_types = {
        block.type
        for block in document.blocks
    }

    assert BlockType.HEADING in block_types
    assert BlockType.PARAGRAPH in block_types


@pytest.mark.anyio
async def test_docling_extracts_docx():
    docx_path = FIXTURE_DIR / "simple.docx"

    source = SourceFile(
        file_id="simple-docx",
        filename=docx_path.name,
        mime_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        extension=".docx",
        size_bytes=docx_path.stat().st_size,
    )

    backend = DoclingBackend()

    with docx_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    print("\n=== EXTRACTION RESULT ===")
    print(f"Status: {result.status}")

    for error in result.errors:
        print(
            f"Error: code={error.code}, "
            f"backend={error.backend}, "
            f"message={error.message}, "
            f"details={error.details}"
        )

    for warning in result.warnings:
        print(f"Warning: {warning}")

    assert result.status.value == "success"
    assert result.document is not None

    document = result.document
    print_document(document)

    assert document.title == "Quarterly Infrastructure Report"

    assert any(
        block.text == "Overview"
        and block.type == BlockType.HEADING
        for block in document.blocks
    )

    assert any(
        block.type == BlockType.LIST_ITEM
        for block in document.blocks
    )

    tables = [
        block.table
        for block in document.blocks
        if block.type == BlockType.TABLE
        and block.table is not None
    ]

    assert len(tables) == 1

    table = tables[0]

    assert table.rows == 5
    assert table.columns == 4

    assert any(
        cell.text == "us-east-1"
        for cell in table.cells
    )


@pytest.mark.anyio
async def test_docling_extracts_html():
    html_path = FIXTURE_DIR / "simple.html"

    source = SourceFile(
        file_id="simple-html",
        filename=html_path.name,
        mime_type="text/html",
        extension=".html",
        size_bytes=html_path.stat().st_size,
    )

    backend = DoclingBackend()

    with html_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    print("\n=== EXTRACTION RESULT ===")
    print(f"Status: {result.status}")

    for error in result.errors:
        print(
            f"Error: code={error.code}, "
            f"backend={error.backend}, "
            f"message={error.message}, "
            f"details={error.details}",
        )

    for warning in result.warnings:
        print(f"Warning: {warning}")

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document

    print_document(document)

    assert document.source.file_id == "simple-html"
    assert document.title == "Quarterly Infrastructure Report"

    assert len(document.blocks) > 0

    block_types = {
        block.type
        for block in document.blocks
    }

    assert BlockType.HEADING in block_types
    assert BlockType.PARAGRAPH in block_types
    assert BlockType.TABLE in block_types
    assert BlockType.LIST_ITEM in block_types


@pytest.mark.anyio
async def test_docling_extracts_htm():
    htm_path = FIXTURE_DIR / "simple.htm"

    source = SourceFile(
        file_id="simple-htm",
        filename=htm_path.name,
        mime_type="text/html",
        extension=".htm",
        size_bytes=htm_path.stat().st_size,
    )

    backend = DoclingBackend()

    with htm_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document

    assert document.source.file_id == "simple-htm"
    assert document.title == "Quarterly Infrastructure Report"

    assert len(document.blocks) > 0

    block_types = {
        block.type
        for block in document.blocks
    }

    assert BlockType.HEADING in block_types
    assert BlockType.PARAGRAPH in block_types
    assert BlockType.TABLE in block_types
    assert BlockType.LIST_ITEM in block_types


@pytest.mark.anyio
async def test_docling_extracts_png():
    image_path = FIXTURE_DIR / "simple.png"

    source = SourceFile(
        file_id="simple-png",
        filename=image_path.name,
        mime_type="image/png",
        extension=".png",
        size_bytes=image_path.stat().st_size,
    )

    backend = DoclingBackend()

    with image_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(
                enable_ocr=True,
                enable_tables=True,
            ),
        )

    print("\n=== EXTRACTION RESULT ===")
    print(f"Status: {result.status}")

    for error in result.errors:
        print(
            f"Error: code={error.code}, "
            f"backend={error.backend}, "
            f"message={error.message}, "
            f"details={error.details}"
        )

    for warning in result.warnings:
        print(f"Warning: {warning}")

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document

    print_document(document)

    assert document.source.file_id == "simple-png"

    # Title
    assert document.title == "Quarterly Infrastructure Report"

    # Paragraph
    text_blocks = [
        block.text
        for block in document.blocks
        if block.text
    ]

    extracted_text = "\n".join(text_blocks)

    assert (
        "This report summarizes platform capacity"
        in extracted_text
    )

    # Table detection
    table_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.TABLE
    ]

    assert len(table_blocks) == 1

    table = table_blocks[0].table

    assert table is not None

    # Structural detection succeeded.
    assert table.rows == 5
    assert table.columns == 4

    # Image OCR table reconstruction is currently imperfect,
    # so verify recovered content rather than exact cell geometry.
    table_text = "\n".join(
        cell.text
        for cell in table.cells
        if cell.text
    )

    assert "Region" in table_text
    assert "Nodes" in table_text
    assert "Utilization" in table_text
    assert "Status" in table_text

    assert "us-east-1" in table_text
    assert "us-west-2" in table_text
    assert "eu-central-1" in table_text
    assert "ap-south-1" in table_text


@pytest.mark.anyio
async def test_docling_extracts_jpeg():
    image_path = FIXTURE_DIR / "simple.jpeg"

    source = SourceFile(
        file_id="simple-jpeg",
        filename=image_path.name,
        mime_type="image/jpeg",
        extension=".jpeg",
        size_bytes=image_path.stat().st_size,
    )

    backend = DoclingBackend()

    with image_path.open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(
                enable_ocr=True,
                enable_tables=True,
            ),
        )

    print("\n=== EXTRACTION RESULT ===")
    print(f"Status: {result.status}")

    for error in result.errors:
        print(
            f"Error: code={error.code}, "
            f"backend={error.backend}, "
            f"message={error.message}, "
            f"details={error.details}"
        )

    for warning in result.warnings:
        print(f"Warning: {warning}")

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    document = result.document

    print_document(document)

    assert document.source.file_id == "simple-jpeg"

    # Title
    assert document.title == "Quarterly Infrastructure Report"

    # Paragraph
    text_blocks = [
        block.text
        for block in document.blocks
        if block.text
    ]

    extracted_text = "\n".join(text_blocks)

    assert (
        "This report summarizes platform capacity"
        in extracted_text
    )

    # Table
    table_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.TABLE
    ]

    assert len(table_blocks) == 1

    table = table_blocks[0].table

    assert table is not None

    # Structural detection
    assert table.rows == 5
    assert table.columns == 4

    # Verify recovered table content without requiring
    # exact OCR cell geometry.
    table_text = "\n".join(
        cell.text
        for cell in table.cells
        if cell.text
    )

    assert "Region" in table_text
    assert "Nodes" in table_text
    assert "Utilization" in table_text
    assert "Status" in table_text

    assert "us-east-1" in table_text
    assert "us-west-2" in table_text
    assert "eu-central-1" in table_text
    assert "ap-south-1" in table_text