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
from app.features.document_intelligence.extraction.backends.xml_backend import (
    XmlBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(file_id: str = "simple-xml") -> SourceFile:
    return SourceFile(
        file_id=file_id,
        filename="simple.xml",
        mime_type="application/xml",
        extension=".xml",
        size_bytes=100,
    )


@pytest.mark.anyio
async def test_extracts_xml_as_single_code_block():
    backend = XmlBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.xml").open("rb") as f:
        result = await backend.extract(
            source=source,
            content=f,
            options=ExtractionOptions(),
        )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None

    blocks = result.document.blocks
    assert len(blocks) == 1
    assert blocks[0].type == BlockType.CODE
    assert blocks[0].metadata["language"] == "xml"
    assert "us-east-1" in blocks[0].text
    assert "<report>" in blocks[0].text


@pytest.mark.anyio
async def test_rejects_malformed_xml():
    backend = XmlBackend()
    source = make_source(file_id="malformed-xml")

    result = await backend.extract(
        source=source,
        content=BytesIO(b"<a><b></a>"),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


@pytest.mark.anyio
async def test_rejects_dtd_with_entity_expansion():
    backend = XmlBackend()
    source = make_source(file_id="xxe-xml")

    # A classic entity-expansion ("billion laughs") style payload.
    # defusedxml must reject this outright rather than expand it.
    payload = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
     <!ENTITY lol "lol">
     <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    ]>
    <lolz>&lol2;</lolz>
    """

    result = await backend.extract(
        source=source,
        content=BytesIO(payload),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT
