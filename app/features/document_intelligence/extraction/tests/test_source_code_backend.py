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
from app.features.document_intelligence.extraction.backends.source_code import (
    SourceCodeBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


def make_source(
    file_id: str = "simple-py",
    extension: str = ".py",
) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        filename=f"simple{extension}",
        extension=extension,
        size_bytes=100,
    )


@pytest.mark.parametrize(
    "extension, expected_language",
    [
        (".py", "python"),
        (".js", "javascript"),
        (".jsx", "javascript"),
        (".ts", "typescript"),
        (".tsx", "typescript"),
        (".java", "java"),
        (".c", "c"),
        (".cpp", "cpp"),
        (".cs", "csharp"),
        (".go", "go"),
        (".php", "php"),
        (".rb", "ruby"),
        (".sh", "bash"),
        (".css", "css"),
    ],
)
@pytest.mark.anyio
async def test_extracts_each_fixture_as_single_code_block(
    extension: str,
    expected_language: str,
):
    backend = SourceCodeBackend()
    source = make_source(
        file_id=f"lang-test{extension}",
        extension=extension,
    )

    assert backend.supports(source) is True

    fixture_path = FIXTURE_DIR / f"simple{extension}"

    with fixture_path.open("rb") as f:
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
    assert blocks[0].metadata["language"] == expected_language

    # Faithful preservation: indentation, blank lines, quoting, comment
    # syntax all untouched, for every language.
    original = fixture_path.read_text(encoding="utf-8")
    assert blocks[0].text == original


@pytest.mark.anyio
async def test_rejects_empty_file():
    backend = SourceCodeBackend()
    source = make_source(file_id="empty-py")

    result = await backend.extract(
        source=source,
        content=BytesIO(b""),
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.FAILED
    assert result.errors[0].code == ExtractionErrorCode.INVALID_CONTENT


def test_does_not_support_unrelated_extensions():
    backend = SourceCodeBackend()
    source = make_source(file_id="not-code", extension=".pdf")

    assert backend.supports(source) is False
