from pydantic import BaseModel, Field
from app.features.document_intelligence.canonical.enums import ExtractionErrorCode, ExtractionMethod, BlockType, ExtractionStatus
from typing import Any, Literal


class BoundingBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    # Coordinate system dimensions allow us to normalize later.
    page_width: float | None = None
    page_height: float | None = None


class Provenance(BaseModel):
    page_number: int | None = None

    bbox: BoundingBox | None = None

    char_start: int | None = None
    char_end: int | None = None

    source_element_id: str | None = None

    extraction_method: ExtractionMethod = ExtractionMethod.NATIVE


class TableCell(BaseModel):
    row: int
    column: int

    text: str

    row_span: int = 1
    column_span: int = 1

    is_header: bool = False

    provenance: list[Provenance] = Field(default_factory=list)


class TableData(BaseModel):
    rows: int
    columns: int

    cells: list[TableCell]

    markdown: str | None = None
    html: str | None = None


class ImageData(BaseModel):
    asset_id: str | None = None

    mime_type: str | None = None

    width: int | None = None
    height: int | None = None

    alt_text: str | None = None

    ocr_text: str | None = None

    description: str | None = None

    caption: str | None = None


class DocumentBlock(BaseModel):
    id: str
    type: BlockType

    text: str | None = None
    table: TableData | None = None
    image: ImageData | None = None

    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)

    heading_level: int | None = None
    section_path: list[str] = Field(default_factory=list)

    ordinal: int

    provenance: list[Provenance] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPage(BaseModel):
    page_number: int

    width: float | None = None
    height: float | None = None

    block_ids: list[str] = Field(default_factory=list)

    page_ordinal: int | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceFile(BaseModel):
    file_id: str

    filename: str

    mime_type: str | None = None
    extension: str

    size_bytes: int | None = None

    content_hash: str | None = None


class ExtractionMetadata(BaseModel):
    backend: str

    backend_version: str | None = None

    status: ExtractionStatus

    duration_ms: int | None = None

    used_ocr: bool = False
    used_vision: bool = False

    warnings: list[str] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    id: str

    source: SourceFile

    title: str | None = None

    language: str | None = None

    pages: list[DocumentPage] = Field(default_factory=list)

    blocks: list[DocumentBlock] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)

    extraction: ExtractionMetadata

    schema_version: str = "1.0"


class Chunk(BaseModel):
    id: str

    document_id: str

    block_ids: list[str]

    text: str

    token_count: int

    section_path: list[str] = Field(default_factory=list)

    provenance: list[Provenance] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class IndexedChunk(Chunk):
    embedding_id: str | None = None

    embedding_model: str | None = None


class Citation(BaseModel):
    document_id: str
    file_id: str

    block_ids: list[str] = Field(default_factory=list)

    page_numbers: list[int] = Field(default_factory=list)

    snippet: str | None = None

    bounding_boxes: list[BoundingBox] = Field(default_factory=list)