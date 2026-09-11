from __future__ import annotations

import re
import zipfile
from typing import BinaryIO

MAX_ROWS = 500_000
MAX_COLUMNS = 2_000
MAX_CELLS = 5_000_000

MAX_WORKSHEETS = 200

MAX_TEXT_PARAGRAPHS = 200_000

# Post-parse sanity check for JSON/YAML. Sized generously so a legitimate,
# flat, ~25MB document doesn't trip it (e.g. a large flat array of small
# records can easily have hundreds of thousands of nodes).
MAX_STRUCTURED_NODES = 1_000_000

# Pre-parse heuristic for YAML anchor/alias density. This runs BEFORE
# yaml.safe_load(), unlike the node-count check above: a YAML anchor/alias
# ("billion laughs"-style) bomb does its damage *during* parsing, since
# safe_load fully materializes aliased content into real Python objects.
# A post-parse check would already be too late. This regex-based count is
# a heuristic, not a real YAML tokenizer, so it can both under- and
# over-count in unusual documents, but at this threshold false positives
# on ordinary config-shaped YAML are very unlikely.
MAX_YAML_ANCHORS_OR_ALIASES = 1_000

MAX_XLSX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 200

_YAML_ANCHOR_RE = re.compile(r"&\S+")
_YAML_ALIAS_RE = re.compile(r"\*\S+")


class ResourceLimitExceeded(Exception):
    """Raised when parsed content exceeds a configured resource limit."""


def check_table_dimensions(
    *,
    rows: int,
    columns: int,
    context: str,
) -> None:
    if rows > MAX_ROWS:
        raise ResourceLimitExceeded(
            f"{context}: row count {rows} exceeds the limit of {MAX_ROWS}."
        )

    if columns > MAX_COLUMNS:
        raise ResourceLimitExceeded(
            f"{context}: column count {columns} exceeds the limit of "
            f"{MAX_COLUMNS}."
        )

    if rows * columns > MAX_CELLS:
        raise ResourceLimitExceeded(
            f"{context}: cell count {rows * columns} exceeds the limit of "
            f"{MAX_CELLS}."
        )


def check_paragraph_count(count: int) -> None:
    if count > MAX_TEXT_PARAGRAPHS:
        raise ResourceLimitExceeded(
            f"Paragraph count {count} exceeds the limit of "
            f"{MAX_TEXT_PARAGRAPHS}."
        )


def check_worksheet_count(count: int) -> None:
    if count > MAX_WORKSHEETS:
        raise ResourceLimitExceeded(
            f"Worksheet count {count} exceeds the limit of "
            f"{MAX_WORKSHEETS}."
        )


def count_nodes(
    value: object,
    *,
    limit: int = MAX_STRUCTURED_NODES,
) -> int:
    """Count nodes in a parsed JSON/YAML structure.

    Aborts as soon as the count exceeds `limit`, rather than fully
    materializing a count for a pathologically large structure.
    """

    count = 0
    stack = [value]

    while stack:
        current = stack.pop()
        count += 1

        if count > limit:
            raise ResourceLimitExceeded(
                f"Parsed structure exceeds the node limit of {limit}."
            )

        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)

    return count


def check_yaml_alias_density(text: str) -> None:
    """Heuristic pre-parse guard against YAML anchor/alias expansion bombs.

    Must run before yaml.safe_load(), since the expansion this guards
    against happens during parsing, not after.
    """

    anchors = len(_YAML_ANCHOR_RE.findall(text))
    aliases = len(_YAML_ALIAS_RE.findall(text))

    if anchors + aliases > MAX_YAML_ANCHORS_OR_ALIASES:
        raise ResourceLimitExceeded(
            f"YAML document has an unusually high number of anchors/aliases "
            f"({anchors + aliases}), which can cause exponential expansion "
            f"during parsing; rejecting as a precaution."
        )


def check_zip_container(
    stream: BinaryIO,
    *,
    max_uncompressed_bytes: int = MAX_XLSX_UNCOMPRESSED_BYTES,
    max_ratio: int = MAX_XLSX_COMPRESSION_RATIO,
) -> None:
    """Guard against zip-bomb-style containers (e.g. a malicious .xlsx).

    `stream` must be seekable; its position is restored to 0 afterward.
    """

    stream.seek(0)

    try:
        with zipfile.ZipFile(stream) as archive:
            total_uncompressed = 0

            for info in archive.infolist():
                total_uncompressed += info.file_size

                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size

                    if ratio > max_ratio:
                        raise ResourceLimitExceeded(
                            f"Zip entry '{info.filename}' has a suspicious "
                            f"compression ratio ({ratio:.0f}x)."
                        )

            if total_uncompressed > max_uncompressed_bytes:
                raise ResourceLimitExceeded(
                    f"Zip container uncompressed size "
                    f"{total_uncompressed} exceeds the limit of "
                    f"{max_uncompressed_bytes}."
                )
    finally:
        stream.seek(0)
