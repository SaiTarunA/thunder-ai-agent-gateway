from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import BinaryIO

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionErrorCode,
    ExtractionMethod,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    CanonicalDocument,
    DocumentBlock,
    ExtractionMetadata,
    Provenance,
    SourceFile,
)
from app.features.document_intelligence.extraction.interfaces import (
    ExtractionBackend,
)
from app.features.document_intelligence.extraction.limits import (
    MAX_STRUCTURED_NODES,
    ResourceLimitExceeded,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionError,
    ExtractionOptions,
    ExtractionResult,
)


class XmlBackend(ExtractionBackend):
    """Native extraction backend for arbitrary XML files.

    Uses defusedxml, NOT stdlib xml.etree.ElementTree directly. The
    stdlib parser is explicitly documented as unsafe against
    maliciously constructed data (XXE, entity-expansion "billion
    laughs"); defusedxml disables DTDs, entity expansion, and external
    resolution by default. Docling has no generic-XML support (only
    narrow dialects like JATS/USPTO), so there's no richer backend to
    consider here.

    Arbitrary XML schemas have no universal mapping to
    HEADING/PARAGRAPH/TABLE, so this validates and preserves the
    document as a single normalized CODE block for v1, same as
    JsonBackend/YamlBackend.
    """

    _SUPPORTED_EXTENSIONS = {".xml"}

    @property
    def name(self) -> str:
        return "xml"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"application/xml", "text/xml"},
            supports_ocr=False,
            supports_tables=False,
            supports_images=False,
            supports_layout=False,
            supports_provenance=True,
        )

    def supports(self, source: SourceFile) -> bool:
        return source.extension.lower() in self._SUPPORTED_EXTENSIONS

    async def extract(
        self,
        source: SourceFile,
        content: BinaryIO,
        options: ExtractionOptions,
    ) -> ExtractionResult:
        started_at = time.perf_counter()

        try:
            content.seek(0)
            data = content.read()

            if not data:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    errors=[
                        ExtractionError(
                            code=ExtractionErrorCode.INVALID_CONTENT,
                            message="The file is empty.",
                            retryable=False,
                            backend=self.name,
                        )
                    ],
                )

            root = DefusedET.fromstring(data)

            self._check_element_count(root)

            ET.indent(root)
            pretty = ET.tostring(root, encoding="unicode")

            block = DocumentBlock(
                id="xml:root",
                type=BlockType.CODE,
                text=pretty,
                ordinal=0,
                provenance=[
                    Provenance(
                        source_element_id="root",
                        extraction_method=ExtractionMethod.NATIVE,
                    )
                ],
                metadata={"language": "xml"},
            )

            document = CanonicalDocument(
                id=self._document_id(source),
                source=source,
                blocks=[block],
                metadata={"source_backend": self.name},
                extraction=ExtractionMetadata(
                    backend=self.name,
                    status=ExtractionStatus.SUCCESS,
                    duration_ms=int(
                        (time.perf_counter() - started_at) * 1000
                    ),
                    used_ocr=False,
                    used_vision=False,
                ),
            )

            return ExtractionResult(
                status=ExtractionStatus.SUCCESS,
                document=document,
            )

        except ResourceLimitExceeded as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        message=str(exc),
                        retryable=False,
                        backend=self.name,
                    )
                ],
            )
        except DefusedXmlException as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.INVALID_CONTENT,
                        message=(
                            f"Rejected an unsafe XML construct: {exc}"
                        ),
                        retryable=False,
                        backend=self.name,
                    )
                ],
            )
        except ET.ParseError as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.INVALID_CONTENT,
                        message=f"Invalid XML: {exc}",
                        retryable=False,
                        backend=self.name,
                    )
                ],
            )
        except Exception as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.PARSING_FAILED,
                        message=(
                            f"XML extraction failed for "
                            f"'{source.filename}'."
                        ),
                        retryable=False,
                        backend=self.name,
                        details={
                            "exception_type": type(exc).__name__,
                            "exception": str(exc),
                        },
                    )
                ],
            )

    def _check_element_count(
        self,
        root: ET.Element,
        *,
        limit: int = MAX_STRUCTURED_NODES,
    ) -> None:
        count = 0

        for _ in root.iter():
            count += 1

            if count > limit:
                raise ResourceLimitExceeded(
                    f"XML element count exceeds the limit of {limit}."
                )

    def _document_id(self, source: SourceFile) -> str:
        if source.content_hash:
            return f"document:{source.content_hash}"

        return f"document:{source.file_id}"
