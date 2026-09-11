from __future__ import annotations

import gc
import os
import time
from io import BytesIO
from pathlib import Path

import psutil

from app.features.document_intelligence.canonical.schemas import SourceFile
from app.features.document_intelligence.extraction.backends.docling import (
    DoclingBackend,
)
from app.features.document_intelligence.extraction.schemas import (
    ExtractionOptions,
)


FIXTURE = b"""# Test Document

This is a paragraph containing enough text to make this a realistic
small document extraction test.

## Second Section

Another paragraph with some additional content.

## Third Section

More content for the benchmark.
"""


def memory_mb(process: psutil.Process) -> float:
    return process.memory_info().rss / (1024 * 1024)


def create_source() -> SourceFile:
    return SourceFile(
        file_id="benchmark-file",
        filename="benchmark.md",
        mime_type="text/markdown",
        extension=".md",
        size_bytes=len(FIXTURE),
        content_hash="benchmark-hash",
    )


def summarize_result(result) -> dict:
    document = result.document

    if document is None:
        return {
            "status": result.status.value,
            "blocks": 0,
            "pages": 0,
            "characters": 0,
        }

    characters = sum(
        len(block.text or "")
        for block in document.blocks
    )

    tables = sum(
        1
        for block in document.blocks
        if block.table is not None
    )

    images = sum(
        1
        for block in document.blocks
        if block.image is not None
    )

    return {
        "status": result.status.value,
        "blocks": len(document.blocks),
        "pages": len(document.pages),
        "characters": characters,
        "tables": tables,
        "images": images,
    }


async def run_benchmark() -> None:
    process = psutil.Process(os.getpid())

    print("=" * 70)
    print("DOCLING CPU BENCHMARK")
    print("=" * 70)

    print(f"Python PID: {process.pid}")
    print(f"Initial RSS: {memory_mb(process):.2f} MB")
    print()

    # ---------------------------------------------------------
    # Cold start
    # ---------------------------------------------------------

    gc.collect()

    before_memory = memory_mb(process)
    start = time.perf_counter()

    backend = DoclingBackend()

    initialization_ms = (
        time.perf_counter() - start
    ) * 1000

    after_memory = memory_mb(process)

    print("COLD START")
    print(f"  Initialization: {initialization_ms:.2f} ms")
    print(
        f"  Memory before:  {before_memory:.2f} MB"
    )
    print(
        f"  Memory after:   {after_memory:.2f} MB"
    )
    print(
        f"  Memory delta:   "
        f"{after_memory - before_memory:.2f} MB"
    )
    print()

    # ---------------------------------------------------------
    # Warm extraction
    # ---------------------------------------------------------

    source = create_source()
    options = ExtractionOptions()

    before_memory = memory_mb(process)

    start = time.perf_counter()

    result = await backend.extract(
        source=source,
        content=BytesIO(FIXTURE),
        options=options,
    )

    extraction_ms = (
        time.perf_counter() - start
    ) * 1000

    after_memory = memory_mb(process)

    print("WARM EXTRACTION")
    print(f"  Wall time:      {extraction_ms:.2f} ms")
    print(
        f"  Memory before:  {before_memory:.2f} MB"
    )
    print(
        f"  Memory after:   {after_memory:.2f} MB"
    )
    print(
        f"  Memory delta:   "
        f"{after_memory - before_memory:.2f} MB"
    )

    print()
    print("RESULT")
    print("-" * 70)

    for key, value in summarize_result(result).items():
        print(f"  {key}: {value}")

    print()

    # ---------------------------------------------------------
    # Repeated warm extraction
    # ---------------------------------------------------------

    runs = 5
    timings: list[float] = []

    print(f"WARM RUNS ({runs})")
    print("-" * 70)

    for index in range(runs):
        gc.collect()

        start = time.perf_counter()

        result = await backend.extract(
            source=source,
            content=BytesIO(FIXTURE),
            options=options,
        )

        elapsed_ms = (
            time.perf_counter() - start
        ) * 1000

        timings.append(elapsed_ms)

        print(
            f"  Run {index + 1}: "
            f"{elapsed_ms:.2f} ms"
        )

    average = sum(timings) / len(timings)
    minimum = min(timings)
    maximum = max(timings)

    print()
    print(f"  Average: {average:.2f} ms")
    print(f"  Minimum: {minimum:.2f} ms")
    print(f"  Maximum: {maximum:.2f} ms")

    print()
    print("=" * 70)


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_benchmark())
