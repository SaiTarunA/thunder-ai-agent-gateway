from pydantic import BaseModel, Field
from typing import Any
from app.features.document_intelligence.canonical.enums import ExtractionDecision, ExtractionErrorCode, ExtractionStatus
from app.features.document_intelligence.canonical.schemas import CanonicalDocument


class ExtractionOptions(BaseModel):
    enable_ocr: bool = False

    enable_tables: bool = True

    enable_images: bool = True

    enable_image_description: bool = False

    enable_layout: bool = True

    max_pages: int | None = None

    timeout_seconds: int = 120


class ExtractionCapabilities(BaseModel):
    supported_mime_types: set[str]
    supports_ocr: bool = False
    supports_tables: bool = False
    supports_images: bool = False
    supports_layout: bool = False
    supports_provenance: bool = False


class ExtractionQuality(BaseModel):

    overall_score: float

    text_score: float | None = None

    structure_score: float | None = None

    table_score: float | None = None

    ocr_score: float | None = None

    provenance_score: float | None = None

    warnings: list[str] = Field(
        default_factory=list
    )


class ExtractionError(BaseModel):
    code: ExtractionErrorCode

    message: str

    retryable: bool = False

    backend: str | None = None

    details: dict[str, Any] = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    status: ExtractionStatus

    document: CanonicalDocument | None = None

    errors: list[ExtractionError] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)


class ExtractionPolicyResult(BaseModel):
    decision: ExtractionDecision

    reasons: list[str] = Field(
        default_factory=list,
    )
