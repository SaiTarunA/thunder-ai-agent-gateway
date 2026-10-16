"""Filename resolution, and the cached listing of every file in a Drive.

People type filenames from memory: a dropped letter, different separators,
no extension, stripped punctuation, or a file whose own name is misspelled.
A typed name goes through four tiers, cheapest first, stopping at the first
that answers:

  1. exact      normalised equality
  2. contains   substring, or every typed token present in the name
  3. fuzzy      difflib edit distance
  4. semantic   a model, for input no edit distance reaches ("my CV" -> JOHN_CV)

Tiers 1-3 run locally against a cached listing of the whole Drive. That
listing is the one piece of per-user search state kept between questions:
fuzzy matching needs every name, and Drive can only be queried for exact
substrings.
"""

import asyncio
import difflib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import DriveAccess, DriveContext, DriveUser, cache_redis, drive_client

logger = logging.getLogger(__name__)


def names_key(principal: str) -> str:
    return f"gdrive:names:{principal}"


def names_refresh_lock_key(principal: str) -> str:
    return f"gdrive:names:{principal}:refreshing"


def names_stamp_key(principal: str) -> str:
    # When the stored listing was built: a process holding that build need
    # not read and parse the whole listing again to know it is current.
    return f"gdrive:names:{principal}:at"


FOLDER_MIME = "application/vnd.google-apps.folder"
# A pointer to a file elsewhere: nothing of its own to read or open.
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

_LISTING_FIELDS = (
    "nextPageToken,"
    "files(id,name,mimeType,size,modifiedTime,md5Checksum,parents,webViewLink)"
)

# Separators people vary freely: "Jane Doe_Resume" and "jane-doe resume"
# are the same name typed by two people.
_SEPARATORS = re.compile(r"[\s_\-.,()\[\]{}]+")

# An extension by shape rather than by list: a dot, then 1-5 characters
# including a letter, at the very end. "Report v1.2" and "Q4.2024" keep
# their numeric suffix, which is a version, not a format.
_EXTENSION = re.compile(r"\.(?=[a-z0-9]{1,5}$)[a-z0-9]*[a-z][a-z0-9]*$")


def tokens(text: str) -> list[str]:
    """Words in any script: runs of letters, digits and combining marks.

    The marks matter: Devanagari vowel signs and decomposed accents are
    marks, so an ASCII pattern splits "आधार" and turns "résumé" into "r" and
    "sum". Scripts written without spaces come out as one token per run,
    which still suits Drive's substring matching.
    """
    text = unicodedata.normalize("NFC", text or "").casefold()
    words: list[str] = []
    current: list[str] = []

    for char in text:
        if unicodedata.category(char)[0] in "LNM":
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []

    if current:
        words.append("".join(current))

    return words


def is_word(token: str) -> bool:
    """Long enough to match on. Two characters, so "CV", "ID", "W2" and "Q4"
    survive; single characters are dropped only in ASCII, since in Chinese or
    Japanese one character is routinely a whole word."""
    return len(token) >= 2 or not token.isascii()


@dataclass(frozen=True)
class FileRef:
    """A file as known from metadata alone: nothing downloaded."""

    id: str
    name: str
    mime_type: str = ""
    size: int = 0
    modified_time: str = ""
    md5: str = ""
    folder: str = ""
    web_view_link: str = ""

    @property
    def normalized(self) -> str:
        return normalize(self.name)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mime_type": self.mime_type,
            "size": self.size,
            "modified_time": self.modified_time,
            "md5": self.md5,
            "folder": self.folder,
            "web_view_link": self.web_view_link,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "FileRef":
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            mime_type=raw.get("mime_type", ""),
            size=int(raw.get("size") or 0),
            modified_time=raw.get("modified_time", ""),
            md5=raw.get("md5", ""),
            folder=raw.get("folder", ""),
            web_view_link=raw.get("web_view_link", ""),
        )


@dataclass
class NameResolution:
    """What a typed name resolved to. ``tier`` is reported so a fuzzy or
    semantic match is never presented as though the user named it exactly."""

    matches: list[FileRef] = field(default_factory=list)
    tier: str = "none"
    confident: bool = False
    query: str = ""


def normalize(text: str) -> str:
    """Lowercase, drop a trailing extension, flatten separators:
    "Jane_Doe-Resume.PDF" and "jane doe resume" compare equal."""
    lowered = _EXTENSION.sub("", (text or "").strip().lower())
    return _SEPARATORS.sub(" ", lowered).strip()


def is_noise(name: str, folder: str = "") -> bool:
    """Files an operator has explicitly excluded; nothing by default.

    What counts as clutter depends on the person, so automatic handling is
    a ranking penalty (template_names), never an exclusion: a wrong guess
    there costs a position, where a wrong guess here hides the file.
    """
    if not (gdrive_config.exclude_path_patterns or gdrive_config.exclude_extensions):
        return False

    haystack = f"{folder}/{name}".lower()

    if any(p.lower() in haystack for p in gdrive_config.exclude_path_patterns):
        return True

    return any(name.lower().endswith(ext.lower()) for ext in gdrive_config.exclude_extensions)


def template_names(refs: list[FileRef]) -> set[str]:
    """Filenames reused across many folders: generated by a template, or a
    human folder per client each holding Contract.pdf. That ambiguity is why
    this only ever lowers a file's rank, never hides it."""
    folders: dict[str, set[str]] = {}

    for ref in refs:
        folders.setdefault(ref.normalized, set()).add(ref.folder)

    return {
        name for name, seen in folders.items()
        if len(seen) >= gdrive_config.template_name_min_folders
    }


# ---------------------------------------------------------------------
# The cached listing
# ---------------------------------------------------------------------


@dataclass
class _Listing:
    read_at: float          # monotonic, when this process last read it
    refreshed_at: float     # wall clock, when it was built from Drive
    refs: list[FileRef]
    folders: dict[str, str]  # folder id -> path


# The listing as last read from Redis, per process. One question reads it
# several times, and each read parses every filename in the Drive.
_LOCAL_SECONDS = 30.0
_local: dict[str, _Listing] = {}

# Users a process keeps a listing (and what is derived from it) for: each
# is a whole Drive's names. The least recently read go first.
_MAX_USERS = 200

# Bumped when a user's connection is replaced: a build that started before
# then lists the old account's Drive and must not be stored.
_generations: dict[str, int] = {}


def _keep(cache: dict, principal: str, value) -> None:
    cache.pop(principal, None)
    cache[principal] = value

    while len(cache) > _MAX_USERS:
        cache.pop(next(iter(cache)))

# Background rebuilds, referenced until done so they are not collected.
_refresh_tasks: set[asyncio.Task] = set()

_FILES_QUERY = f"mimeType != '{FOLDER_MIME}' and mimeType != '{SHORTCUT_MIME}' and trashed = false"
_FOLDERS_QUERY = f"mimeType = '{FOLDER_MIME}' and trashed = false"
_FOLDER_FIELDS = "nextPageToken,files(id,name,parents,createdTime,modifiedTime)"

# The folder listing is split by creation time and the parts fetched at
# once: one part per year this far back, plus one for anything older. A part
# with more than a page left is split again (see _list_created).
_FOLDER_YEARS = 6
_SPLIT_PARTS = 4


class _Progress:
    """What a listing build has fetched so far: files newest first, and
    folders alongside them."""

    def __init__(self) -> None:
        self.files: list[dict] = []
        # By id: a folder created on a boundary between parts can be listed twice.
        self.folders: dict[str, dict] = {}
        # Set once more than name_listing_large_files are listed with more
        # still to come: a large Drive.
        self.large = asyncio.Event()
        self._snapshot: Optional[tuple[tuple[int, int], _Listing]] = None

    def add_files(self, items: list[dict], more: bool) -> None:
        self.files.extend(items)

        if more and len(self.files) > gdrive_config.name_listing_large_files:
            self.large.set()

    def add_folders(self, items: list[dict]) -> None:
        self.folders.update((item["id"], item) for item in items)

    def listing(self) -> "_Listing":
        """Everything listed so far. The same object until another page
        arrives, so what is derived from it (profile, vocabulary) is reused."""
        size = (len(self.files), len(self.folders))

        if self._snapshot is None or self._snapshot[0] != size:
            self._snapshot = (size, _to_listing(self.files, self.folders.values()))

        return self._snapshot[1]


# Builds under way, per user: a question arriving during one (a warm-up's,
# say) waits for it instead of listing the Drive a second time.
_builds: dict[str, tuple[asyncio.Task, _Progress]] = {}


async def _list_all(
    ctx: DriveContext,
    query: str,
    fields: str,
    on_page: Callable[[list[dict], bool], None],
    *,
    order_by: Optional[str] = None,
) -> None:
    """Every page of a listing, each handed to ``on_page`` with whether more
    follow, as it arrives."""
    page_token: Optional[str] = None

    while True:
        response = await ctx.drive.call(
            lambda token, page_token=page_token: drive_client.list_files(
                token, query=query, fields=fields, page_size=1000, page_token=page_token, order_by=order_by
            )
        )
        page_token = response.get("nextPageToken")
        on_page(response.get("files", []), bool(page_token))

        if not page_token:
            return


_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _creation_spans(now: datetime) -> list[tuple[Optional[datetime], Optional[datetime]]]:
    """[from, to) creation-time spans covering every item exactly once: a
    year each for the last _FOLDER_YEARS years, and everything before."""
    starts = [datetime(now.year - back, 1, 1, tzinfo=timezone.utc) for back in range(_FOLDER_YEARS - 1, -1, -1)]
    return [(None, starts[0]), *zip(starts, [*starts[1:], None])]


async def _list_created(
    ctx: DriveContext,
    query: str,
    fields: str,
    on_page: Callable[[list[dict]], None],
    created_from: Optional[datetime],
    created_to: Optional[datetime],
) -> None:
    """Everything ``query`` matches created in [created_from, created_to),
    oldest first, each page handed to ``on_page``.

    With more than a page left, the rest of the span is cut in time: this
    listing carries on through the first part, and the later parts are
    listed alongside it (each cut the same way in turn) instead of after it,
    page by page. A bulk upload, everything created within seconds, leaves
    the later parts empty and this listing paging through, as unsplit.
    """
    bounds = [query]

    if created_from:
        bounds.append(f"createdTime >= '{created_from.strftime(_TIME_FORMAT)}'")
    if created_to:
        bounds.append(f"createdTime < '{created_to.strftime(_TIME_FORMAT)}'")

    spanned = " and ".join(bounds)
    page_token: Optional[str] = None
    # Where this listing stops once the later parts are being listed alongside.
    stop_at = created_to
    later: Optional[asyncio.Task] = None

    try:
        while True:
            response = await ctx.drive.call(
                lambda token, page_token=page_token: drive_client.list_files(
                    token, query=spanned, fields=fields, page_size=1000, page_token=page_token, order_by="createdTime"
                )
            )
            items = response.get("files", [])
            on_page(items)
            page_token = response.get("nextPageToken")
            last = _created(items[-1]) if items else None

            if not page_token or (stop_at and last and last >= stop_at):
                break

            if later is None and last is not None:
                end = created_to or datetime.now(timezone.utc) + timedelta(days=1)
                step = (end - last) / _SPLIT_PARTS
                cuts = sorted({(last + step * i).replace(microsecond=0) for i in range(1, _SPLIT_PARTS)} - {last})

                if cuts:
                    stop_at = cuts[0]
                    later = asyncio.ensure_future(_all(
                        _list_created(ctx, query, fields, on_page, start, stop)
                        for start, stop in zip(cuts, [*cuts[1:], created_to])
                    ))

        if later is not None:
            await later
    except BaseException:
        if later is not None:
            later.cancel()
        raise


def _created(item: dict) -> Optional[datetime]:
    """An item's creation time, to the second, as Drive queries take it."""
    stamp = item.get("createdTime", "")
    return datetime.strptime(stamp[:19], _TIME_FORMAT).replace(tzinfo=timezone.utc) if stamp else None


async def _all(coroutines) -> None:
    """Run these at once. The first failure cancels the rest and is raised
    as itself, not wrapped in a group."""
    tasks = [asyncio.ensure_future(c) for c in coroutines]

    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        raise


def _folder_paths(folders: dict[str, dict]) -> dict[str, str]:
    """Folder id -> human path, so ranking can use folder context."""

    def path_of(folder_id: str, seen: frozenset = frozenset()) -> str:
        node = folders.get(folder_id)

        # A missing ancestor (no permission on a parent folder) is common.
        if not node or folder_id in seen:
            return ""

        parents = node.get("parents") or []
        parent_path = path_of(parents[0], seen | {folder_id}) if parents else ""
        name = node.get("name", "")

        return f"{parent_path}/{name}" if parent_path else name

    return {folder_id: path_of(folder_id) for folder_id in folders}


def _to_listing(files: list[dict], folders: Iterable[dict]) -> _Listing:
    # Most recently modified first, the order Drive lists folders in when
    # not split, and the order the stored map keeps.
    ordered = sorted(folders, key=lambda folder: folder.get("modifiedTime", ""), reverse=True)
    paths = _folder_paths({folder["id"]: folder for folder in ordered})
    refs: list[FileRef] = []

    for item in files:
        parents = item.get("parents") or []
        refs.append(
            FileRef(
                id=item["id"],
                name=item.get("name", ""),
                mime_type=item.get("mimeType", ""),
                size=int(item.get("size") or 0),
                modified_time=item.get("modifiedTime", ""),
                md5=item.get("md5Checksum", ""),
                folder=paths.get(parents[0], "") if parents else "",
                web_view_link=item.get("webViewLink", ""),
            )
        )

    return _Listing(read_at=time.monotonic(), refreshed_at=time.time(), refs=refs, folders=paths)


async def refresh_name_cache(ctx: DriveContext, progress: Optional[_Progress] = None) -> _Listing:
    """Re-list every file and folder in the Drive and store them. Files come
    newest first, so a question on a large Drive can go ahead with the
    newest part (see _whole_or_newest); folders alongside, in parallel parts
    by creation time, so their paths are complete by then."""
    started = time.perf_counter()
    progress = progress or _Progress()
    generation = _generations.get(ctx.principal, 0)

    await _all([
        _list_all(ctx, _FILES_QUERY, _LISTING_FIELDS, progress.add_files, order_by="modifiedTime desc"),
        *(
            _list_created(ctx, _FOLDERS_QUERY, _FOLDER_FIELDS, progress.add_folders, created_from, created_to)
            for created_from, created_to in _creation_spans(datetime.now(timezone.utc))
        ),
    ])

    listing = _to_listing(progress.files, progress.folders.values())

    # The user connected another account while this listed the old one.
    if _generations.get(ctx.principal, 0) != generation:
        logger.info(f"Drive listing of a replaced connection not stored :: principal={ctx.principal}")
        return listing

    payload = await asyncio.to_thread(
        json.dumps,
        {"at": listing.refreshed_at, "refs": [r.to_dict() for r in listing.refs], "folders": listing.folders},
    )

    async with cache_redis.client.pipeline(transaction=True) as pipe:
        pipe.set(names_key(ctx.principal), payload, ex=gdrive_config.name_cache_max_age_seconds)
        pipe.set(names_stamp_key(ctx.principal), repr(listing.refreshed_at), ex=gdrive_config.name_cache_max_age_seconds)
        await pipe.execute()

    _keep(_local, ctx.principal, listing)

    logger.info(
        f"Drive name cache rebuilt :: principal={ctx.principal}, files={len(listing.refs)}, "
        f"seconds={time.perf_counter() - started:.2f}"
    )
    return listing


def _parse_listing(raw: str) -> Optional[_Listing]:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None

    return _Listing(
        read_at=time.monotonic(),
        refreshed_at=float(payload.get("at") or 0.0),
        refs=[FileRef.from_dict(d) for d in payload.get("refs") or []],
        folders=payload.get("folders") or {},
    )


async def _read_cached(principal: str) -> Optional[_Listing]:
    raw = await cache_redis.client.get(names_key(principal))

    if not raw:
        return None

    listing = await asyncio.to_thread(_parse_listing, raw)

    if listing is None:
        logger.warning(f"Drive name cache corrupt, rebuilding :: principal={principal}")

    return listing


async def _still_current(principal: str, listing: _Listing) -> bool:
    """Is ``listing`` the build Redis holds? Expired, it is not."""
    stamp = await cache_redis.client.get(names_stamp_key(principal))
    return stamp is not None and stamp == repr(listing.refreshed_at)


def _start_build(ctx: DriveContext) -> tuple[asyncio.Task, _Progress]:
    """The user's build under way, or a new one."""
    principal = ctx.principal
    build = _builds.get(principal)

    if build is None:
        progress = _Progress()
        task = asyncio.create_task(refresh_name_cache(ctx, progress))
        build = _builds[principal] = (task, progress)

        def finished(done: asyncio.Task) -> None:
            # Unless forget_names has dropped it since.
            if _builds.get(principal, (None,))[0] is done:
                del _builds[principal]

            # Marks a failure as seen when every waiter has moved on (a
            # large Drive's question, a warm-up); the next build reports it.
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)

    return build


async def _build(ctx: DriveContext) -> _Listing:
    """Build the user's listing, or wait for the build already under way.
    Shielded, since a build is shared: one waiter giving up must not cancel
    it for the others."""
    task, _ = _start_build(ctx)
    return await asyncio.shield(task)


async def _whole_or_newest(ctx: DriveContext) -> _Listing:
    """The user's whole listing, as _build, for a small Drive. A large one
    (past name_listing_large_files) takes a request per 460 files, one after
    another: the question goes ahead with the newest files listed so far,
    and the build carries on to store the whole listing for the next."""
    task, progress = _start_build(ctx)
    large = asyncio.create_task(progress.large.wait())

    try:
        # Neither is cancelled by a waiter giving up.
        await asyncio.wait({task, large}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        large.cancel()

    if task.done():
        return task.result()

    listing = progress.listing()
    logger.info(
        f"Large Drive, searching its newest files while the listing completes :: principal={ctx.principal}, "
        f"files={len(listing.refs)}, folders={len(listing.folders)}"
    )
    return listing


def warm_listing(user: DriveUser) -> None:
    """Start building the user's listing if none is cached, and return at
    once. For where a search is likely to follow (Drive just connected, a
    message on its way through routing): the search then finds the listing
    built, or joins the build, instead of starting one."""
    task = asyncio.create_task(_warm(DriveContext(drive=DriveAccess(user))))
    _refresh_tasks.add(task)
    task.add_done_callback(_refresh_tasks.discard)


async def _warm(ctx: DriveContext) -> None:
    try:
        if not await cache_redis.client.exists(names_key(ctx.principal)):
            await _build(ctx)
    except Exception as e:
        logger.warning(f"Drive listing warm-up failed :: principal={ctx.principal}, error={e}")


async def _refresh_quietly(ctx: DriveContext) -> None:
    try:
        await _build(ctx)
    except Exception:
        logger.warning(f"Background Drive name refresh failed :: principal={ctx.principal}", exc_info=True)
    finally:
        await cache_redis.client.delete(names_refresh_lock_key(ctx.principal))


async def _listing(ctx: DriveContext) -> _Listing:
    """The cached listing, rebuilt in the background once stale.

    Past name_cache_ttl_seconds the cached listing is still served while a
    rebuild runs off the question's path; only a listing past
    name_cache_max_age_seconds (Redis expires it), or none, is rebuilt while
    the caller waits.
    """
    listing = _local.get(ctx.principal)

    if listing is None or time.monotonic() - listing.read_at >= _LOCAL_SECONDS:
        # The same build as the one held: kept, with everything worked out
        # from it. On a 20,000-file Drive, reading and parsing it again and
        # rebuilding those cost 0.4s of every question.
        if listing is not None and await _still_current(ctx.principal, listing):
            listing.read_at = time.monotonic()
        else:
            listing = await _read_cached(ctx.principal)

            if listing is None:
                return await _whole_or_newest(ctx)

        _keep(_local, ctx.principal, listing)

    # One rebuild at a time per user, across processes. The lock outlasts a
    # large Drive's build (about a minute for 20,000 files); a finished
    # build releases it.
    if time.time() - listing.refreshed_at > gdrive_config.name_cache_ttl_seconds and await cache_redis.client.set(
        names_refresh_lock_key(ctx.principal), "1", nx=True, ex=600
    ):
        task = asyncio.create_task(_refresh_quietly(ctx))
        _refresh_tasks.add(task)
        task.add_done_callback(_refresh_tasks.discard)

    return listing


async def get_names(ctx: DriveContext, *, refresh: bool = False) -> list[FileRef]:
    """Every file in the Drive, from cache. Shared between callers; do not mutate."""
    if refresh:
        return (await _build(ctx)).refs

    return (await _listing(ctx)).refs


async def get_folders(ctx: DriveContext) -> dict[str, str]:
    """Every folder in the Drive, id -> path, from the same cache."""
    return (await _listing(ctx)).folders


async def forget_names(principal: str) -> bool:
    """Drop a user's cached listing. True if one existed."""
    # A build under way for the connection being replaced finishes, but no
    # later question waits on it, and what it lists is not stored.
    _generations[principal] = _generations.get(principal, 0) + 1
    _builds.pop(principal, None)
    _local.pop(principal, None)
    _profiles.pop(principal, None)
    _vocabularies.pop(principal, None)
    _indexes.pop(principal, None)
    return bool(await cache_redis.client.delete(names_key(principal), names_stamp_key(principal)))


def _word_matches(word: str, candidates: set[str]) -> bool:
    """Equal, or one a prefix of the other ("tax" / "taxes")."""
    if word in candidates:
        return True

    return len(word) >= 3 and any(
        len(c) >= 3 and (c.startswith(word) or word.startswith(c))
        for c in candidates
    )


def match_folders(named: str, folders: dict[str, str], *, limit: int = 30) -> dict[str, str]:
    """The folders a question names, with everything beneath them.

    ``named`` is the model's reading of the folder: a path, a name, or a
    half-remembered description. Best tier only:

      3  the full path, or its trailing segments, exactly
      2  every word in the folder's own name
      1  every word somewhere in the folder's path

    Capped, since each folder is one clause of a Drive query; shallowest
    first, so the cap drops deep subfolders rather than the named ones.
    """
    text = (named or "").strip().strip("/").casefold()
    words = {w for w in tokens(text) if is_word(w)}

    if not words:
        return {}

    tiers: dict[int, list[str]] = {}

    for folder_id, path in folders.items():
        low = path.casefold()

        if low == text or low.endswith("/" + text):
            tier = 3
        elif all(_word_matches(w, set(tokens(path.rsplit("/", 1)[-1]))) for w in words):
            tier = 2
        elif all(_word_matches(w, set(tokens(path))) for w in words):
            tier = 1
        else:
            continue

        tiers.setdefault(tier, []).append(folder_id)

    if not tiers:
        return {}

    roots = [folders[i] for i in tiers[max(tiers)]]
    matched = {
        folder_id: path
        for folder_id, path in folders.items()
        if any(path == root or path.startswith(root + "/") for root in roots)
    }

    ordered = sorted(matched.items(), key=lambda kv: kv[1].count("/"))

    return dict(ordered[:limit])


def collapse_duplicates(refs: list[FileRef], *, keep: str = "newest") -> list[FileRef]:
    """One entry per distinct content (md5); files with no checksum, such as
    Google-native docs, are always kept.

    The copy kept is the most recently modified, in the position of the
    first, so "my latest resume" is not reported with an older copy's date.
    ``keep="oldest"`` turns that round, for a question asking for the oldest.
    """
    oldest = keep == "oldest"
    chosen: dict[str, FileRef] = {}

    for ref in refs:
        best = chosen.get(ref.md5)

        if ref.md5 and (
            best is None
            or (ref.modified_time < best.modified_time if oldest
                else ref.modified_time > best.modified_time)
        ):
            chosen[ref.md5] = ref

    placed: set[str] = set()
    kept: list[FileRef] = []

    for ref in refs:
        if ref.md5:
            if ref.md5 in placed:
                continue
            placed.add(ref.md5)
            ref = chosen[ref.md5]

        kept.append(ref)

    return kept


# ---------------------------------------------------------------------
# The Drive's own vocabulary
# ---------------------------------------------------------------------


def _is_vocabulary(token: str) -> bool:
    """Is this filename word worth showing as evidence?

    Rules about a token's shape only, never its meaning. Short codes and
    short numbers stay ("w2", "1099", "401"); identifiers go (long digit
    strings, long mixed letter-digit hashes).
    """
    if len(token) < 2 and token.isascii():
        return False

    if not all(unicodedata.category(c)[0] in "LNM" for c in token):
        return False

    if token.isdigit():
        return len(token) <= 4

    if any(c.isdigit() for c in token) and len(token) >= 6:
        return False

    return True


@dataclass
class CorpusProfile:
    """The vocabulary this person's Drive actually uses, read off their own
    folder and file names, so query expansion is grounded in it rather than
    in what such documents are usually called."""

    folders: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    file_count: int = 0

    @property
    def is_useful(self) -> bool:
        return bool(self.folders or self.terms)


# principal -> (the listing it was built from, its profile). The listing is
# held so a different list cannot reuse its id().
_profiles: dict[str, tuple[list, CorpusProfile]] = {}


async def corpus_profile(ctx: DriveContext, *, max_folders: int = 40, max_terms: int = 400) -> CorpusProfile:
    """A bounded summary of a Drive's vocabulary, rebuilt only when the
    listing changes. Terms are rarest first: a word used once (a client's
    name, a private abbreviation) is the unguessable one that decides
    whether a file is found."""
    listing = await get_names(ctx)
    cached = _profiles.get(ctx.principal)

    if cached and cached[0] is listing:
        return cached[1]

    profile = await asyncio.to_thread(
        _build_profile,
        [r for r in listing if not is_noise(r.name, r.folder)],
        max_folders,
        max_terms,
    )
    _keep(_profiles, ctx.principal, (listing, profile))

    return profile


def _build_profile(files: list[FileRef], max_folders: int, max_terms: int) -> CorpusProfile:
    if not files:
        return CorpusProfile()

    folder_counts: dict[str, int] = {}
    term_counts: dict[str, int] = {}
    # Shallowest depth each term appears at, for breaking rarity ties.
    term_depth: dict[str, int] = {}

    templates = template_names(files)

    for ref in files:
        if ref.folder:
            folder_counts[ref.folder] = folder_counts.get(ref.folder, 0) + 1

        # A template file's name says nothing about what this person keeps.
        if ref.normalized in templates:
            continue

        depth = ref.folder.count("/") + 1 if ref.folder else 0

        # Per file, not per occurrence.
        for token in set(ref.normalized.split()):
            if _is_vocabulary(token):
                term_counts[token] = term_counts.get(token, 0) + 1
                term_depth[token] = min(term_depth.get(token, depth), depth)

    # Folders are the person's own taxonomy: most populated first.
    folders = sorted(folder_counts, key=lambda f: (-folder_counts[f], f))

    # Words before bare numbers (a folder of "Invoice 001".."Invoice 500"
    # would otherwise crowd out every word), then rarest first, then
    # shallowest, since people keep their own documents near the top.
    terms = sorted(term_counts, key=lambda t: (t.isdigit(), term_counts[t], term_depth[t], t))

    return CorpusProfile(folders=folders[:max_folders], terms=terms[:max_terms], file_count=len(files))


@dataclass
class PathVocabulary:
    """Which files each word of this Drive's paths belongs to: how rare a
    word is HERE, and which files sit under a folder named with it (Drive's
    name search reads a file's own name only)."""

    file_count: int = 0
    # word -> ids of the files whose name or folder path contains it
    files: dict[str, set[str]] = field(default_factory=dict)
    # word -> the files whose folder path contains it
    in_folders: dict[str, list[FileRef]] = field(default_factory=dict)

    def count(self, word: str) -> int:
        return len(self.files.get(word, ()))


# principal -> (the listing it was built from, its vocabulary)
_vocabularies: dict[str, tuple[list, PathVocabulary]] = {}


async def path_vocabulary(ctx: DriveContext) -> PathVocabulary:
    """The PathVocabulary of a Drive, rebuilt only when the listing changes."""
    listing = await get_names(ctx)
    cached = _vocabularies.get(ctx.principal)

    if cached and cached[0] is listing:
        return cached[1]

    vocabulary = await asyncio.to_thread(_build_vocabulary, listing)
    _keep(_vocabularies, ctx.principal, (listing, vocabulary))

    return vocabulary


def _build_vocabulary(listing: list[FileRef]) -> PathVocabulary:
    vocabulary = PathVocabulary()

    for ref in listing:
        if is_noise(ref.name, ref.folder):
            continue

        vocabulary.file_count += 1
        folder_words = set(tokens(ref.folder))

        for word in folder_words | set(tokens(ref.normalized)):
            vocabulary.files.setdefault(word, set()).add(ref.id)

        for word in folder_words:
            vocabulary.in_folders.setdefault(word, []).append(ref)

    return vocabulary


@dataclass
class ListingIndex:
    """What every question looks up in a listing, worked out once per
    listing: per question it took 0.7s on a 20,000-file Drive."""

    # Every file's normalized name: is a question some file's name?
    names: set[str] = field(default_factory=set)
    # Names reused across folders (see template_names).
    templates: set[str] = field(default_factory=set)
    # Each word of a filename -> the files named with it, operator-excluded
    # files left out: spelling-tolerant matching compares each distinct
    # word once, not once per file.
    words: dict[str, list[FileRef]] = field(default_factory=dict)


# principal -> (the listing it was built from, its index)
_indexes: dict[str, tuple[list, ListingIndex]] = {}


async def listing_index(ctx: DriveContext) -> ListingIndex:
    """The ListingIndex of a Drive, rebuilt only when the listing changes."""
    listing = await get_names(ctx)
    cached = _indexes.get(ctx.principal)

    if cached and cached[0] is listing:
        return cached[1]

    index = await asyncio.to_thread(_build_index, listing)
    _keep(_indexes, ctx.principal, (listing, index))

    return index


def _build_index(listing: list[FileRef]) -> ListingIndex:
    index = ListingIndex(templates=template_names(listing))

    for ref in listing:
        normalized = ref.normalized
        index.names.add(normalized)

        if is_noise(ref.name, ref.folder):
            continue

        for word in set(tokens(normalized)):
            if is_word(word):
                index.words.setdefault(word, []).append(ref)

    return index


# ---------------------------------------------------------------------
# Resolution ladder
# ---------------------------------------------------------------------


async def searchable_files(ctx: DriveContext) -> list[FileRef]:
    """The cached listing without operator-excluded files."""
    return [r for r in await get_names(ctx) if not is_noise(r.name, r.folder)]


def _tier_exact(typed: str, pool: list[FileRef]) -> list[FileRef]:
    return [r for r in pool if r.normalized == typed]


def _tier_contains(typed: str, pool: list[FileRef]) -> list[FileRef]:
    """Substring, or every typed token present somewhere in the name."""
    typed_tokens = [t for t in typed.split() if t]

    if not typed_tokens:
        return []

    return [
        ref for ref in pool
        if typed in ref.normalized or all(token in ref.normalized for token in typed_tokens)
    ]


def _tier_fuzzy(typed: str, pool: list[FileRef]) -> list[FileRef]:
    """Edit distance, for a genuine typo on either side. A runner-up far
    enough behind the leader is dropped, so the user is not asked to choose
    between an answer and two near-misses."""
    by_name: dict[str, list[FileRef]] = {}

    for ref in pool:
        by_name.setdefault(ref.normalized, []).append(ref)

    cutoff = gdrive_config.name_fuzzy_cutoff
    matcher = difflib.SequenceMatcher()
    matcher.set_seq2(typed)

    scored: list[tuple[float, str]] = []

    for name in by_name:
        matcher.set_seq1(name)

        # Cheap upper bounds first; the real ratio is the expensive part.
        if matcher.real_quick_ratio() < cutoff or matcher.quick_ratio() < cutoff:
            continue

        ratio = matcher.ratio()

        if ratio >= cutoff:
            scored.append((ratio, name))

    if not scored:
        return []

    scored.sort(key=lambda item: -item[0])

    if len(scored) == 1 or scored[0][0] - scored[1][0] >= gdrive_config.name_fuzzy_decisive_gap:
        return list(by_name[scored[0][1]])

    top = scored[: gdrive_config.name_fuzzy_max_matches]

    return [ref for _, name in top for ref in by_name[name]]


async def _tier_semantic(ctx: DriveContext, typed: str, pool: list[FileRef]) -> list[FileRef]:
    """Ask a model which filenames the user could have meant: a synonym, a
    description ("the excel one"), a half-remembered title. Last, because it
    costs a call where the tiers above cost microseconds."""
    if not pool:
        return []

    # Plausible options rather than an arbitrary slice: names sharing a
    # token first, then the most recently modified.
    typed_tokens = set(normalize(typed).split())

    def affinity(ref: FileRef) -> tuple:
        overlap = len(typed_tokens & set(ref.normalized.split()))
        return (-overlap, _negated(ref.modified_time))

    shortlist = sorted(pool, key=affinity)[: gdrive_config.name_llm_candidates]

    # Imported here: judge imports this module.
    from app.features.gdrive_search.judge import match_filename

    return await match_filename(ctx, typed, shortlist)


def _negated(timestamp: str) -> tuple:
    """Sort key putting the most recent timestamp first."""
    return tuple(-ord(c) for c in (timestamp or ""))


def _local_tiers(query: str, pool: list[FileRef]) -> Optional[tuple[str, list[FileRef]]]:
    """The first of the exact, contains and fuzzy tiers that matches anything."""
    for tier, finder in (("exact", _tier_exact), ("contains", _tier_contains), ("fuzzy", _tier_fuzzy)):
        matches = collapse_duplicates(finder(query, pool))

        if matches:
            return tier, matches

    return None


async def resolve_name(ctx: DriveContext, typed: str, *, allow_semantic: bool = True) -> NameResolution:
    """Resolve a typed filename through the tiers. One match is acted on,
    several become options, and none falls through to the semantic tier."""
    query = normalize(typed)

    if not query:
        return NameResolution(query=query)

    pool = await searchable_files(ctx)

    # Off the event loop: edit distance over a large Drive takes a while.
    found = await asyncio.to_thread(_local_tiers, query, pool)

    if found:
        tier, matches = found
        return NameResolution(matches=matches, tier=tier, confident=len(matches) == 1, query=query)

    if allow_semantic:
        matches = collapse_duplicates(await _tier_semantic(ctx, typed, pool))

        if matches:
            return NameResolution(matches=matches, tier="semantic", confident=len(matches) == 1, query=query)

    return NameResolution(query=query)
