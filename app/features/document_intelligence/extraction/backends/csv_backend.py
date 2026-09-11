from __future__ import annotations

import time
from io import BytesIO
from typing import BinaryIO

import pandas as pd

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
    TableCell,
    TableData,
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


class CsvBackend(ExtractionBackend):
    """Native extraction backend for CSV files.

    Uses pandas (already a project dependency) rather than Polars: at
    the platform's 25MB upload cap, Polars' multi-threaded-parsing
    performance advantage doesn't materialize, and pandas avoids adding
    a new dependency. Values are read as strings (dtype=str,
    keep_default_na=False) so text fidelity is preserved: "007" stays
    "007" rather than becoming 7, and empty cells stay distinguishable
    from an inferred null.
    """

    _SUPPORTED_EXTENSIONS = {".csv"}

    @property
    def name(self) -> str:
        return "csv"

    @property
    def capabilities(self) -> ExtractionCapabilities:
        return ExtractionCapabilities(
            supported_mime_types={"text/csv"},
            supports_ocr=False,
            supports_tables=True,
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

            try:
                df = pd.read_csv(
                    BytesIO(data),
                    dtype=str,
                    keep_default_na=False,
                )
            except pd.errors.EmptyDataError as exc:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    errors=[
                        ExtractionError(
                            code=ExtractionErrorCode.INVALID_CONTENT,
                            message=f"Empty or unparseable CSV: {exc}",
                            retryable=False,
                            backend=self.name,
                        )
                    ],
                )
            except pd.errors.ParserError as exc:
                return ExtractionResult(
                    status=ExtractionStatus.FAILED,
                    errors=[
                        ExtractionError(
                            code=ExtractionErrorCode.INVALID_CONTENT,
                            message=f"Malformed CSV: {exc}",
                            retryable=False,
                            backend=self.name,
                        )
                    ],
                )

            table = self._build_table(df)

            block = DocumentBlock(
                id="csv:table",
                type=BlockType.TABLE,
                table=table,
                ordinal=0,
                provenance=[
                    Provenance(
                        source_element_id="table",
                        extraction_method=ExtractionMethod.NATIVE,
                    )
                ],
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
        except Exception as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                errors=[
                    ExtractionError(
                        code=ExtractionErrorCode.PARSING_FAILED,
                        message=(
                            f"CSV extraction failed for "
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

    def _build_table(self, df: pd.DataFrame) -> TableData:
        columns = [str(column) for column in df.columns]
        num_columns = len(columns)
        num_rows = len(df) + 1

        check_table_dimensions(
            rows=num_rows,
            columns=num_columns,
            context="CSV",
        )

        cells: list[TableCell] = []

        for col_idx, column_name in enumerate(columns):
            cells.append(
                TableCell(
                    row=0,
                    column=col_idx,
                    text=column_name,
                    is_header=True,
                    provenance=[
                        Provenance(
                            source_element_id=f"header:{col_idx}",
                            extraction_method=ExtractionMethod.NATIVE,
                        )
                    ],
                )
            )

        for row_idx, row in enumerate(
            df.itertuples(index=False),
            start=1,
        ):
            for col_idx, value in enumerate(row):
                cells.append(
                    TableCell(
                        row=row_idx,
                        column=col_idx,
                        text="" if value is None else str(value),
                        provenance=[
                            Provenance(
                                source_element_id=(
                                    f"r{row_idx}c{col_idx}"
                                ),
                                extraction_method=(
                                    ExtractionMethod.NATIVE
                                ),
                            )
                        ],
                    )
                )

        return TableData(
            rows=num_rows,
            columns=num_columns,
            cells=cells,
        )

    def _document_id(self, source: SourceFile) -> str:
        if source.content_hash:
            return f"document:{source.content_hash}"

        return f"document:{source.file_id}"
