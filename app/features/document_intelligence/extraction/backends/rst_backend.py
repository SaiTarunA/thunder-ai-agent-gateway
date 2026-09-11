from __future__ import annotations

import io
import time
from typing import BinaryIO

from docutils import nodes
from docutils.core import publish_doctree

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
    ImageData,
    Provenance,
    SourceFile,
    TableCell,
    TableData,
)
from app.features.document_intelligence.extraction.encoding import (
    decode_text,
)
from app.features.document_intelligence.extraction.interfaces import (
    ExtractionBackend,
)
from app.features.document_intelligence.extraction.limits import (
    ResourceLimitExceeded,
    check_table_dimensions,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionError,
    ExtractionOptions,
    ExtractionResult,
)

# Hardened for untrusted input, per docutils' own security guidance
# (https://docutils.sourceforge.io/docs/howto/security.html):
#
# - file_insertion_enabled=False / raw_enabled=False block the
#   ".. include::" and ".. raw::" directives, which otherwise allow
#   arbitrary local file disclosure and raw HTML/script injection.
# - _disable_config=True is not optional. Verified empirically: without
#   it, a docutils.conf file in the process's working directory can
#   silently override file_insertion_enabled/raw_enabled back to
#   enabled, completely bypassing the settings above.
# - report_level/halt_level are set to the maximum so a malformed or
#   adversarial document degrades to a system_message node in the tree
#   rather than raising, consistent with treating syntax errors as
#   recoverable rather than fatal.
_SETTINGS_OVERRIDES = {
    "file_insertion_enabled": False,
    "raw_enabled": False,
    "_disable_config": True,
    "report_level": 5,
    "halt_level": 5,
}


class RstBackend(ExtractionBackend):
    """Native extraction backend for reStructuredText files.

    Docling has no RST support (only two narrow XML dialects), so this
    parses with docutils directly into its doctree, then walks that
    tree into the canonical schema: titles/sections become
    TITLE/HEADING (nesting depth becomes heading_level), paragraphs and
    list items map directly, literal blocks become CODE, tables are
    walked into TableData, and images become IMAGE blocks carrying only
    alt text/URI metadata — no image content is fetched or resolved,
    consistent with image work being paused elsewhere in this module.
    """

    _SUPPORTED_EXTENSIONS = {".rst"}

    @property
    def name(self) -> str:
        return "rst"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"text/x-rst"},
            supports_ocr=False,
            supports_tables=True,
            supports_images=False,
            supports_layout=True,
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

            try:
                doctree = publish_doctree(
                    text,
                    settings_overrides={
                        **_SETTINGS_OVERRIDES,
                        "warning_stream": io.StringIO(),
                    },
                )
            except RecursionError:
                raise ResourceLimitExceeded(
                    "RST document is nested too deeply to parse safely."
                )

            blocks = self._build_blocks(doctree)

            document = CanonicalDocument(
                id=self._document_id(source),
                source=source,
                title=self._extract_title(blocks),
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
                            f"RST extraction failed for "
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

    def _build_blocks(
        self,
        doctree: nodes.document,
    ) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        ordinal = [0]

        try:
            self._walk(doctree, depth=0, blocks=blocks, ordinal=ordinal)
        except RecursionError:
            raise ResourceLimitExceeded(
                "RST document structure is nested too deeply to walk "
                "safely."
            )

        return blocks

    def _walk(
        self,
        container: nodes.Element,
        depth: int,
        blocks: list[DocumentBlock],
        ordinal: list[int],
    ) -> None:
        for child in container.children:
            if isinstance(child, (nodes.system_message, nodes.comment)):
                continue

            if isinstance(child, nodes.section):
                self._walk(child, depth + 1, blocks, ordinal)
                continue

            if isinstance(child, nodes.title):
                text = self._normalize_text(child.astext())

                if text:
                    is_document_title = depth == 0

                    blocks.append(
                        DocumentBlock(
                            id=f"rst:block:{ordinal[0]}",
                            type=(
                                BlockType.TITLE
                                if is_document_title
                                else BlockType.HEADING
                            ),
                            text=text,
                            heading_level=(
                                None if is_document_title else depth
                            ),
                            ordinal=ordinal[0],
                            provenance=self._provenance(ordinal[0]),
                        )
                    )
                    ordinal[0] += 1

                continue

            if isinstance(child, nodes.paragraph):
                text = self._normalize_text(child.astext())

                if text:
                    blocks.append(
                        DocumentBlock(
                            id=f"rst:block:{ordinal[0]}",
                            type=BlockType.PARAGRAPH,
                            text=text,
                            ordinal=ordinal[0],
                            provenance=self._provenance(ordinal[0]),
                        )
                    )
                    ordinal[0] += 1

                continue

            if isinstance(
                child,
                (nodes.bullet_list, nodes.enumerated_list),
            ):
                for item in child.children:
                    text = self._normalize_text(item.astext())

                    if text:
                        blocks.append(
                            DocumentBlock(
                                id=f"rst:block:{ordinal[0]}",
                                type=BlockType.LIST_ITEM,
                                text=text,
                                ordinal=ordinal[0],
                                provenance=self._provenance(ordinal[0]),
                            )
                        )
                        ordinal[0] += 1

                continue

            if isinstance(child, nodes.literal_block):
                text = child.astext()

                if text.strip():
                    blocks.append(
                        DocumentBlock(
                            id=f"rst:block:{ordinal[0]}",
                            type=BlockType.CODE,
                            text=text,
                            ordinal=ordinal[0],
                            provenance=self._provenance(ordinal[0]),
                        )
                    )
                    ordinal[0] += 1

                continue

            if isinstance(child, nodes.table):
                table = self._convert_table(child)

                if table is not None:
                    blocks.append(
                        DocumentBlock(
                            id=f"rst:block:{ordinal[0]}",
                            type=BlockType.TABLE,
                            table=table,
                            ordinal=ordinal[0],
                            provenance=self._provenance(ordinal[0]),
                        )
                    )
                    ordinal[0] += 1

                continue

            if isinstance(child, nodes.image):
                blocks.append(
                    DocumentBlock(
                        id=f"rst:block:{ordinal[0]}",
                        type=BlockType.IMAGE,
                        image=ImageData(
                            alt_text=child.get("alt"),
                        ),
                        ordinal=ordinal[0],
                        provenance=self._provenance(ordinal[0]),
                        metadata={"uri": child.get("uri")},
                    )
                )
                ordinal[0] += 1
                continue

            if isinstance(child, nodes.Element):
                # Unrecognized container (block_quote, admonition,
                # docinfo, etc.) - recurse best-effort at the same
                # depth rather than silently dropping nested content.
                self._walk(child, depth, blocks, ordinal)

    def _convert_table(
        self,
        table_node: nodes.table,
    ) -> TableData | None:
        tgroup = next(
            (
                child
                for child in table_node.children
                if isinstance(child, nodes.tgroup)
            ),
            None,
        )

        if tgroup is None:
            return None

        num_columns = sum(
            1
            for child in tgroup.children
            if isinstance(child, nodes.colspec)
        )

        thead = next(
            (
                child
                for child in tgroup.children
                if isinstance(child, nodes.thead)
            ),
            None,
        )
        tbody = next(
            (
                child
                for child in tgroup.children
                if isinstance(child, nodes.tbody)
            ),
            None,
        )

        cells: list[TableCell] = []
        row_index = 0

        for section, is_header in ((thead, True), (tbody, False)):
            if section is None:
                continue

            for row in section.children:
                for col_index, entry in enumerate(row.children):
                    cells.append(
                        TableCell(
                            row=row_index,
                            column=col_index,
                            text=self._normalize_text(entry.astext()),
                            is_header=is_header,
                            provenance=self._provenance(row_index),
                        )
                    )

                row_index += 1

        if row_index == 0 or num_columns == 0:
            return None

        check_table_dimensions(
            rows=row_index,
            columns=num_columns,
            context="RST table",
        )

        return TableData(
            rows=row_index,
            columns=num_columns,
            cells=cells,
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Collapse the source's line-wrapping into single spaces.

        A hard line break inside an RST paragraph/title/list-item is
        the author's line-wrapping style, not real structure - other
        backends in this module (e.g. PyMuPDFBackend) normalize the
        same way. literal_block/CODE text is handled separately and
        kept verbatim.
        """

        return " ".join(text.split())

    def _extract_title(
        self,
        blocks: list[DocumentBlock],
    ) -> str | None:
        for block in blocks:
            if block.type == BlockType.TITLE and block.text:
                return block.text

        return None

    def _provenance(self, ordinal: int) -> list[Provenance]:
        return [
            Provenance(
                source_element_id=f"block:{ordinal}",
                extraction_method=ExtractionMethod.NATIVE,
            )
        ]

    def _document_id(self, source: SourceFile) -> str:
        if source.content_hash:
            return f"document:{source.content_hash}"

        return f"document:{source.file_id}"
