from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.features.document_intelligence.canonical.enums import (
    ExtractionErrorCode,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    CanonicalDocument,
    ExtractionMetadata,
    SourceFile,
)
from app.features.document_intelligence.extraction.factory import (
    build_default_extraction_registry,
)
from app.features.document_intelligence.extraction.interfaces import (
    DocumentSource,
)
from app.features.document_intelligence.extraction.orchestrator import (
    ExtractionOrchestrator,
)
from app.features.document_intelligence.extraction.registry import (
    ExtractionBackendRegistry,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionError,
    ExtractionOptions,
    ExtractionResult,
)

BASE_DIR = Path(__file__).parent.parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"


class FakeDocumentSource(DocumentSource):
    """In-memory DocumentSource backed by a fixed file_id -> bytes map."""

    def __init__(
        self,
        files: dict[str, tuple[SourceFile, bytes]],
        *,
        fail_metadata_for: set[str] | None = None,
        fail_download_for: set[str] | None = None,
    ) -> None:
        self._files = files
        self._fail_metadata_for = fail_metadata_for or set()
        self._fail_download_for = fail_download_for or set()

    async def get_metadata(self, file_id: str) -> SourceFile:
        if file_id in self._fail_metadata_for:
            raise RuntimeError("metadata lookup failed")

        return self._files[file_id][0]

    async def download(self, file_id: str) -> BytesIO:
        if file_id in self._fail_download_for:
            raise RuntimeError("download failed")

        return BytesIO(self._files[file_id][1])


class FakeBackend:
    """Fake extraction backend candidate."""

    def __init__(
        self,
        name: str,
        *,
        supports_extensions: set[str],
        result: ExtractionResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._supports_extensions = supports_extensions
        self._result = result
        self._raises = raises
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def supports(self, source: SourceFile) -> bool:
        return source.extension.lower() in self._supports_extensions

    async def extract(
        self,
        *,
        source,
        content,
        options,
    ) -> ExtractionResult:
        self.calls += 1

        if self._raises is not None:
            raise self._raises

        return self._result


def make_source(
    file_id: str = "test-file",
    extension: str = ".pdf",
) -> SourceFile:
    return SourceFile(
        file_id=file_id,
        filename=f"example{extension}",
        extension=extension,
        size_bytes=100,
    )


def make_document(source: SourceFile, *, backend: str) -> CanonicalDocument:
    return CanonicalDocument(
        id="document:test",
        source=source,
        extraction=ExtractionMetadata(
            backend=backend,
            status=ExtractionStatus.SUCCESS,
        ),
    )


@pytest.mark.anyio
async def test_orchestrator_returns_unsupported_file_type_when_no_candidates():
    source = make_source(extension=".unsupported")

    document_source = FakeDocumentSource(
        {source.file_id: (source, b"content")},
    )

    registry = ExtractionBackendRegistry([])

    orchestrator = ExtractionOrchestrator(document_source, registry)

    result = await orchestrator.extract(source.file_id)

    assert result.status == ExtractionStatus.FAILED
    assert len(result.errors) == 1
    assert (
        result.errors[0].code
        == ExtractionErrorCode.UNSUPPORTED_FILE_TYPE
    )


@pytest.mark.anyio
async def test_orchestrator_reports_download_failed_on_metadata_error():
    document_source = FakeDocumentSource(
        {},
        fail_metadata_for={"missing-file"},
    )

    registry = ExtractionBackendRegistry([])

    orchestrator = ExtractionOrchestrator(document_source, registry)

    result = await orchestrator.extract("missing-file")

    assert result.status == ExtractionStatus.FAILED
    assert (
        result.errors[0].code == ExtractionErrorCode.DOWNLOAD_FAILED
    )


@pytest.mark.anyio
async def test_orchestrator_reports_download_failed_on_content_error():
    source = make_source()

    document_source = FakeDocumentSource(
        {source.file_id: (source, b"content")},
        fail_download_for={source.file_id},
    )

    registry = ExtractionBackendRegistry(
        [
            FakeBackend(
                "fake",
                supports_extensions={".pdf"},
            )
        ]
    )

    orchestrator = ExtractionOrchestrator(document_source, registry)

    result = await orchestrator.extract(source.file_id)

    assert result.status == ExtractionStatus.FAILED
    assert (
        result.errors[0].code == ExtractionErrorCode.DOWNLOAD_FAILED
    )


@pytest.mark.anyio
async def test_orchestrator_uses_first_successful_candidate():
    source = make_source()

    document_source = FakeDocumentSource(
        {source.file_id: (source, b"content")},
    )

    failing_backend = FakeBackend(
        "failing",
        supports_extensions={".pdf"},
        result=ExtractionResult(status=ExtractionStatus.FAILED),
    )

    succeeding_backend = FakeBackend(
        "succeeding",
        supports_extensions={".pdf"},
        result=ExtractionResult(
            status=ExtractionStatus.SUCCESS,
            document=make_document(source, backend="succeeding"),
        ),
    )

    registry = ExtractionBackendRegistry(
        [failing_backend, succeeding_backend],
    )

    orchestrator = ExtractionOrchestrator(document_source, registry)

    result = await orchestrator.extract(source.file_id)

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document.extraction.backend == "succeeding"

    assert failing_backend.calls == 1
    assert succeeding_backend.calls == 1


@pytest.mark.anyio
async def test_orchestrator_moves_to_next_candidate_when_one_raises():
    source = make_source()

    document_source = FakeDocumentSource(
        {source.file_id: (source, b"content")},
    )

    raising_backend = FakeBackend(
        "raising",
        supports_extensions={".pdf"},
        raises=RuntimeError("boom"),
    )

    succeeding_backend = FakeBackend(
        "succeeding",
        supports_extensions={".pdf"},
        result=ExtractionResult(
            status=ExtractionStatus.SUCCESS,
            document=make_document(source, backend="succeeding"),
        ),
    )

    registry = ExtractionBackendRegistry(
        [raising_backend, succeeding_backend],
    )

    orchestrator = ExtractionOrchestrator(document_source, registry)

    result = await orchestrator.extract(source.file_id)

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document.extraction.backend == "succeeding"

    assert raising_backend.calls == 1
    assert succeeding_backend.calls == 1


@pytest.mark.anyio
async def test_orchestrator_fails_when_all_candidates_fail():
    source = make_source()

    document_source = FakeDocumentSource(
        {source.file_id: (source, b"content")},
    )

    backend_a = FakeBackend(
        "a",
        supports_extensions={".pdf"},
        result=ExtractionResult(
            status=ExtractionStatus.FAILED,
            errors=[
                ExtractionError(
                    code=ExtractionErrorCode.PARSING_FAILED,
                    message="backend a failed",
                    backend="a",
                )
            ],
        ),
    )

    backend_b = FakeBackend(
        "b",
        supports_extensions={".pdf"},
        raises=RuntimeError("boom"),
    )

    registry = ExtractionBackendRegistry([backend_a, backend_b])

    orchestrator = ExtractionOrchestrator(document_source, registry)

    result = await orchestrator.extract(source.file_id)

    assert result.status == ExtractionStatus.FAILED
    assert len(result.errors) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "filename, extension, expected_backend",
    [
        ("simple.pdf", ".pdf", "pymupdf"),
        ("simple.docx", ".docx", "docling"),
        ("simple.md", ".md", "docling"),
        ("simple.html", ".html", "docling"),
        ("simple.htm", ".htm", "docling"),
        ("simple.txt", ".txt", "plain_text"),
        ("simple.json", ".json", "json"),
        ("simple.yaml", ".yaml", "yaml"),
        ("simple.csv", ".csv", "csv"),
        ("simple.xml", ".xml", "xml"),
        ("simple.xlsx", ".xlsx", "xlsx"),
        ("simple.py", ".py", "source_code"),
        ("simple.css", ".css", "source_code"),
        ("simple.rst", ".rst", "rst"),
        ("simple.tex", ".tex", "docling"),
        ("simple.pptx", ".pptx", "docling"),
        ("simple.odt", ".odt", "docling"),
        ("simple.epub", ".epub", "docling"),
    ],
)
async def test_orchestrator_routes_real_fixtures_through_default_registry(
    filename: str,
    extension: str,
    expected_backend: str,
):
    fixture_path = FIXTURE_DIR / filename
    content_bytes = fixture_path.read_bytes()

    source = SourceFile(
        file_id=f"fixture-{extension.lstrip('.')}",
        filename=filename,
        extension=extension,
        size_bytes=len(content_bytes),
    )

    document_source = FakeDocumentSource(
        {source.file_id: (source, content_bytes)},
    )

    orchestrator = ExtractionOrchestrator(
        document_source,
        build_default_extraction_registry(),
    )

    result = await orchestrator.extract(
        source.file_id,
        options=ExtractionOptions(),
    )

    assert result.status == ExtractionStatus.SUCCESS
    assert result.document is not None
    assert result.document.extraction.backend == expected_backend
