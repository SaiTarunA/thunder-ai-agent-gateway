from __future__ import annotations

import asyncio
import gc
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import psutil

from app.features.document_intelligence.canonical.enums import (
    BlockType,
    ExtractionStatus,
)
from app.features.document_intelligence.canonical.schemas import (
    CanonicalDocument,
    SourceFile,
)
from app.features.document_intelligence.extraction.backends.docling import (
    DoclingBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)


BASE_DIR = Path(__file__).parent.parent
FIXTURE_DIR = BASE_DIR / "fixtures"
EXPECTED_PATH = FIXTURE_DIR / "expected.json"

FIXTURES = [
    FIXTURE_DIR / "simple.md",
    FIXTURE_DIR / "simple.docx",
    FIXTURE_DIR / "simple.pdf",
]


@dataclass
class PerformanceResult:
    elapsed_ms: float
    cpu_ms: float
    peak_rss_mb: float


@dataclass
class QualityResult:
    passed: bool
    title: str | None
    headings: int
    paragraphs: int
    list_items: int
    tables: int
    table_cells: int
    heading_levels_match: bool
    prose_match: bool
    errors: list[str]


class PeakMemoryMonitor:
    def __init__(
        self,
        process: psutil.Process,
        interval_seconds: float = 0.01,
    ):
        self.process = process
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self.peak_rss = 0

    def start(self) -> None:
        self.peak_rss = self.process.memory_info().rss

        self._thread = threading.Thread(
            target=self._monitor,
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> int:
        self._stop.set()
        self._thread.join()

        return self.peak_rss

    def _monitor(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self.process.memory_info().rss

                if rss > self.peak_rss:
                    self.peak_rss = rss

            except psutil.Error:
                break

            self._stop.wait(self.interval_seconds)


def load_expected() -> dict[str, Any]:
    with EXPECTED_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def create_source(path: Path) -> SourceFile:
    data = path.read_bytes()

    mime_types = {
        ".md": "text/markdown",
        ".docx": (
            "application/"
            "vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        ".pdf": "application/pdf",
    }

    return SourceFile(
        file_id=f"benchmark-{path.stem}",
        filename=path.name,
        mime_type=mime_types[path.suffix.lower()],
        extension=path.suffix.lower(),
        size_bytes=len(data),
        content_hash=f"benchmark-{path.stem}",
    )


def normalize_text(text: str) -> str:
    text = text.lower()

    # Normalize whitespace.
    text = re.sub(r"\s+", " ", text)

    # Normalize common bullet/list markers.
    text = re.sub(
        r"(^|\s)[•●▪◦]\s*",
        r"\1",
        text,
    )

    text = re.sub(
        r"(^|\s)\d+\.\s*",
        r"\1",
        text,
    )

    # Remove punctuation.
    text = re.sub(
        r"[^\w\s%\-]",
        "",
        text,
    )

    return text.strip()


def normalized_expected_prose(
    expected: dict[str, Any],
) -> list[str]:
    prose: list[str] = []

    for paragraph in expected["paragraphs"]:
        prose.append(normalize_text(paragraph))

    for list_data in expected["lists"]:
        for item in list_data["items"]:
            prose.append(normalize_text(item))

    return prose


def normalized_extracted_prose(
    document: CanonicalDocument,
) -> list[str]:
    prose: list[str] = []

    for block in document.blocks:
        if block.type not in {
            BlockType.PARAGRAPH,
            BlockType.LIST_ITEM,
        }:
            continue

        if not block.text:
            continue

        prose.append(normalize_text(block.text))

    return prose


def check_quality(
    document: CanonicalDocument,
    expected: dict[str, Any],
) -> QualityResult:
    errors: list[str] = []

    counts = expected["counts"]

    title_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.TITLE
    ]

    heading_blocks = [
        block
        for block in document.blocks
        if block.type in {
            BlockType.TITLE,
            BlockType.HEADING,
        }
    ]

    paragraph_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.PARAGRAPH
    ]

    list_item_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.LIST_ITEM
    ]

    table_blocks = [
        block
        for block in document.blocks
        if block.type == BlockType.TABLE
    ]

    table_cells = sum(
        len(block.table.cells)
        for block in table_blocks
        if block.table is not None
    )

    # ---------------------------------------------------------
    # Counts
    # ---------------------------------------------------------

    if len(heading_blocks) != counts["headings"]:
        errors.append(
            f"Expected {counts['headings']} headings, "
            f"got {len(heading_blocks)}."
        )

    if len(paragraph_blocks) != counts["paragraphs"]:
        errors.append(
            f"Expected {counts['paragraphs']} paragraphs, "
            f"got {len(paragraph_blocks)}."
        )

    if len(list_item_blocks) != counts["list_items"]:
        errors.append(
            f"Expected {counts['list_items']} list items, "
            f"got {len(list_item_blocks)}."
        )

    if len(table_blocks) != counts["tables"]:
        errors.append(
            f"Expected {counts['tables']} tables, "
            f"got {len(table_blocks)}."
        )

    if table_cells != counts["table_cells"]:
        errors.append(
            f"Expected {counts['table_cells']} table cells, "
            f"got {table_cells}."
        )

    # ---------------------------------------------------------
    # Title
    # ---------------------------------------------------------

    title = (
        title_blocks[0].text
        if title_blocks
        else None
    )

    if title != expected["title"]:
        errors.append(
            f"Expected title {expected['title']!r}, "
            f"got {title!r}."
        )

    # ---------------------------------------------------------
    # Heading hierarchy
    # ---------------------------------------------------------

    expected_headings = [
        (
            heading["level"],
            heading["text"],
        )
        for heading in expected["headings"]
    ]

    actual_headings = [
        (
            block.heading_level,
            block.text,
        )
        for block in heading_blocks
    ]

    heading_levels_match = (
        actual_headings == expected_headings
    )

    if not heading_levels_match:
        errors.append(
            "Heading hierarchy does not match.\n"
            f"Expected: {expected_headings!r}\n"
            f"Actual:   {actual_headings!r}"
        )

    # ---------------------------------------------------------
    # Prose
    # ---------------------------------------------------------

    expected_prose = normalized_expected_prose(expected)
    actual_prose = normalized_extracted_prose(document)

    prose_match = actual_prose == expected_prose

    if not prose_match:
        errors.append(
            "Normalized prose does not match.\n"
            f"Expected: {expected_prose!r}\n"
            f"Actual:   {actual_prose!r}"
        )

    return QualityResult(
        passed=not errors,
        title=title,
        headings=len(heading_blocks),
        paragraphs=len(paragraph_blocks),
        list_items=len(list_item_blocks),
        tables=len(table_blocks),
        table_cells=table_cells,
        heading_levels_match=heading_levels_match,
        prose_match=prose_match,
        errors=errors,
    )


async def extract_once(
    backend: DoclingBackend,
    source: SourceFile,
    data: bytes,
) -> tuple[Any, PerformanceResult]:
    process = psutil.Process(os.getpid())

    gc.collect()

    memory_before = process.memory_info().rss
    cpu_before = time.process_time()

    monitor = PeakMemoryMonitor(process)
    monitor.start()

    started = time.perf_counter()

    result = await backend.extract(
        source=source,
        content=BytesIO(data),
        options=ExtractionOptions(),
    )

    elapsed_ms = (
        time.perf_counter() - started
    ) * 1000

    cpu_ms = (
        time.process_time() - cpu_before
    ) * 1000

    peak_rss = monitor.stop()

    # Ensure the final RSS is at least represented.
    final_rss = process.memory_info().rss
    peak_rss = max(
        peak_rss,
        final_rss,
    )

    return result, PerformanceResult(
        elapsed_ms=elapsed_ms,
        cpu_ms=cpu_ms,
        peak_rss_mb=(
            peak_rss / (1024 * 1024)
        ),
    )


def print_quality(
    quality: QualityResult,
) -> None:
    print("Quality")
    print(
        f"  Title:            "
        f"{'PASS' if quality.title else 'FAIL'}"
    )
    print(
        f"  Headings:         {quality.headings}"
    )
    print(
        f"  Paragraphs:       {quality.paragraphs}"
    )
    print(
        f"  List items:       {quality.list_items}"
    )
    print(
        f"  Tables:           {quality.tables}"
    )
    print(
        f"  Table cells:      {quality.table_cells}"
    )
    print(
        f"  Heading levels:   "
        f"{'PASS' if quality.heading_levels_match else 'FAIL'}"
    )
    print(
        f"  Prose parity:     "
        f"{'PASS' if quality.prose_match else 'FAIL'}"
    )

    # List grouping isn't represented in the current
    # canonical model, so we explicitly don't score it.
    print(
        "  List grouping:    NOT MEASURED"
    )


def print_performance(
    label: str,
    performance: PerformanceResult,
) -> None:
    print(label)
    print(
        f"  Wall time:        "
        f"{performance.elapsed_ms:.2f} ms"
    )
    print(
        f"  CPU time:         "
        f"{performance.cpu_ms:.2f} ms"
    )
    print(
        f"  Peak RSS:         "
        f"{performance.peak_rss_mb:.2f} MB"
    )


async def benchmark_fixture(
    path: Path,
    expected: dict[str, Any],
) -> dict[str, Any]:
    data = path.read_bytes()
    source = create_source(path)

    backend = DoclingBackend()

    print("=" * 70)
    print(f"FORMAT: {path.suffix.upper()}")
    print("=" * 70)
    print(f"File:              {path.name}")
    print(
        f"File size:         "
        f"{len(data) / 1024:.2f} KB"
    )
    print()

    # ---------------------------------------------------------
    # First extraction
    # ---------------------------------------------------------

    result, first_performance = await extract_once(
        backend,
        source,
        data,
    )

    print_performance(
        "FIRST EXTRACTION",
        first_performance,
    )

    if result.status not in {
        ExtractionStatus.SUCCESS,
        ExtractionStatus.PARTIAL,
    }:
        print()
        print("EXTRACTION FAILED")

        for error in result.errors:
            print(
                f"  [{error.code.value}] "
                f"{error.message}"
            )

        return {
            "format": path.suffix,
            "passed": False,
        }

    assert result.document is not None

    quality = check_quality(
        result.document,
        expected,
    )

    print()
    print_quality(quality)

    # ---------------------------------------------------------
    # Warm runs
    # ---------------------------------------------------------

    warm_runs = 5
    warm_results: list[PerformanceResult] = []

    print()
    print(f"WARM RUNS ({warm_runs})")
    print("-" * 70)

    for index in range(warm_runs):
        result, performance = await extract_once(
            backend,
            source,
            data,
        )

        warm_results.append(performance)

        print(
            f"  Run {index + 1}: "
            f"{performance.elapsed_ms:.2f} ms "
            f"(CPU {performance.cpu_ms:.2f} ms, "
            f"Peak RSS {performance.peak_rss_mb:.2f} MB)"
        )

    average_wall = (
        sum(
            item.elapsed_ms
            for item in warm_results
        )
        / len(warm_results)
    )

    average_cpu = (
        sum(
            item.cpu_ms
            for item in warm_results
        )
        / len(warm_results)
    )

    peak_rss = max(
        item.peak_rss_mb
        for item in warm_results
    )

    print()
    print(
        f"  Average wall:     "
        f"{average_wall:.2f} ms"
    )
    print(
        f"  Average CPU:      "
        f"{average_cpu:.2f} ms"
    )
    print(
        f"  Peak RSS:         "
        f"{peak_rss:.2f} MB"
    )

    print()

    if quality.passed:
        print("OVERALL: PASS")
    else:
        print("OVERALL: FAIL")

        for error in quality.errors:
            print()
            print("ERROR:")
            print(error)

    return {
        "format": path.suffix,
        "file_size_bytes": len(data),
        "first_ms": first_performance.elapsed_ms,
        "warm_average_ms": average_wall,
        "warm_average_cpu_ms": average_cpu,
        "peak_rss_mb": peak_rss,
        "quality_passed": quality.passed,
    }


async def main() -> None:
    expected = load_expected()

    results = []

    for fixture in FIXTURES:
        if not fixture.exists():
            print(
                f"Missing fixture: {fixture}"
            )
            continue

        results.append(
            await benchmark_fixture(
                fixture,
                expected,
            )
        )

        print()

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"{'Format':<10}"
        f"{'First':>12}"
        f"{'Warm avg':>14}"
        f"{'CPU avg':>14}"
        f"{'Peak RSS':>14}"
        f"{'Quality':>12}"
    )

    print("-" * 70)

    for result in results:
        print(
            f"{result['format']:<10}"
            f"{result.get('first_ms', 0):>10.2f} ms"
            f"{result.get('warm_average_ms', 0):>12.2f} ms"
            f"{result.get('warm_average_cpu_ms', 0):>12.2f} ms"
            f"{result.get('peak_rss_mb', 0):>12.2f} MB"
            f"{'PASS' if result.get('quality_passed') else 'FAIL':>12}"
        )

    print("=" * 70)

# async def main2() -> None:
#     from docling.datamodel.pipeline_options import PdfPipelineOptions

#     options = PdfPipelineOptions()

#     print(options)


if __name__ == "__main__":
    asyncio.run(main())
    # asyncio.run(main2())