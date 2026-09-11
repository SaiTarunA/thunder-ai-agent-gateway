from __future__ import annotations

import io
import time

import pymupdf

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionMethod,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    BoundingBox,
    CanonicalDocument,
    DocumentBlock,
    DocumentPage,
    ExtractionMetadata,
    Provenance,
    SourceFile,
)
from app.features.document_intelligence.extraction.interfaces import (
    ExtractionBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionOptions,
    ExtractionResult,
)


class PyMuPDFBackend(ExtractionBackend):
    """Lightweight native PDF extraction using PyMuPDF.

    This backend intentionally does not perform:
    - OCR
    - semantic table recognition
    - image understanding
    - layout-model inference

    It is intended to be a cheap first-pass PDF extractor.
    """

    _SUPPORTED_EXTENSIONS = {".pdf"}

    @property
    def name(self) -> str:
        return "pymupdf"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"application/pdf"},
            supports_ocr=False,
            supports_tables=False,
            supports_images=False,
            supports_layout=True,
            supports_provenance=True,
        )

    def supports(self, source: SourceFile) -> bool:
        return source.extension.lower() in self._SUPPORTED_EXTENSIONS

    async def extract(
        self,
        source: SourceFile,
        content: io.BufferedIOBase,
        options: ExtractionOptions,
    ) -> ExtractionResult:
        started_at = time.perf_counter()

        try:
            content.seek(0)
            data = content.read()

            if not data:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    errors=[],
                    warnings=["PDF content is empty."],
                )

            document = pymupdf.open(
                stream=data,
                filetype="pdf",
            )

            try:
                canonical = self._build_canonical_document(
                    source=source,
                    pdf=document,
                    started_at=started_at,
                    max_pages=options.max_pages,
                )
            finally:
                document.close()

            return ExtractionResult(
                status=ExtractionStatus.SUCCESS,
                document=canonical,
            )

        except Exception as exc:
            duration_ms = int(
                (time.perf_counter() - started_at) * 1000
            )

            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    {
                        "code": "parsing_failed",
                        "message": str(exc),
                        "retryable": False,
                        "backend": self.name,
                    }
                ],
                warnings=[
                    f"PyMuPDF extraction failed after "
                    f"{duration_ms} ms."
                ],
            )

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize extracted PDF text."""

        return " ".join(text.split())

    @staticmethod
    def _is_list_item(text: str) -> bool:
        """Detect basic list-item markers."""

        stripped = text.strip()

        if stripped.startswith(("•", "-", "*")):
            return True

        if len(stripped) >= 2:
            if (
                stripped[0].isdigit()
                and stripped[1] in {".", ")"}
            ):
                return True

        return False

    @staticmethod
    def _get_block_font_size(
        page: pymupdf.Page,
        block_index: int,
    ) -> float | None:
        """Return the largest font size found in a text block.

        PyMuPDF's ``get_text("blocks")`` does not expose font sizes,
        so we inspect the corresponding block through the dictionary
        representation.
        """

        text_dict = page.get_text("dict")

        blocks = text_dict.get("blocks", [])

        if block_index >= len(blocks):
            return None

        block = blocks[block_index]

        if block.get("type") != 0:
            return None

        font_sizes: list[float] = []

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size")

                if size is not None:
                    font_sizes.append(float(size))

        if not font_sizes:
            return None

        return max(font_sizes)

    @staticmethod
    def _build_heading_levels(
        font_sizes: list[float],
    ) -> dict[float, int]:
        """Map larger font sizes to lower heading levels.

        Example:

            [19.0, 13.5, 11.5, 10.0]

        becomes approximately:

            {
                19.0: 1,
                13.5: 2,
                11.5: 3,
            }

        The smallest/common body font size is excluded.
        """

        unique_sizes = sorted(
            set(font_sizes),
            reverse=True,
        )

        if len(unique_sizes) <= 1:
            return {}

        # Assume the smallest font size represents normal body text.
        heading_sizes = unique_sizes[:-1]

        heading_levels: dict[float, int] = {}

        for level, font_size in enumerate(
            heading_sizes,
            start=1,
        ):
            heading_levels[font_size] = level

        return heading_levels

    def _detect_block_type(
        self,
        text: str,
        font_size: float | None,
        heading_levels: dict[float, int],
    ) -> tuple[BlockType, int | None]:
        """Detect the canonical block type.

        Detection order:

        1. List item
        2. Heading based on font size
        3. Paragraph
        """

        if self._is_list_item(text):
            return BlockType.LIST_ITEM, None

        if font_size is not None:
            heading_level = heading_levels.get(font_size)

            if heading_level is not None:
                return BlockType.HEADING, heading_level

        return BlockType.PARAGRAPH, None

    @staticmethod
    def _detect_document_title(
        blocks: list[DocumentBlock],
    ) -> str | None:
        """Detect a likely document title.

        Prefer the first level-1 heading. If no level-1 heading exists,
        fall back to the previous lightweight heuristic.
        """

        for block in blocks:
            if (
                block.type == BlockType.HEADING
                and block.heading_level == 1
            ):
                return block.text

        for block in blocks[:5]:
            text = block.text.strip()

            if not text:
                continue

            if block.type in {
                BlockType.LIST_ITEM,
                BlockType.TABLE,
            }:
                continue

            if len(text) > 200:
                continue

            if text.startswith(("•", "-", "*")):
                continue

            return text

        return None

    def _build_canonical_document(
        self,
        source: SourceFile,
        pdf: pymupdf.Document,
        started_at: float,
        max_pages: int | None,
    ) -> CanonicalDocument:
        blocks: list[DocumentBlock] = []
        pages: list[DocumentPage] = []

        page_count = len(pdf)

        if max_pages is not None:
            page_count = min(
                page_count,
                max_pages,
            )

        #
        # STEP 1:
        # Collect all font sizes used by text blocks.
        #
        # We do this first so we can determine which font sizes
        # represent heading levels before building DocumentBlocks.
        #
        font_sizes: list[float] = []

        for page_index in range(page_count):
            page = pdf.load_page(page_index)

            text_blocks = page.get_text("blocks")

            for block_index, block in enumerate(text_blocks):
                if len(block) < 5:
                    continue

                font_size = self._get_block_font_size(
                    page=page,
                    block_index=block_index,
                )

                if font_size is not None:
                    font_sizes.append(font_size)

        #
        # STEP 2:
        # Build a font-size -> heading-level mapping.
        #
        heading_levels = self._build_heading_levels(
            font_sizes=font_sizes,
        )

        ordinal = 0

        #
        # STEP 3:
        # Extract the actual canonical document blocks.
        #
        for page_index in range(page_count):
            page = pdf.load_page(page_index)

            rect = page.rect

            page_block_ids: list[str] = []

            text_blocks = page.get_text("blocks")

            for block_index, block in enumerate(text_blocks):
                if len(block) < 5:
                    continue

                x0, y0, x1, y1, text = block[:5]

                text = self._normalize_text(text)

                if not text:
                    continue

                font_size = self._get_block_font_size(
                    page=page,
                    block_index=block_index,
                )

                block_type, heading_level = self._detect_block_type(
                    text=text,
                    font_size=font_size,
                    heading_levels=heading_levels,
                )

                if (
                    block_type == BlockType.HEADING
                    and heading_level == 1
                    and not any(block.type == BlockType.TITLE for block in blocks)
                ):
                    block_type = BlockType.TITLE
                    heading_level = None

                block_id = (
                    f"{source.file_id}:"
                    f"page-{page_index + 1}:"
                    f"block-{block_index}"
                )

                bbox = BoundingBox(
                    x0=float(x0),
                    y0=float(y0),
                    x1=float(x1),
                    y1=float(y1),
                    page_width=float(rect.width),
                    page_height=float(rect.height),
                )

                provenance = Provenance(
                    page_number=page_index + 1,
                    bbox=bbox,
                    extraction_method=ExtractionMethod.NATIVE,
                )

                document_block = DocumentBlock(
                    id=block_id,
                    type=block_type,
                    text=text,
                    parent_id=None,
                    children_ids=[],
                    heading_level=heading_level,
                    section_path=[],
                    ordinal=ordinal,
                    provenance=[provenance],
                    metadata={
                        "page_block_index": block_index,
                        "font_size": font_size,
                    },
                )

                blocks.append(document_block)

                page_block_ids.append(block_id)

                ordinal += 1

            pages.append(
                DocumentPage(
                    page_number=page_index + 1,
                    width=float(rect.width),
                    height=float(rect.height),
                    block_ids=page_block_ids,
                    page_ordinal=page_index,
                )
            )

        duration_ms = int(
            (time.perf_counter() - started_at) * 1000
        )

        document_title = self._detect_document_title(
            blocks
        )

        return CanonicalDocument(
            id=f"{source.file_id}:pymupdf",
            source=source,
            title=document_title,
            language=None,
            pages=pages,
            blocks=blocks,
            metadata={
                "page_count": page_count,
                "source_page_count": len(pdf),
            },
            extraction=ExtractionMetadata(
                backend=self.name,
                backend_version=pymupdf.VersionBind,
                status=ExtractionStatus.SUCCESS,
                duration_ms=duration_ms,
                used_ocr=False,
                used_vision=False,
                warnings=[],
            ),
        )