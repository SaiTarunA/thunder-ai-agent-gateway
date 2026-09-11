from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
import yaml

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionErrorCode,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.yaml_backend import (
    YamlBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(
    file_id: str = "simple-yaml",
    extension: str = ".yaml",
) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        filename=f"simple{extension}",
        mime_type="application/x-yaml",
        extension=extension,
        size_bytes=100,
    )


@pytest.mark.anyio
async def test_extracts_yaml_as_single_code_block():
    backend = YamlBackend()
    source = make_source()

    with (FIXTURE_DIR / "simple.yaml").open("rb") as f:
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
    assert blocks[0].metadata["language"] == "yaml"

    reparsed = yaml.safe_load(blocks[0].text)
    assert reparsed == {
        "region": "us-east-1",
        "nodes": 120,
        "utilization": 0.72,
        "status": "Healthy",
        "tags": ["production", "primary"],
    }


def test_supports_yml_extension_too():
    backend = YamlBackend()
    source = make_source(file_id="simple-yml", extension=".yml")

    assert backend.supports(source) is True


@pytest.mark.anyio
async def test_rejects_invalid_yaml():
    backend = YamlBackend()
    source = make_source(file_id="invalid-yaml")

    result = await backend.extract(
        source=source,
        content=BytesIO(b"key: [unclosed"),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


@pytest.mark.anyio
async def test_rejects_high_anchor_alias_density_before_parsing():
    backend = YamlBackend()
    source = make_source(file_id="alias-bomb-yaml")

    # A small, cheap-to-construct YAML document with anchor/alias
    # density far beyond anything a legitimate config file would use.
    # This must be rejected before yaml.safe_load() ever runs, since
    # that's where the actual expansion damage would occur.
    lines = ["anchors:"]
    for i in range(1100):
        lines.append(f"  - &a{i} value{i}")
    lines.append("aliases:")
    for i in range(1100):
        lines.append(f"  - *a{i}")

    data = "\n".join(lines).encode("utf-8")

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
