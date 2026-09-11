from __future__ import annotations

import time
from typing import BinaryIO

import yaml

try:
    from yaml import CSafeLoader as _SafeLoader
except ImportError:
    from yaml import SafeLoader as _SafeLoader

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
    ResourceLimitExceeded,
    check_yaml_alias_density,
    count_nodes,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionCapabilities,
    ExtractionError,
    ExtractionOptions,
    ExtractionResult,
)


class YamlBackend(ExtractionBackend):
    """Native extraction backend for YAML files.

    Uses the safe loader exclusively (never yaml.load()), which prevents
    construction of arbitrary Python objects from untrusted input. That
    does NOT protect against anchor/alias expansion ("billion laughs"
    style) attacks, since aliases are still fully resolved into real
    Python objects during a safe load. See
    limits.check_yaml_alias_density for the pre-parse mitigation.

    Same structural approach as JsonBackend: validate and preserve as a
    single normalized CODE block for v1, rather than inferring semantic
    structure from an arbitrary schema.
    """

    _SUPPORTED_EXTENSIONS = {".yaml", ".yml"}

    @property
    def name(self) -> str:
        return "yaml"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"application/x-yaml", "text/yaml"},
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

            text = data.decode("utf-8")

            # Must run before parsing: the expansion this guards against
            # happens during yaml.safe_load(), not after.
            check_yaml_alias_density(text)

            parsed = yaml.load(text, Loader=_SafeLoader)

            count_nodes(parsed)

            pretty = yaml.dump(
                parsed,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

            block = DocumentBlock(
                id="yaml:root",
                type=BlockType.CODE,
                text=pretty,
                ordinal=0,
                provenance=[
                    Provenance(
                        source_element_id="root",
                        extraction_method=ExtractionMethod.NATIVE,
                    )
                ],
                metadata={"language": "yaml"},
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
        except UnicodeDecodeError as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.INVALID_CONTENT,
                        message=f"Unable to decode YAML as UTF-8: {exc}",
                        retryable=False,
                        backend=self.name,
                    )
                ],
            )
        except yaml.YAMLError as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.INVALID_CONTENT,
                        message=f"Invalid YAML: {exc}",
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
                            f"YAML extraction failed for "
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
