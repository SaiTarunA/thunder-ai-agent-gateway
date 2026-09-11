from __future__ import annotations

import pytest

from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.csv_backend import (
    CsvBackend,
)
from app.features.document_intelligence.extraction.backends.docling import (
    DoclingBackend,
)
from app.features.document_intelligence.extraction.backends.json_backend import (
    JsonBackend,
)
from app.features.document_intelligence.extraction.backends.rst_backend import (
    RstBackend,
)
from app.features.document_intelligence.extraction.backends.source_code import (
    SourceCodeBackend,
)
from app.features.document_intelligence.extraction.backends.text import (
    PlainTextBackend,
)
from app.features.document_intelligence.extraction.backends.xlsx_backend import (
    XlsxBackend,
)
from app.features.document_intelligence.extraction.backends.xml_backend import (
    XmlBackend,
)
from app.features.document_intelligence.extraction.backends.yaml_backend import (
    YamlBackend,
)
from app.features.document_intelligence.extraction.factory import (
    build_default_extraction_registry,
)
from app.features.document_intelligence.extraction.pipelines.document import (
    DocumentExtractionPipeline,
)
from app.features.document_intelligence.extraction.pipelines.pdf import (
    PdfExtractionPipeline,
)


def make_source(extension: str) -> SourceFile:
    return SourceFile(
        file_id="test-file",
        filename=f"example{extension}",
        extension=extension,
        size_bytes=100,
    )


def test_default_registry_routes_pdf_to_pdf_pipeline_first():
    registry = build_default_extraction_registry()

    candidates = registry.get_candidates(make_source(".pdf"))

    assert len(candidates) >= 1
    assert isinstance(candidates[0], PdfExtractionPipeline)


def test_default_registry_routes_docx_md_html_to_document_pipeline_first():
    registry = build_default_extraction_registry()

    for extension in (
        ".docx", ".md", ".html", ".htm",
        ".tex", ".pptx", ".odt", ".epub",
    ):
        candidates = registry.get_candidates(make_source(extension))

        assert len(candidates) >= 1
        assert isinstance(candidates[0], DocumentExtractionPipeline)


def test_default_registry_routes_images_to_docling_backend():
    registry = build_default_extraction_registry()

    for extension in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        candidates = registry.get_candidates(make_source(extension))

        assert len(candidates) == 1
        assert isinstance(candidates[0], DoclingBackend)


def test_default_registry_has_no_candidates_for_unsupported_extension():
    registry = build_default_extraction_registry()

    candidates = registry.get_candidates(make_source(".unsupported"))

    assert candidates == []


@pytest.mark.parametrize(
    "extension, backend_cls",
    [
        (".txt", PlainTextBackend),
        (".json", JsonBackend),
        (".yaml", YamlBackend),
        (".yml", YamlBackend),
        (".csv", CsvBackend),
        (".xml", XmlBackend),
        (".xlsx", XlsxBackend),
        (".xlsm", XlsxBackend),
        (".py", SourceCodeBackend),
        (".sh", SourceCodeBackend),
        (".css", SourceCodeBackend),
        (".rst", RstBackend),
    ],
)
def test_default_registry_routes_native_formats(extension, backend_cls):
    registry = build_default_extraction_registry()

    candidates = registry.get_candidates(make_source(extension))

    assert len(candidates) == 1
    assert isinstance(candidates[0], backend_cls)
