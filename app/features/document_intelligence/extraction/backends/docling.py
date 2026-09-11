from __future__ import annotations

import io
import time
import zipfile
from typing import BinaryIO

from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling_core.types.doc import (
    CodeItem,
    DocItem,
    DocItemLabel,
    ListItem,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TitleItem,
)

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionErrorCode,
    ExtractionMethod,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    BoundingBox,
    CanonicalDocument,
    DocumentBlock,
    DocumentPage,
    ExtractionMetadata,
    ImageData,
    Provenance,
    SourceFile,
    TableCell,
    TableData,
)

from app.features.document_intelligence.extraction.interfaces import ExtractionBackend
from app.features.document_intelligence.extraction.limits import (
    ResourceLimitExceeded,
    check_zip_container,
)
from app.features.document_intelligence.extraction.schemas import ExtractionCapabilities, ExtractionOptions, ExtractionError, ExtractionResult


class DoclingBackend(ExtractionBackend):
    """
    Docling-based document extraction backend.

    This class is intentionally responsible only for translating
    Docling's representation into our canonical document model.
    """

    _EXTENSION_TO_INPUT_FORMAT = {
        ".pdf": InputFormat.PDF,
        ".docx": InputFormat.DOCX,
        ".md": InputFormat.MD,
        ".html": InputFormat.HTML,
        ".htm": InputFormat.HTML,
        ".tex": InputFormat.LATEX,
        ".pptx": InputFormat.PPTX,
        ".odt": InputFormat.ODT,
        ".epub": InputFormat.EPUB,
        ".png": InputFormat.IMAGE,
        ".jpg": InputFormat.IMAGE,
        ".jpeg": InputFormat.IMAGE,
        ".webp": InputFormat.IMAGE,
        ".bmp": InputFormat.IMAGE,
    }

    # .odt/.epub are zip containers; the upload size cap alone doesn't
    # protect against a zip-bomb-style file, same reasoning as .xlsx.
    _ZIP_CONTAINER_EXTENSIONS = {".odt", ".epub"}

    _SUPPORTED_EXTENSIONS = set(
        _EXTENSION_TO_INPUT_FORMAT.keys()
    )

    @property
    def name(self) -> str:
        return "docling"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={
                "application/pdf",
                (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "text/markdown",
                "text/html",
                "application/x-tex",
                (
                    "application/vnd.openxmlformats-officedocument."
                    "presentationml.presentation"
                ),
                "application/vnd.oasis.opendocument.text",
                "application/epub+zip",
                "image/png",
                "image/jpeg",
                "image/webp",
                "image/bmp",
            },
            supports_ocr=True,
            supports_tables=True,
            supports_images=True,
            supports_layout=True,
            supports_provenance=True,
        )

    def supports(self, source: SourceFile) -> bool:
        return (
            self._normalized_extension(source)
            in self._SUPPORTED_EXTENSIONS
        )

    async def extract(
        self,
        source: SourceFile,
        content: BinaryIO,
        options: ExtractionOptions,
    ) -> ExtractionResult:
        started_at = time.perf_counter()

        try:
            input_format = self._get_input_format(source)

            if input_format is None:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    errors=[
                        ExtractionError(
                            code=ExtractionErrorCode.UNSUPPORTED_FILE_TYPE,
                            message=(
                                f"Docling backend does not support "
                                f"'{source.filename}'."
                            ),
                            retryable=False,
                            backend=self.name,
                        )
                    ],
                )

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

            if (
                self._normalized_extension(source)
                in self._ZIP_CONTAINER_EXTENSIONS
            ):
                try:
                    check_zip_container(io.BytesIO(data))
                except zipfile.BadZipFile as exc:
                    return ExtractionResult(
                        status=ExtractionStatus.FAILED,
                        errors=[
                            ExtractionError(
                                code=ExtractionErrorCode.INVALID_CONTENT,
                                message=(
                                    f"Not a valid "
                                    f"'{source.extension}' file: {exc}"
                                ),
                                retryable=False,
                                backend=self.name,
                            )
                        ],
                    )
                except ResourceLimitExceeded as exc:
                    return ExtractionResult(
                        status=ExtractionStatus.FAILED,
                        errors=[
                            ExtractionError(
                                code=(
                                    ExtractionErrorCode
                                    .RESOURCE_LIMIT_EXCEEDED
                                ),
                                message=str(exc),
                                retryable=False,
                                backend=self.name,
                            )
                        ],
                    )

            if input_format == InputFormat.PDF:
                pipeline_options = PdfPipelineOptions(
                    do_ocr=options.enable_ocr,
                    do_table_structure=options.enable_tables,
                )

                converter = DocumentConverter(
                    allowed_formats=[input_format],
                    format_options={
                        InputFormat.PDF: PdfFormatOption(
                            pipeline_options=pipeline_options,
                        ),
                    },
                )
            else:
                converter = DocumentConverter(
                    allowed_formats=[input_format],
                )

            stream = self._build_document_stream(
                source=source,
                data=data,
            )

            result = converter.convert(
                stream,
                raises_on_error=False,
                max_num_pages=(
                    options.max_pages
                    if options.max_pages is not None
                    else 2**63 - 1
                ),
            )

            if result.document is None:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    errors=[
                        ExtractionError(
                            code=ExtractionErrorCode.PARSING_FAILED,
                            message=(
                                f"Docling could not produce a document "
                                f"for '{source.filename}'."
                            ),
                            retryable=False,
                            backend=self.name,
                        )
                    ],
                )

            document = self._build_canonical_document(
                source=source,
                docling_document=result.document,
                options=options,
                duration_ms=int(
                    (time.perf_counter() - started_at) * 1000
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
                        code=self._map_exception(exc),
                        message=(
                            f"Docling extraction failed for "
                            f"'{source.filename}'."
                        ),
                        retryable=self._is_retryable(exc),
                        backend=self.name,
                        details={
                            "exception_type": type(exc).__name__,
                            "exception": str(exc),
                        },
                    )
                ],
            )

    def _normalized_extension(
        self,
        source: SourceFile,
    ) -> str:
        extension = source.extension.lower()

        if not extension.startswith("."):
            extension = f".{extension}"

        return extension

    def _get_input_format(
        self,
        source: SourceFile,
    ) -> InputFormat | None:
        return self._EXTENSION_TO_INPUT_FORMAT.get(
            self._normalized_extension(source)
        )

    def _build_document_stream(
        self,
        source: SourceFile,
        data: bytes,
    ):
        from docling_core.types.io import DocumentStream

        return DocumentStream(
            name=source.filename,
            stream=io.BytesIO(data),
        )

    def _build_canonical_document(
        self,
        source: SourceFile,
        docling_document,
        options: ExtractionOptions,
        duration_ms: int,
    ) -> CanonicalDocument:
        blocks = self._build_blocks(docling_document)
        pages = self._build_pages(docling_document, blocks)

        extraction = ExtractionMetadata(
            backend=self.name,
            status=ExtractionStatus.SUCCESS,
            duration_ms=duration_ms,
            used_ocr=False,
            used_vision=False,
        )

        return CanonicalDocument(
            id=self._document_id(source),
            source=source,
            title=self._extract_title(
                blocks,
            ),
            language=None,
            pages=pages,
            blocks=blocks,
            metadata={
                "source_backend": self.name,
                "docling_document_name": getattr(
                    docling_document,
                    "name",
                    None,
                ),
            },
            extraction=extraction,
        )

    def _build_blocks(
        self,
        docling_document,
    ) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []

        for ordinal, (item, level) in enumerate(
            docling_document.iterate_items()
        ):
            if self._is_redundant_table_content(
                item, docling_document
            ):
                continue

            block = self._convert_item(
                item=item,
                docling_document=docling_document,
                ordinal=ordinal,
                level=level,
            )

            if block is not None:
                blocks.append(block)

        return blocks

    def _is_redundant_table_content(
        self,
        item,
        docling_document,
    ) -> bool:
        """Skip items whose content is already captured by a table.

        Some backends (confirmed for ODT) represent each table cell's
        content twice: once via TableData.table_cells (what
        _convert_table reads), and again as loose items parented under
        a "rich cell group" whose own parent is the TableItem. Without
        this check, cell text appears twice in the canonical output:
        once inside the TABLE block, once as duplicate top-level
        blocks.
        """

        parent_ref = getattr(item, "parent", None)

        if parent_ref is None:
            return False

        try:
            parent = parent_ref.resolve(docling_document)
        except Exception:
            return False

        if isinstance(parent, TableItem):
            return True

        grandparent_ref = getattr(parent, "parent", None)

        if grandparent_ref is None:
            return False

        try:
            grandparent = grandparent_ref.resolve(docling_document)
        except Exception:
            return False

        return isinstance(grandparent, TableItem)

    def _convert_item(
        self,
        item: DocItem,
        docling_document,
        ordinal: int,
        level: int | None = None,
    ) -> DocumentBlock | None:
        block_type = self._map_block_type(item)

        text = self._extract_text(item)

        table = None
        image = None

        if isinstance(item, TableItem):
            table = self._convert_table(item, docling_document)

        elif isinstance(item, PictureItem):
            image = self._convert_image(item)

        if (
            text is None
            and table is None
            and image is None
        ):
            return None

        return DocumentBlock(
            id=self._block_id(item, ordinal),
            type=block_type,
            text=text,
            table=table,
            image=image,
            parent_id=self._parent_id(item),
            children_ids=self._children_ids(item),
            heading_level=self._heading_level(item, level),
            section_path=[],
            ordinal=ordinal,
            provenance=self._convert_provenance(item),
            metadata={
                "docling_ref": getattr(
                    item,
                    "self_ref",
                    None,
                ),
                "docling_label": str(
                    getattr(item, "label", None)
                ),
            },
        )

    def _map_block_type(
        self,
        item: DocItem,
    ) -> BlockType:
        if isinstance(item, TitleItem):
            return BlockType.TITLE

        if isinstance(item, SectionHeaderItem):
            return BlockType.HEADING

        if isinstance(item, ListItem):
            return BlockType.LIST_ITEM

        if isinstance(item, CodeItem):
            return BlockType.CODE

        if isinstance(item, TableItem):
            return BlockType.TABLE

        if isinstance(item, PictureItem):
            return BlockType.IMAGE

        label = getattr(item, "label", None)

        if label in (DocItemLabel.TEXT, DocItemLabel.PARAGRAPH):
            return BlockType.PARAGRAPH

        if label == DocItemLabel.CAPTION:
            return BlockType.CAPTION

        if label == DocItemLabel.FOOTNOTE:
            return BlockType.FOOTNOTE

        if label == DocItemLabel.PAGE_HEADER:
            return BlockType.HEADER

        if label == DocItemLabel.PAGE_FOOTER:
            return BlockType.FOOTER

        return BlockType.OTHER

    def _extract_text(
        self,
        item: DocItem,
    ) -> str | None:
        text = getattr(item, "text", None)

        if text is None:
            return None

        text = text.strip()

        return text or None

    def _convert_table(
        self,
        item: TableItem,
        docling_document,
    ) -> TableData:
        data = item.data

        cells: list[TableCell] = []

        for cell in data.table_cells:
            cells.append(
                TableCell(
                    row=cell.start_row_offset_idx,
                    column=cell.start_col_offset_idx,
                    text=cell.text,
                    row_span=(
                        cell.end_row_offset_idx
                        - cell.start_row_offset_idx
                        + 1
                    ),
                    column_span=(
                        cell.end_col_offset_idx
                        - cell.start_col_offset_idx
                        + 1
                    ),
                    is_header=(
                        getattr(cell, "column_header", False)
                        or getattr(cell, "row_header", False)
                    ),
                    provenance=self._convert_provenance(cell),
                )
            )

        markdown = None

        try:
            markdown = item.export_to_markdown(docling_document)
        except Exception:
            pass

        return TableData(
            rows=data.num_rows,
            columns=data.num_cols,
            cells=cells,
            markdown=markdown,
        )

    def _convert_image(
        self,
        item: PictureItem,
    ) -> ImageData:
        image = getattr(item, "image", None)

        width = None
        height = None

        if image is not None:
            size = getattr(image, "size", None)

            if size is not None:
                width = int(size.width)
                height = int(size.height)

        return ImageData(
            width=width,
            height=height,
        )

    def _convert_provenance(
        self,
        item,
    ) -> list[Provenance]:
        result: list[Provenance] = []

        self_ref = getattr(item, "self_ref", None)

        for prov in getattr(item, "prov", []) or []:
            bbox = getattr(prov, "bbox", None)

            bounding_box = None

            if bbox is not None:
                bounding_box = BoundingBox(
                    x0=bbox.l,
                    y0=bbox.t,
                    x1=bbox.r,
                    y1=bbox.b,
                )

            result.append(
                Provenance(
                    page_number=getattr(
                        prov,
                        "page_no",
                        None,
                    ),
                    bbox=bounding_box,
                    source_element_id=self_ref,
                    extraction_method=ExtractionMethod.NATIVE,
                )
            )

        # Non-paginated formats (DOCX, Markdown, HTML) never populate
        # item.prov, since there is no page/bbox to report. They still
        # have a stable reference back to their source element, so trace
        # provenance through that instead of leaving it empty.
        if not result and self_ref:
            result.append(
                Provenance(
                    source_element_id=self_ref,
                    extraction_method=ExtractionMethod.NATIVE,
                )
            )

        return result

    def _build_pages(
        self,
        docling_document,
        blocks: list[DocumentBlock],
    ) -> list[DocumentPage]:
        pages_by_number: dict[int, list[str]] = {}

        for block in blocks:
            for provenance in block.provenance:
                if provenance.page_number is None:
                    continue

                pages_by_number.setdefault(
                    provenance.page_number,
                    [],
                ).append(block.id)

        pages: list[DocumentPage] = []

        for page_number in sorted(pages_by_number):
            page = docling_document.pages.get(page_number)

            width = None
            height = None

            if page is not None:
                size = getattr(page, "size", None)

                if size is not None:
                    width = getattr(size, "width", None)
                    height = getattr(size, "height", None)

            pages.append(
                DocumentPage(
                    page_number=page_number,
                    width=width,
                    height=height,
                    block_ids=pages_by_number[page_number],
                    page_ordinal=page_number,
                )
            )

        return pages

    def _extract_title(
        self,
        blocks: list[DocumentBlock],
    ) -> str | None:
        # Prefer an explicit document title.
        for block in blocks:
            if (
                block.type == BlockType.TITLE
                and block.text
            ):
                return block.text

        # Some formats, such as DOCX, may represent the document
        # title as the first level-1 heading.
        for block in blocks:
            if (
                block.type == BlockType.HEADING
                and block.heading_level == 1
                and block.text
            ):
                return block.text

        return None

    def _parent_id(
        self,
        item: DocItem,
    ) -> str | None:
        parent = getattr(item, "parent", None)

        if parent is None:
            return None

        return getattr(parent, "cref", None)

    def _children_ids(
        self,
        item: DocItem,
    ) -> list[str]:
        children = getattr(item, "children", None) or []

        return [
            getattr(child, "cref", str(child))
            for child in children
        ]

    def _heading_level(
        self,
        item: DocItem,
        level: int | None = None,
    ) -> int | None:
        if isinstance(item, TitleItem):
            return 1

        if isinstance(item, SectionHeaderItem):
            return getattr(item, "level", level)

        return None

    def _block_id(
        self,
        item: DocItem,
        ordinal: int,
    ) -> str:
        ref = getattr(item, "self_ref", None)

        if ref:
            return f"docling:{ref}"

        return f"docling:block:{ordinal}"

    def _document_id(
        self,
        source: SourceFile,
    ) -> str:
        if source.content_hash:
            return f"document:{source.content_hash}"

        return f"document:{source.file_id}"

    def _map_exception(
        self,
        exc: Exception,
    ) -> ExtractionErrorCode:
        name = type(exc).__name__.lower()
        message = str(exc).lower()

        if "password" in name or "password" in message:
            return ExtractionErrorCode.PASSWORD_PROTECTED

        if "ocr" in name or "ocr" in message:
            return ExtractionErrorCode.OCR_FAILED

        if "memory" in name or "limit" in message:
            return ExtractionErrorCode.RESOURCE_LIMIT_EXCEEDED

        if "invalid" in name or "corrupt" in message:
            return ExtractionErrorCode.INVALID_CONTENT

        return ExtractionErrorCode.PARSING_FAILED

    def _is_retryable(
        self,
        exc: Exception,
    ) -> bool:
        return False
