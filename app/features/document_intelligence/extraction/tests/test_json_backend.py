from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionErrorCode,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.json_backend import (
    JsonBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(file_id: str = "simple-json") -> SourceFile:
    return SourceFile(
        file_id=file_id,
        filename="simple.json",
        mime_type="application/json",
        extension=".json",
        size_bytes=100,
    )


@pytest.mark.anyio
async def test_extracts_json_as_single_code_block():
    backend = JsonBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.json").open("rb") as f:
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
    assert blocks[0].metadata["language"] == "json"

    # Re-parses to the same structure, even though whitespace was
    # normalized by json.dumps(indent=2).
    assert json.loads(blocks[0].text) == {
        "region": "us-east-1",
        "nodes": 120,
        "utilization": 0.72,
        "status": "Healthy",
        "tags": ["production", "primary"],
    }


@pytest.mark.anyio
async def test_rejects_invalid_json():
    backend = JsonBackend()
    source = make_source(file_id="invalid-json")

    result = await backend.extract(
        source=source,
        content=BytesIO(b"{not valid json"),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


@pytest.mark.anyio
async def test_rejects_pathologically_deep_nesting():
    backend = JsonBackend()
    source = make_source(file_id="deep-json")

    depth = 100_000
    data = (b"[" * depth) + (b"]" * depth)

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
