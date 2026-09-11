from __future__ import annotations

import time
from typing import BinaryIO

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
from app.features.document_intelligence.extraction.encoding import (
    decode_text,
)
from app.features.document_intelligence.extraction.interfaces import (
    ExtractionBackend,
)
from app.features.document_intelligence.extraction.limits import (
    ResourceLimitExceeded,
    check_paragraph_count,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionError,
    ExtractionOptions,
    ExtractionResult,
)


class PlainTextBackend(ExtractionBackend):
    """Native extraction backend for plain text files.

    No third-party document library applies here; the only real problem
    is character encoding. UTF-8 is tried first, since it's the common
    case and doesn't require pulling in a detection library. For
    anything else, charset-normalizer identifies the actual encoding
    rather than guessing or failing outright.
    """

    _SUPPORTED_EXTENSIONS = {".txt"}

    @property
    def name(self) -> str:
        return "plain_text"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"text/plain"},
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

            text = decode_text(data)

            blocks = self._build_blocks(text)

            document = CanonicalDocument(
                id=self._document_id(source),
                source=source,
                blocks=blocks,
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
        except Exception as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.PARSING_FAILED,
                        message=(
                            f"Text extraction failed for "
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

    def _build_blocks(self, text: str) -> list[DocumentBlock]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")

        paragraphs = [
            paragraph.strip()
            for paragraph in normalized.split("\n\n")
        ]
        paragraphs = [
            paragraph
            for paragraph in paragraphs
            if paragraph
        ]

        check_paragraph_count(len(paragraphs))

        blocks: list[DocumentBlock] = []

        for ordinal, paragraph in enumerate(paragraphs):
            blocks.append(
                DocumentBlock(
                    id=f"txt:paragraph:{ordinal}",
                    type=BlockType.PARAGRAPH,
                    text=paragraph,
                    ordinal=ordinal,
                    provenance=[
                        Provenance(
                            source_element_id=f"paragraph:{ordinal}",
                            extraction_method=ExtractionMethod.NATIVE,
                        )
                    ],
                )
            )

        return blocks

    def _document_id(self, source: SourceFile) -> str:
        if source.content_hash:
            return f"document:{source.content_hash}"

        return f"document:{source.file_id}"
