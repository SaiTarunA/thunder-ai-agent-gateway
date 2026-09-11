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
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionError,
    ExtractionOptions,
    ExtractionResult,
)


class SourceCodeBackend(ExtractionBackend):
    """Native extraction backend for source code files (and CSS, which
    shares the exact same "language-tagged plain text" treatment).

    Deliberately simple for v1: no AST/syntax-aware parsing. Full
    syntax-aware extraction (imports/classes/functions/methods) was
    evaluated and explicitly deferred, since tree-sitter grammars have
    no shared node-type vocabulary across languages (e.g. Python's
    function_definition vs JavaScript's function_declaration vs an
    arrow function buried inside a lexical_declaration; Java has no
    top-level functions at all; Ruby's `require` is an ordinary method
    call rather than a distinct import construct). That amount of
    per-language design work is a separate future initiative, not part
    of adding source-code format support.

    Every supported extension shares this exact extraction logic and
    differs only in its language label, so one backend covers all of
    them rather than one class per language.

    No chunking is performed here: chunking is a distinct downstream
    concern already modeled separately in the schema (Chunk /
    IndexedChunk), not an extraction-layer responsibility. A file's
    entire source becomes a single CODE block, bounded only by the
    platform's upload size limit — unlike YAML/zip, plain source text
    has no amplification vector where decoded size could exceed the
    input size, so no extra resource limit is needed here.
    """

    _EXTENSION_TO_LANGUAGE = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".cs": "csharp",
        ".go": "go",
        ".php": "php",
        ".rb": "ruby",
        ".sh": "bash",
        ".css": "css",
    }

    @property
    def name(self) -> str:
        return "source_code"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={
                "text/x-python",
                "application/javascript",
                "application/typescript",
                "text/x-java-source",
                "text/x-c",
                "text/x-c++",
                "text/x-csharp",
                "text/x-go",
                "application/x-httpd-php",
                "application/x-ruby",
                "application/x-sh",
                "text/css",
            },
            supports_ocr=False,
            supports_tables=False,
            supports_images=False,
            supports_layout=False,
            supports_provenance=True,
        )

    def supports(self, source: SourceFile) -> bool:
        return (
            source.extension.lower()
            in self._EXTENSION_TO_LANGUAGE
        )

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
            language = self._EXTENSION_TO_LANGUAGE[
                source.extension.lower()
            ]

            block = DocumentBlock(
                id="code:root",
                type=BlockType.CODE,
                text=text,
                ordinal=0,
                provenance=[
                    Provenance(
                        source_element_id="root",
                        extraction_method=ExtractionMethod.NATIVE,
                    )
                ],
                metadata={"language": language},
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

        except Exception as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.PARSING_FAILED,
                        message=(
                            f"Source code extraction failed for "
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

    def _document_id(self, source: SourceFile) -> str:
        if source.content_hash:
            return f"document:{source.content_hash}"

        return f"document:{source.file_id}"
