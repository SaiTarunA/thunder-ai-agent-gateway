from enum import Enum


class BlockType(str, Enum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    IMAGE = "image"
    CAPTION = "caption"
    CODE = "code"
    QUOTE = "quote"
    FOOTNOTE = "footnote"
    HEADER = "header"
    FOOTER = "footer"
    OTHER = "other"


class ExtractionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ExtractionMethod(str, Enum):
    NATIVE = "native"
    OCR = "ocr"
    VISION = "vision"
    HYBRID = "hybrid"


class ExtractionErrorCode(str, Enum):
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    DOWNLOAD_FAILED = "download_failed"
    INVALID_CONTENT = "invalid_content"
    PARSING_FAILED = "parsing_failed"
    OCR_FAILED = "ocr_failed"
    PASSWORD_PROTECTED = "password_protected"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    INTERNAL_ERROR = "internal_error"

class ExtractionDecision(str, Enum):
    ACCEPT = "accept"
    FALLBACK = "fallback"