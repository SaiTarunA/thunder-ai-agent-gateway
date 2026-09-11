import asyncio
import time
from typing import BinaryIO

from app.features.document_intelligence.canonical.enums import (
    ExtractionErrorCode,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    SourceFile,
)
from app.features.document_intelligence.extraction.schemas import ExtractionError, ExtractionResult

from .interfaces import DocumentSource, ExtractionBackend
from .registry import ExtractionBackendRegistry
from .schemas import ExtractionOptions


class ExtractionOrchestrator:
    def __init__(
        self,
        source: DocumentSource,
        registry: ExtractionBackendRegistry,
    ):
        self._source = source
        self._registry = registry

    async def extract(
        self,
        file_id: str,
        options: ExtractionOptions | None = None,
    ) -> ExtractionResult:
        options = options or ExtractionOptions()

        # 1. Resolve file metadata.
        try:
            source = await self._source.get_metadata(file_id)
        except Exception as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.DOWNLOAD_FAILED,
                        message="Failed to retrieve file metadata.",
                        retryable=True,
                        details={
                            "exception_type": type(exc).__name__,
                        },
                    )
                ],
            )

        # 2. Find extraction candidates.
        candidates = self._registry.get_candidates(source)

        if not candidates:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.UNSUPPORTED_FILE_TYPE,
                        message=(
                            f"No extraction backend supports "
                            f"'{source.filename}'."
                        ),
                        retryable=False,
                    )
                ],
            )

        # 3. Download once.
        try:
            content = await self._source.download(source.file_id)
        except Exception as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.DOWNLOAD_FAILED,
                        message="Failed to download file content.",
                        retryable=True,
                        details={
                            "exception_type": type(exc).__name__,
                        },
                    )
                ],
            )

        # 4. Try candidate backends.
        return await self._try_candidates(
            source=source,
            content=content,
            candidates=candidates,
            options=options,
        )

    async def _try_candidates(
        self,
        source: SourceFile,
        content: BinaryIO,
        candidates: list[ExtractionBackend],
        options: ExtractionOptions,
    ) -> ExtractionResult:
        errors: list[ExtractionError] = []
        warnings: list[str] = []

        for backend in candidates:
            started_at = time.perf_counter()

            try:
                # Ensure every backend starts reading from the beginning.
                if hasattr(content, "seek"):
                    content.seek(0)

                # Note: this only protects backends that actually yield
                # control back to the event loop during extraction (e.g.
                # around I/O). Most current backends parse synchronously
                # within a single event-loop tick, which asyncio cannot
                # preempt once running — this is not a substitute for the
                # format-specific resource limits in extraction/limits.py,
                # which are the real defense against pathological input.
                result = await asyncio.wait_for(
                    backend.extract(
                        source=source,
                        content=content,
                        options=options,
                    ),
                    timeout=options.timeout_seconds,
                )

                duration_ms = int(
                    (time.perf_counter() - started_at) * 1000
                )

                if result.document is not None:
                    result.document.extraction.duration_ms = duration_ms

                if result.status in {
                    ExtractionStatus.SUCCESS,
                    ExtractionStatus.PARTIAL,
                }:
                    return result

                errors.extend(result.errors)
                warnings.extend(result.warnings)

            except asyncio.TimeoutError:
                duration_ms = int(
                    (time.perf_counter() - started_at) * 1000
                )

                errors.append(
                    ExtractionError(
                        code=ExtractionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        message=(
                            f"Extraction backend '{backend.name}' timed "
                            f"out after {options.timeout_seconds}s."
                        ),
                        retryable=True,
                        backend=backend.name,
                        details={"duration_ms": duration_ms},
                    )
                )

            except Exception as exc:
                duration_ms = int(
                    (time.perf_counter() - started_at) * 1000
                )

                errors.append(
                    ExtractionError(
                        code=ExtractionErrorCode.INTERNAL_ERROR,
                        message=(
                            f"Extraction backend '{backend.name}' failed."
                        ),
                        retryable=True,
                        backend=backend.name,
                        details={
                            "exception_type": type(exc).__name__,
                            "duration_ms": duration_ms,
                        },
                    )
                )

        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            errors=errors,
            warnings=warnings,
        )
