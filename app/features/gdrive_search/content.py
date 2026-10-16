"""Fetching the text of candidate files, for reranking and answering, and
the files themselves when a scan or photo has no text to extract.

Content is cached, not indexed: a file downloaded for one question stays
parsed for the next, keyed by file id and content checksum so an edit
invalidates it, and nothing is read ahead of a question being asked. Only
text and failures are cached, both small.

A downloaded file goes to document_intelligence's extraction orchestrator as
a DriveFileSource. The registry here is built for reranking, which needs the
words of up to twenty files within seconds: fast native readers only, no
OCR, no Docling fallback. Extraction is CPU-bound, so it runs in a worker
thread to keep the event loop free for other requests.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from io import BytesIO
from typing import BinaryIO, Optional

from app.features.document_intelligence.canonical.schemas import CanonicalDocument, SourceFile
from app.features.document_intelligence.extraction.backends.office_text import OfficeTextBackend
from app.features.document_intelligence.extraction.backends.pymupdf import PyMuPDFBackend
from app.features.document_intelligence.extraction.backends.text import PlainTextBackend
from app.features.document_intelligence.extraction.interfaces import DocumentSource
from app.features.document_intelligence.extraction.orchestrator import ExtractionOrchestrator
from app.features.document_intelligence.extraction.registry import ExtractionBackendRegistry
from app.features.document_intelligence.extraction.schemas import ExtractionOptions
from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import DriveAuthRequired, DriveContext, cache_redis, drive_client
from app.features.gdrive_search.names import FileRef
from app.integrations.google_drive.client import FileTooLarge

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Reading files through document_intelligence
# ---------------------------------------------------------------------


GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
PDF = "application/pdf"

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Google Workspace files have no bytes of their own; they are exported. A
# Sheet goes as xlsx, which holds every tab: CSV holds only the first.
EXPORT_FORMATS = {
    GOOGLE_DOC: "text/plain",
    GOOGLE_SHEET: XLSX,
    GOOGLE_SLIDES: "text/plain",
}

# Read as rows under a header: a passage cut from the middle keeps it.
_TABLE_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".ods"}
_TABLE_TYPES = {GOOGLE_SHEET, "text/csv", "text/tab-separated-values"}

# MIME type -> the extension the extraction registry routes on.
_EXTENSIONS = {
    PDF: ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    XLSX: ".xlsx",
    "application/vnd.ms-excel.sheet.macroEnabled.12": ".xlsm",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "message/rfc822": ".eml",
}


def extension_for(mime_type: str) -> Optional[str]:
    """The extension a file is extracted as, or None when nothing reads it.

    Exported Workspace files and every other text/* type (source code, CSV,
    Markdown) are read as plain text: listing text types one by one missed
    every script format.
    """
    if mime_type in _EXTENSIONS:
        return _EXTENSIONS[mime_type]
    if mime_type in EXPORT_FORMATS or mime_type.startswith("text/"):
        return ".txt"
    return None


def is_table(ref: FileRef) -> bool:
    """A spreadsheet or CSV, whose text is rows under a header row."""
    return ref.mime_type in _TABLE_TYPES or extension_for(ref.mime_type) in _TABLE_EXTENSIONS


class UnreadableFile(Exception):
    """No reader produced text from the file."""


class DriveFileSource(DocumentSource):
    """One downloaded Drive file, as document_intelligence's source."""

    def __init__(self, *, file_id: str, filename: str, mime_type: str, content: bytes, content_hash: str = ""):
        self.file_id = file_id
        self._filename = filename
        self._mime_type = mime_type
        self._content = content
        self._content_hash = content_hash or None

    async def get_metadata(self, file_id: str) -> SourceFile:
        return SourceFile(
            file_id=self.file_id,
            filename=self._filename,
            mime_type=self._mime_type,
            extension=extension_for(self._mime_type) or "",
            size_bytes=len(self._content),
            content_hash=self._content_hash,
        )

    async def download(self, file_id: str) -> BinaryIO:
        return BytesIO(self._content)


_REGISTRY = ExtractionBackendRegistry([PyMuPDFBackend(), OfficeTextBackend(), PlainTextBackend()])

_OPTIONS = ExtractionOptions(
    enable_ocr=False,
    enable_tables=False,
    enable_images=False,
    enable_image_description=False,
    enable_layout=False,
    timeout_seconds=30,
)


def canonical_text(document: CanonicalDocument) -> str:
    """A canonical document's text, block by block."""
    parts: list[str] = []

    for block in document.blocks:
        if block.text:
            parts.append(block.text)
        elif block.table is not None:
            parts.append(block.table.markdown or " ".join(cell.text for cell in block.table.cells))

    return "\n".join(parts)


def _extract(source: DriveFileSource) -> str:
    # A fresh event loop in this worker thread: the backends are async in
    # signature but parse synchronously.
    result = asyncio.run(ExtractionOrchestrator(source, _REGISTRY).extract(source.file_id, _OPTIONS))

    if result.document is None:
        raise UnreadableFile(result.errors[0].message if result.errors else "no reader produced text")

    return canonical_text(result.document)


async def extract_text(source: DriveFileSource) -> str:
    """The file's text, extracted off the event loop. Raises UnreadableFile."""
    return await asyncio.to_thread(_extract, source)


# ---------------------------------------------------------------------
# Fetching, with the text cache
# ---------------------------------------------------------------------


def content_key(file_id: str, checksum: str) -> str:
    # Not per user: the same file at the same checksum has the same text for
    # everyone. Safe only because a file reaches a fetch after the asking
    # user's own credentials found it in Drive; a new entry point into
    # fetch_files must keep that true.
    return f"gdrive:content:{file_id}:{checksum}"


@dataclass
class FetchedFile:
    ref: FileRef
    text: str = ""
    error: str = ""


def _checksum(ref: FileRef) -> str:
    """A file's content identity, for cache keys and invalidation."""
    return ref.md5 or ref.modified_time or "0"


# One download per file at a time, per process: a prefetch started before
# the plan arrived is usually still running when the reranker asks for the
# same file, and joining it costs nothing.
_inflight: dict[str, asyncio.Task] = {}

# Downloads that outlived their caller's deadline, referenced until they
# finish so their result still lands in the cache.
_background: set[asyncio.Task] = set()


def _keep_alive(task: asyncio.Task) -> None:
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _fetch_one(ctx: DriveContext, ref: FileRef) -> FetchedFile:
    """A file's text, from cache or downloaded and parsed. A type with no
    text to extract is not downloaded at all, and a file too large to read
    in time is judged on its name instead."""
    if extension_for(ref.mime_type) is None:
        return FetchedFile(ref=ref, error="no readable text")

    if ref.size and ref.size > gdrive_config.rerank_max_download_bytes:
        return FetchedFile(ref=ref, error="too large to read for ranking")

    cache_key = content_key(ref.id, _checksum(ref))

    cached = await _read_cache(cache_key)
    if cached is not None:
        return replace(cached, ref=ref)

    running = _inflight.get(cache_key)

    if running is None:
        running = asyncio.create_task(_download(ctx, ref, cache_key))
        _inflight[cache_key] = running
        running.add_done_callback(lambda _: _inflight.pop(cache_key, None))

    # Shielded, so a caller giving up on the deadline does not cancel a
    # download another caller (or the cache) is still waiting for.
    return replace(await asyncio.shield(running), ref=ref)


async def _read_cache(cache_key: str) -> Optional[FetchedFile]:
    try:
        raw = await cache_redis.client.get(cache_key)
    except Exception as e:
        logger.debug(f"Drive content cache read failed :: {e}")
        return None

    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None

    return FetchedFile(ref=FileRef(id="", name=""), text=payload.get("text", ""), error=payload.get("error", ""))


async def _download(ctx: DriveContext, ref: FileRef, cache_key: str) -> FetchedFile:
    export_mime_type = EXPORT_FORMATS.get(ref.mime_type)
    ceiling = gdrive_config.max_export_bytes if export_mime_type else gdrive_config.max_download_bytes

    try:
        content = await ctx.drive.call(
            lambda token: drive_client.download(
                token, ref.id, max_bytes=ceiling, export_mime_type=export_mime_type
            )
        )
    except FileTooLarge as e:
        return await _remember(cache_key, FetchedFile(ref=ref, error=str(e)))
    except DriveAuthRequired as e:
        # About the user's connection, not the file: never cached.
        return FetchedFile(ref=ref, error=f"download failed: {e}")
    except Exception as e:
        logger.warning(f"Drive fetch failed :: file={ref.name} ({ref.id}), error={e}")
        return await _remember(cache_key, FetchedFile(ref=ref, error=f"download failed: {e}"))

    try:
        text = await extract_text(
            DriveFileSource(
                file_id=ref.id,
                filename=ref.name,
                mime_type=export_mime_type or ref.mime_type,
                content=content,
                content_hash=ref.md5,
            )
        )
    except UnreadableFile as e:
        return await _remember(cache_key, FetchedFile(ref=ref, error=f"could not read: {e}"))

    return await _remember(
        cache_key, FetchedFile(ref=ref, text=text[: gdrive_config.fetch_max_chars_per_file])
    )


async def _remember(cache_key: str, fetched: FetchedFile) -> FetchedFile:
    try:
        await cache_redis.client.set(
            cache_key,
            json.dumps({"text": fetched.text, "error": fetched.error}),
            ex=gdrive_config.fetch_cache_ttl_seconds,
        )
    except Exception as e:
        logger.debug(f"Drive content cache write failed :: {e}")

    return fetched


async def fetch_files(ctx: DriveContext, refs: list[FileRef], *, timeout: Optional[float] = None) -> list[FetchedFile]:
    """Every candidate's text, fetched in parallel.

    A file that fails keeps its error, so the reranker can judge it on its
    metadata. With ``timeout``, files still downloading at the deadline come
    back as "timed out"; their downloads carry on and land in the cache for
    the next question.
    """
    if not refs:
        return []

    limit = asyncio.Semaphore(gdrive_config.fetch_max_workers)

    async def fetch(ref: FileRef) -> FetchedFile:
        async with limit:
            return await _fetch_one(ctx, ref)

    tasks = [asyncio.create_task(fetch(ref)) for ref in refs]
    done, pending = await asyncio.wait(tasks, timeout=timeout)

    for task in pending:
        _keep_alive(task)

    results: list[FetchedFile] = []

    for task, ref in zip(tasks, refs):
        if task not in done:
            results.append(FetchedFile(ref=ref, error="timed out"))
        elif task.exception() is not None:
            results.append(FetchedFile(ref=ref, error=f"fetch failed: {task.exception()}"))
        else:
            results.append(task.result())

    return results


# ---------------------------------------------------------------------
# A file itself, for a model that reads it natively
# ---------------------------------------------------------------------

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# Images a model does not take as they are, sent converted to JPEG.
CONVERTED_IMAGE_TYPES = {"image/bmp", "image/x-ms-bmp", "image/tiff"}

# A phone photo is about 4000 pixels a side; the model reads at most 2048.
_MAX_IMAGE_SIDE = 2000
# Smaller images within that size go as they are, untouched.
_KEEP_IMAGE_BYTES = 1_500_000

# How long a file is held for the answer after the reranker started its
# download. Never put in Redis.
_NATIVE_HOLD_SECONDS = 60

# file key -> its download. Not per user, like the text cache, and safe for
# the same reason: a file reaches here only after the asking user's own
# Drive access found it.
_native: dict[str, asyncio.Task] = {}


@dataclass(frozen=True)
class NativeFile:
    """A file as it is sent to the model: possibly made smaller, so its
    type can differ from the Drive file's."""

    data: bytes
    mime_type: str


def reads_natively(ref: FileRef) -> bool:
    """A PDF or an image a model can read as the file itself: what a scan
    or a photo needs, having no text to extract."""
    readable = ref.mime_type == PDF or ref.mime_type in IMAGE_TYPES or ref.mime_type in CONVERTED_IMAGE_TYPES
    return readable and ref.size <= gdrive_config.answer_native_max_bytes


def _shrink(data: bytes, mime_type: str) -> NativeFile:
    """The file as the model reads it best and cheapest: a scan's first
    answer_native_max_pages pages (OpenAI reads at most 100 a request, and
    every page costs tokens), a photo at most _MAX_IMAGE_SIDE a side, as a
    still JPEG (an animated GIF is refused)."""
    if mime_type == PDF:
        import pymupdf

        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.page_count <= gdrive_config.answer_native_max_pages:
                return NativeFile(data, PDF)

            document.select(range(gdrive_config.answer_native_max_pages))
            return NativeFile(document.tobytes(garbage=3, deflate=True), PDF)

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        if (
            mime_type in IMAGE_TYPES
            and mime_type != "image/gif"
            and max(image.size) <= _MAX_IMAGE_SIDE
            and len(data) <= _KEEP_IMAGE_BYTES
        ):
            return NativeFile(data, mime_type)

        image.seek(0)
        rgba = image.convert("RGBA")
        # Transparent areas on white, as a viewer shows them, not black.
        still = Image.new("RGB", rgba.size, (255, 255, 255))
        still.paste(rgba, mask=rgba.getchannel("A"))
        still.thumbnail((_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE))

        out = BytesIO()
        still.save(out, "JPEG", quality=85)
        return NativeFile(out.getvalue(), "image/jpeg")


async def _download_native(ctx: DriveContext, ref: FileRef) -> NativeFile:
    data = await ctx.drive.call(
        lambda token: drive_client.download(token, ref.id, max_bytes=gdrive_config.answer_native_max_bytes)
    )
    return await asyncio.to_thread(_shrink, data, ref.mime_type)


def _start_native(ctx: DriveContext, ref: FileRef) -> asyncio.Task:
    key = content_key(ref.id, _checksum(ref))
    task = _native.get(key)

    if task is None:
        task = asyncio.create_task(_download_native(ctx, ref))
        # Marks a failure as seen when nobody ends up asking for the file.
        task.add_done_callback(lambda t: t.cancelled() or t.exception())
        _native[key] = task
        asyncio.get_running_loop().call_later(_NATIVE_HOLD_SECONDS, _native.pop, key, None)

    return task


def prefetch_native(ctx: DriveContext, refs: list[FileRef]) -> None:
    """Start downloading files an answer may read natively, without
    waiting: the reranker starts its unreadable candidates, most likely
    first, while its model call runs, before it knows which it will keep.
    Only the first few: a folder of photos would otherwise be downloaded
    whole for an answer that reads four."""
    for ref in [r for r in refs if reads_natively(r)][: gdrive_config.answer_native_prefetch_max]:
        _start_native(ctx, ref)


async def native_file(ctx: DriveContext, ref: FileRef, *, timeout: Optional[float] = None) -> Optional[NativeFile]:
    """A file as the model reads it, from a download already started or a
    new one; None when it cannot be downloaded, or not within ``timeout``
    (the download carries on, for the next question)."""
    try:
        return await asyncio.wait_for(asyncio.shield(_start_native(ctx, ref)), timeout)
    except TimeoutError:
        logger.info(f"Drive native read not in time :: file={ref.name} ({ref.id}), timeout={timeout}")
        return None
    except Exception as e:
        logger.warning(f"Drive native read failed :: file={ref.name} ({ref.id}), error={e}")
        return None


def prefetch(ctx: DriveContext, refs: list[FileRef]) -> None:
    """Start reading files whose text will be wanted shortly, without
    waiting: the reranker reads every verbatim hit, so their downloads
    overlap the model call instead of queueing behind it."""
    if refs:
        _keep_alive(asyncio.create_task(fetch_files(ctx, refs, timeout=gdrive_config.rerank_fetch_timeout_seconds)))
