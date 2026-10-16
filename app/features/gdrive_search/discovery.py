"""Runtime file discovery: find the files, then decide how sure we are.

No index is built ahead of time. Drive's own search does the matching
(including the text it OCRs out of scans), and this module turns its
unranked match lists into a short, ordered shortlist plus an honest verdict:

    understand  question -> Drive terms + intent            1 model call
    discover    several Drive queries at once                free
    prefilter   agreement, name and folder overlap          local

Content reranking (rerank.py) then scores the shortlist on what the files
say, and ``verdict`` reads those scores: confident / uncertain / lost.

The verdict is the point: retrieval that quietly returns its best guess is
indistinguishable from retrieval that knows, and on a real Drive a word
like "invoice" matches dozens of files.
"""

import asyncio
import copy
import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Iterable, Optional

from app.core.utils import utils
from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import DriveContext, DriveUnavailable, drive_client
from app.features.gdrive_search.content import prefetch
from app.features.gdrive_search.judge import STOPWORDS, QueryPlan, understand_query
from app.features.gdrive_search.names import (
    FOLDER_MIME,
    SHORTCUT_MIME,
    FileRef,
    ListingIndex,
    PathVocabulary,
    collapse_duplicates,
    corpus_profile,
    get_folders,
    get_names,
    is_noise,
    is_word,
    listing_index,
    match_folders,
    normalize,
    path_vocabulary,
    tokens,
)
from app.integrations.google_drive.client import DriveRequestError

logger = logging.getLogger(__name__)

_DISCOVERY_FIELDS = (
    "nextPageToken,incompleteSearch,"
    "files(id,name,mimeType,size,modifiedTime,md5Checksum,parents,webViewLink,"
    "owners(displayName,emailAddress),sharingUser(displayName,emailAddress))"
)

# `name contains` matches these too; neither has anything to open.
_NOT_FILES = {FOLDER_MIME, SHORTCUT_MIME}

# A superlative, as Drive sorts it. Drive's own order is by relevance for
# content searches and by recency otherwise; either way a query's first 60
# need not hold the oldest or largest file.
_DRIVE_ORDER = {
    "newest": "modifiedTime desc",
    "oldest": "modifiedTime",
    "largest": "quotaBytesUsed desc",
}

# Shortlist places held for the files a superlative orders first, whatever
# their names: "the latest payslip" is the newest of thirty payslips.
_ORDER_SLOTS = 3

_MIME_FILTERS = {
    "document": (
        "mimeType = 'application/vnd.google-apps.document' or "
        "mimeType contains 'wordprocessingml' or "
        "mimeType = 'application/pdf'"
    ),
    "spreadsheet": (
        "mimeType = 'application/vnd.google-apps.spreadsheet' or "
        "mimeType contains 'spreadsheetml' or mimeType = 'text/csv'"
    ),
    "presentation": (
        "mimeType = 'application/vnd.google-apps.presentation' or "
        "mimeType contains 'presentationml'"
    ),
    "pdf": "mimeType = 'application/pdf'",
    "image": "mimeType contains 'image/'",
}

CONFIDENT = "confident"
UNCERTAIN = "uncertain"
LOST = "lost"


@dataclass
class Candidate:
    """A file that might answer the question, and why we think so."""

    ref: FileRef
    score: float = 0.0
    # How many independent Drive queries surfaced this file: the strongest
    # cheap signal available before anything is read.
    agreement: int = 1
    # A query word closely resembles a word in the filename, though Drive's
    # exact substring match did not fire.
    fuzzy_name: bool = False
    # Drive matched the person's own words verbatim.
    exact: bool = False
    # ...and the match was on the filename rather than the contents.
    exact_in_name: bool = False
    # The filename is reused across many folders (see template_names).
    template: bool = False
    # The file sits in (or beneath) a folder the question named.
    in_folder: bool = False
    # Content (fullText) queries that matched it; see _reserve_content.
    content_hits: int = 0
    # Words Drive's search found inside it. Drive reads the text in images
    # and scans, which nothing here extracts: for such a file this is the
    # only evidence of what it says.
    content_terms: list[str] = field(default_factory=list)
    # The passage the reranker judged.
    excerpt: str = ""
    prefilter_score: float = 0.0
    # Owned or shared by the person the question names (see _involves).
    by_person: bool = False


@dataclass
class Discovery:
    """The shortlist plus an honest statement of how sure we are."""

    query: str
    plan: QueryPlan
    verdict: str = LOST
    # A browse's result, listed in its requested order.
    selected: list[Candidate] = field(default_factory=list)
    considered: int = 0
    # Verbatim hits, reported whether or not the ranked flow chose them:
    # "this literally contains what you typed", which the person can check.
    exact_matches: list[Candidate] = field(default_factory=list)
    # The prefiltered candidates, best first. Content reranking starts from
    # this, because rescuing a file whose name undersold it is the reason to
    # read content at all.
    shortlist: list[Candidate] = field(default_factory=list)
    # How many files the plan's own queries matched, for a browse to say how
    # many there are ("how many starred files?"), not just how many it lists.
    # A query returns at most discovery_max_candidates.
    matched: int = 0


def _escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("'", "\\'")


def _build_queries(plan: QueryPlan, *, filtered: bool = True) -> list[str]:
    """One Drive query per term group, plus a filename sweep.

    Terms within a group are ANDed; groups are separate queries rather than
    one OR, so we can see which files several phrasings agree on.
    """
    suffix = " and ".join(_constraints(plan) if filtered else ["trashed = false"])

    queries: list[str] = []

    for group in plan.term_groups:
        terms = [t for t in group if t.strip()]

        if terms:
            queries.append(_group_query(terms, suffix))

    # Drive's fullText does not always cover a file's own name, and a name
    # match is the strongest signal for a specific document.
    distinct = _distinct_terms(plan)

    if distinct:
        names = " or ".join(f"name contains '{_escape(t)}'" for t in distinct[:8])
        queries.append(f"({names}) and {suffix}")

    # A question with no subject ("everything shared with me last month",
    # "my biggest files") is answered by its filters, or its order, alone.
    if not queries and filtered and (plan.has_filters or plan.order):
        queries.append(suffix)

    return queries


_DATE_FIELDS = {
    "modified": "modifiedTime",
    "created": "createdTime",
    "viewed": "viewedByMeTime",
}


def _folder_query(folders: dict[str, str], plan: QueryPlan) -> str:
    """Files directly inside any of these folders, under the plan's filters."""
    parents = " or ".join(f"'{_escape(folder_id)}' in parents" for folder_id in folders)

    return f"({parents}) and " + " and ".join(_constraints(plan))


def _group_query(terms: list[str], suffix: str) -> str:
    """Every term must appear in the file's contents."""
    content = " and ".join(f"fullText contains '{_escape(t)}'" for t in terms)

    return f"({content}) and {suffix}"


def _relax(plan: QueryPlan, empty: set[str]) -> list[str]:
    """Word pairs for each term group of three or more words that matched
    nothing: an ANDed group is only as good as its least likely word. Pairs,
    not single words, so the result still has to agree with itself."""
    suffix = " and ".join(_constraints(plan))
    queries: list[str] = []

    for group in plan.term_groups:
        terms = [t for t in group if t.strip()]

        if len(terms) < 3 or _group_query(terms, suffix) not in empty:
            continue

        pairs = list(combinations(terms, 2))[: gdrive_config.discovery_relaxed_pairs]
        queries.extend(_group_query(list(pair), suffix) for pair in pairs)

    return queries


def _constraints(plan: QueryPlan) -> list[str]:
    """Every narrowing clause the plan asks for. Only clauses verified
    against the live API: modifiedByMeTime and sharedWithMeTime look
    plausible and are rejected by Drive."""
    constraints = ["trashed = false"]

    if plan.file_type != "any" and plan.file_type in _MIME_FILTERS:
        constraints.append(f"({_MIME_FILTERS[plan.file_type]})")

    date_field = _DATE_FIELDS.get(plan.date_field, "modifiedTime")

    if plan.date_from:
        constraints.append(f"{date_field} >= '{_moment(plan.date_from, '00:00:00', plan.timezone)}'")

    if plan.date_to:
        constraints.append(f"{date_field} <= '{_moment(plan.date_to, '23:59:59', plan.timezone)}'")

    if plan.recency_days > 0 and not (plan.date_from or plan.date_to):
        cutoff = datetime.now(timezone.utc) - timedelta(days=plan.recency_days)
        constraints.append(f"modifiedTime > '{cutoff.isoformat()}'")

    if plan.ownership == "mine":
        constraints.append("'me' in owners")
    elif plan.ownership == "shared":
        # Someone actively shared it with them, not merely "not owned".
        constraints.append("sharedWithMe")

    if plan.starred:
        constraints.append("starred = true")

    # By email only: Drive matches nothing for a name ("'Alex Morgan' in
    # owners"), so a named person is matched locally instead.
    if plan.person_email:
        person = _escape(plan.person_email)
        constraints.append(f"('{person}' in owners or '{person}' in writers)")

    return constraints


def _moment(day: str, clock: str, zone: str) -> str:
    """A time on a day in the user's timezone, with its offset: Drive reads
    a time without one as UTC, and "yesterday" in India starts 5h30 early."""
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=utils.get_zoneinfo(zone)).isoformat()


def _people(item: dict) -> set[str]:
    """The words of the names and emails of whoever owns or shared a file."""
    people = list(item.get("owners") or [])

    if item.get("sharingUser"):
        people.append(item["sharingUser"])

    return {
        word
        for person in people
        for word in tokens(f"{person.get('displayName', '')} {person.get('emailAddress', '')}")
    }


def _involves(item: dict, plan: QueryPlan) -> bool:
    """Does the person the question names own or share this file? Every
    word of the name, each equal to or a prefix of one of theirs: "Alex"
    is Alexandra Morgan, alex.morgan@example.com."""
    wanted = [w for w in tokens(plan.person) if is_word(w)]
    known = _people(item)

    return bool(wanted) and all(
        any(word == mine or (len(word) >= 3 and mine.startswith(word)) for mine in known) for word in wanted
    )


def _distinct_terms(plan: QueryPlan) -> list[str]:
    seen: list[str] = []

    for group in plan.term_groups:
        for term in group:
            cleaned = term.strip()

            if cleaned and cleaned not in seen:
                seen.append(cleaned)

    return seen


def _widen(plan: QueryPlan) -> list[str]:
    """Single terms ORed, with every filter dropped: the recovery when the
    precise, filtered queries found nothing. One absent word sinks an ANDed
    group, and one wrong inferred filter hides everything; with no way to
    tell which, all of them are dropped."""
    terms = _distinct_terms(plan)

    if not terms:
        return []

    clauses = " or ".join(f"fullText contains '{_escape(t)}'" for t in terms[:8])

    return [f"({clauses}) and trashed = false"]


# A quoted span is an explicit instruction not to paraphrase.
_QUOTED = re.compile(r'"([^"]{2,})"|“([^”]{2,})”')


def literal_terms(query: str) -> tuple[list[str], list[str]]:
    """Quoted spans, and the question's own content words."""
    quoted = [
        (first or second).strip()
        for first, second in _QUOTED.findall(query)
        if (first or second).strip()
    ]

    # Quoted words are searched as phrases, not also as loose words.
    remainder = _QUOTED.sub(" ", query)

    words: list[str] = []

    for word in tokens(remainder):
        if is_word(word) and word not in STOPWORDS and word not in words:
            words.append(word)

    return quoted, words


def _literal_queries(query: str, words: Optional[list[str]] = None) -> tuple[list[str], list[str]]:
    """The person's own words, searched exactly as typed: queries for quoted
    spans, and for the loose words (``words``, or the question's first
    literal_max_terms).

    Every other query searches what the model decided the question meant,
    and the model is told to prefer the Drive's vocabulary over the asker's,
    so a word that appears literally in the right file can be paraphrased
    out. This runs alongside the expansion rather than as a fallback (the
    failure it catches is the expansion finding the WRONG thing), and with
    no filters but `trashed`, since filters are inferences too.
    """
    if not gdrive_config.literal_search_enabled:
        return [], []

    quoted, typed = literal_terms(query)
    words = typed if words is None else words

    phrases = [
        f"(fullText contains '{_escape(span)}' or name contains '{_escape(span)}') and trashed = false"
        for span in quoted[:2]
    ]

    if not words:
        return phrases, []

    terms = words[: gdrive_config.literal_max_terms]

    content = " and ".join(f"fullText contains '{_escape(t)}'" for t in terms)
    names = " or ".join(f"name contains '{_escape(t)}'" for t in terms)

    return phrases, [f"({content}) and trashed = false", f"({names}) and trashed = false"]


def _picked_words(query: str, plan: QueryPlan) -> Optional[list[str]]:
    """The question's own words that query understanding named as its
    subject, or None when it named none of them.

    Word order is a poor guide to what matters ("hello, im looking for the
    latest quarterly report" puts the subject fifth). The model chooses and never
    rewrites: a word counts only if it was typed in the question.
    """
    if not (gdrive_config.literal_keywords and plan.keywords):
        return None

    _, typed = literal_terms(query)
    allowed = set(typed)
    picked: list[str] = []

    for keyword in plan.keywords:
        for word in tokens(keyword):
            if word in allowed and word not in picked:
                picked.append(word)

    return picked[: gdrive_config.literal_max_terms] or None


def _keeps_loose_words(query: str, plan: QueryPlan) -> bool:
    """Whether loose-word verbatim hits count once the plan is known: an
    unquoted browse has no content words worth matching."""
    quoted, _ = literal_terms(query)

    return bool(plan.term_groups or quoted)


def _specific_words(query: str, vocabulary: PathVocabulary) -> list[str]:
    """The question's words that pick out a few files in this Drive, rarest
    first: a client's name, a trip, a person. A word in no path says nothing
    either way; a word in many paths narrows nothing."""
    if not gdrive_config.path_match_enabled:
        return []

    quoted, typed = literal_terms(query)
    words = typed + [w for span in quoted for w in tokens(span) if is_word(w) and w not in typed]

    limit = max(
        gdrive_config.rerank_pool,
        int(vocabulary.file_count * gdrive_config.specific_word_max_share),
    )

    # Stable, so typed order breaks ties.
    return sorted((w for w in words if 0 < vocabulary.count(w) <= limit), key=vocabulary.count)


def _by_rarity(typed: list[str], specific: list[str], vocabulary: PathVocabulary) -> list[str]:
    """The typed words in the order the verbatim pass takes them: specific
    first, then words no path uses, then common ones. The pass ANDs only its
    first few words, so this order decides what is searched at all."""
    rest = [w for w in typed if w not in specific]

    return (
        [w for w in specific if w in typed]
        + [w for w in rest if not vocabulary.count(w)]
        + [w for w in rest if vocabulary.count(w)]
    )


def _path_matches(specific: list[str], vocabulary: PathVocabulary) -> list[FileRef]:
    """Files whose folder path holds the question's specific words: most of
    them first, then the rarer, then the newest. Folders only; a word in the
    file's own name is already searched by Drive."""
    hits: dict[str, list] = {}

    for word in specific:
        for ref in vocabulary.in_folders.get(word, ()):
            hit = hits.setdefault(ref.id, [0, 0, ref])
            hit[0] += 1
            hit[1] += vocabulary.count(word)

    ranked = sorted(hits.values(), key=lambda h: h[2].modified_time or "", reverse=True)
    ranked.sort(key=lambda h: (-h[0], h[1]))

    return [ref for _, _, ref in ranked[: gdrive_config.path_match_max_files]]


def _keep_specific(plan: QueryPlan, specific: list[str], vocabulary: PathVocabulary) -> QueryPlan:
    """The plan, with a group of its own for the question's folder-naming
    words when the expansion left them out: the model can swap the client a
    question names for another client it saw listed. Two words at most."""
    named = [w for w in specific if vocabulary.in_folders.get(w)][:2]

    if not named or not plan.term_groups:
        return plan

    covered = _plan_words(plan)

    if all(w in covered for w in named):
        return plan

    kept = copy.copy(plan)
    # First, so the filename sweep's term cap never drops it.
    kept.term_groups = [named] + plan.term_groups

    return kept


def _without_folder(plan: QueryPlan) -> QueryPlan:
    """The plan, for a folder no folder in the Drive is called: its words
    become the search when they were the whole question. Left as a folder,
    "what's in my Old Projects folder" lists the Drive's newest files."""
    kept = copy.copy(plan)
    kept.folder = ""

    if not kept.term_groups:
        words = [w for w in tokens(plan.folder) if is_word(w) and w not in STOPWORDS]
        kept.term_groups = [words[:2]] if words else []

    return kept


# A name someone typed rather than a sentence: an extension, or the digits
# and underscores of a generated name.
_FILE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def looks_like_filename(query: str, names: set[str]) -> bool:
    """Is this query a filename rather than a question? One token with an
    extension, a digit or an underscore; or several words that are exactly a
    file's name in THIS Drive (``names``: every file's normalized name). A
    single plain word never is."""
    text = query.strip().strip("\"'“”")

    if not text or len(text) > 120:
        return False

    if not any(char.isspace() for char in text):
        # Digits and underscores mark a name only in ASCII: Chinese or
        # Japanese is written without spaces, so "2024年の請求書はどこ" is a
        # question, not a file.
        generated = text.isascii() and (any(char.isdigit() for char in text) or "_" in text)
        return bool(_FILE_EXTENSION.search(text) or generated)

    return len(tokens(text)) >= 2 and normalize(text) in names


def _filename_plan(query: str, names: set[str]) -> Optional[QueryPlan]:
    """A plan for a filename query, made without the model: there is nothing
    to expand, and the call costs about two seconds."""
    if not looks_like_filename(query, names):
        return None

    text = query.strip().strip("\"'“”")
    stem = _FILE_EXTENSION.sub("", text) or text

    return QueryPlan(term_groups=[[stem]], intent="entity")


async def _run_query(
    ctx: DriveContext, query: str, *, order_by: Optional[str] = None, page_size: Optional[int] = None
) -> Optional[list[dict]]:
    """The files a query matches; None when Drive refused it or never
    answered, which is not the same as matching nothing."""

    async def page(page_token: Optional[str] = None) -> dict:
        return await ctx.drive.call(
            lambda token: drive_client.list_files(
                token,
                query=query,
                fields=_DISCOVERY_FIELDS,
                page_size=page_size or gdrive_config.discovery_max_candidates,
                page_token=page_token,
                order_by=order_by,
            )
        )

    try:
        response = await page()

        # Searching every shared drive, Drive can return an empty first
        # page with more to come.
        if not response.get("files") and response.get("nextPageToken"):
            response = await page(response["nextPageToken"])
    except DriveRequestError as e:
        # One malformed or rejected query must not sink the others.
        logger.warning(f"Drive discovery query failed :: query={query[:80]!r}, error={e}")
        return None

    if response.get("incompleteSearch"):
        logger.info(f"Drive search did not cover every shared drive :: query={query[:80]!r}")

    return [item for item in response.get("files", []) if item.get("mimeType") not in _NOT_FILES]


@dataclass
class DriveHits:
    """What a batch of Drive queries found."""

    files: dict[str, dict] = field(default_factory=dict)
    # How many independent queries found each file: the agreement count.
    found_by: dict[str, int] = field(default_factory=dict)
    # How many of those were content queries rather than filename sweeps.
    content_by: dict[str, int] = field(default_factory=dict)
    # Queries that matched nothing, so a caller can relax just those.
    empty: set[str] = field(default_factory=set)
    # The words Drive found inside each file's contents (see _content_terms).
    content_terms: dict[str, set[str]] = field(default_factory=dict)
    # Queries sent, and those Drive refused or never answered.
    ran: int = 0
    failed: int = 0

    def merge(self, other: "DriveHits") -> None:
        for file_id, item in other.files.items():
            self.files.setdefault(file_id, item)

        for mine, theirs in ((self.found_by, other.found_by), (self.content_by, other.content_by)):
            for file_id, count in theirs.items():
                mine[file_id] = mine.get(file_id, 0) + count

        for file_id, terms in other.content_terms.items():
            self.content_terms.setdefault(file_id, set()).update(terms)

    def only(self, ids: set[str]) -> "DriveHits":
        """The part of these hits that is about ``ids``."""
        return DriveHits(
            files={i: f for i, f in self.files.items() if i in ids},
            found_by={i: n for i, n in self.found_by.items() if i in ids},
            content_by={i: n for i, n in self.content_by.items() if i in ids},
            content_terms={i: t for i, t in self.content_terms.items() if i in ids},
        )


_FULLTEXT_TERM = re.compile(r"fullText contains '((?:[^'\\]|\\.)*)'")


def _content_terms(query: str) -> list[str]:
    """The words a query proves are inside every file it matches: its
    fullText terms, when its search clause requires them all. A clause with
    an "or" (a name or the contents, any of several words) proves none."""
    clause = query.split(") and ", 1)[0]

    if " or " in clause:
        return []

    return [re.sub(r"\\(.)", r"\1", term) for term in _FULLTEXT_TERM.findall(clause)]


async def _search_drive(
    ctx: DriveContext, queries: list[str], *, order_by: Optional[str] = None, page_size: Optional[int] = None
) -> DriveHits:
    """Run every query at once."""
    hits = DriveHits(ran=len(queries))

    if not queries:
        return hits

    results = await asyncio.gather(*(_run_query(ctx, q, order_by=order_by, page_size=page_size) for q in queries))

    for query, batch in zip(queries, results):
        in_content = "fullText contains" in query
        terms = _content_terms(query)

        if batch is None:
            hits.failed += 1
            continue

        if not batch:
            hits.empty.add(query)

        for item in batch:
            file_id = item.get("id")

            if not file_id:
                continue

            hits.found_by[file_id] = hits.found_by.get(file_id, 0) + 1
            hits.files.setdefault(file_id, item)

            if in_content:
                hits.content_by[file_id] = hits.content_by.get(file_id, 0) + 1

            if terms:
                hits.content_terms.setdefault(file_id, set()).update(terms)

    return hits


async def find_by_name(ctx: DriveContext, typed: str) -> list[FileRef]:
    """Files whose names hold every word of a typed name, by Drive's own
    name search: for a name the cached listing lacks."""
    words = [w for w in tokens(normalize(typed)) if is_word(w)][: gdrive_config.literal_max_terms]

    if not words:
        return []

    clause = " and ".join(f"name contains '{_escape(w)}'" for w in words)
    items = await _run_query(ctx, f"{clause} and trashed = false") or []
    folders = await get_folders(ctx)

    return collapse_duplicates([_to_ref(item, folders) for item in items])


async def _search_and_prefetch(ctx: DriveContext, queries: list[str], folders: dict[str, str]) -> DriveHits:
    """A verbatim search, then a head start on reading what it found: the
    reranker reads every verbatim hit, so the download overlaps the model
    call instead of queueing behind it."""
    hits = await _search_drive(ctx, queries)

    if hits.files:
        newest = sorted(
            hits.files.values(), key=lambda item: item.get("modifiedTime") or "", reverse=True
        )[: gdrive_config.literal_max_results]
        prefetch(ctx, [_to_ref(item, folders) for item in newest])

    return hits


def _to_ref(item: dict, folders: dict[str, str]) -> FileRef:
    """Drive's JSON plus the path of its folder (``folders``: folder id ->
    path, from the cached listing). By folder rather than by file, so a file
    the listing does not hold (newer than it, or older than a large Drive's
    newest files) still gets its path."""
    parents = item.get("parents") or []

    return FileRef(
        id=item.get("id", ""),
        name=item.get("name", ""),
        mime_type=item.get("mimeType", ""),
        size=int(item.get("size") or 0),
        modified_time=item.get("modifiedTime", ""),
        md5=item.get("md5Checksum", ""),
        folder=folders.get(parents[0], "") if parents else "",
        web_view_link=item.get("webViewLink", ""),
    )


def _query_words(query: str) -> set[str]:
    # Not "my" or "the": compared with every filename, they match a quarter of a Drive.
    return {w for w in tokens(query) if is_word(w) and w not in STOPWORDS}


def _plan_words(plan: QueryPlan) -> set[str]:
    return {w for group in plan.term_groups for term in group for w in tokens(term) if is_word(w)}


def _fuzzy_name_hits_sync(index: ListingIndex, listing: list[FileRef], words: set[str]) -> list[FileRef]:
    """Files whose NAME nearly matches a query word: Drive's `name contains`
    is an exact substring test, so "receipt" never finds a file
    saved as reciept_march.jpg. In listing order (newest first)."""
    if not words:
        return []

    cutoff = gdrive_config.discovery_name_fuzzy_cutoff
    matcher = difflib.SequenceMatcher()
    hit: set[str] = set()

    # Each distinct filename word once: a Drive's names share most of theirs.
    for token, refs in index.words.items():
        matched = False

        for word in words:
            # Exact containment is already Drive's job.
            if word == token:
                matched = True
                break

            matcher.set_seq1(token)
            matcher.set_seq2(word)

            if (
                matcher.real_quick_ratio() >= cutoff
                and matcher.quick_ratio() >= cutoff
                and matcher.ratio() >= cutoff
            ):
                matched = True
                break

        if matched:
            hit.update(ref.id for ref in refs)

    return [ref for ref in listing if ref.id in hit]


async def _fuzzy_name_hits(ctx: DriveContext, words: set[str]) -> list[FileRef]:
    """Runs against the cached listing, off the event loop: it compares
    every filename word in the Drive."""
    if not words:
        return []

    return await asyncio.to_thread(_fuzzy_name_hits_sync, await listing_index(ctx), await get_names(ctx), words)


def _prefilter(
    query: str, plan: QueryPlan, candidates: list[Candidate], specific: Iterable[str] = ()
) -> list[Candidate]:
    """Cheap local ordering, to cut the pool down before anything is read.
    Not trying to be right; trying to make sure the genuinely good files are
    inside the shortlist."""
    terms = {t.lower() for group in plan.term_groups for t in group if is_word(t)}
    terms |= {w for w in tokens(query) if is_word(w)}

    newest = max((c.ref.modified_time for c in candidates if c.ref.modified_time), default="")

    folder_words = {w for w in tokens(plan.folder) if is_word(w)}
    specific = set(specific)

    for candidate in candidates:
        ref = candidate.ref

        name_words = set(tokens(normalize(ref.name)))
        path_words = set(tokens(ref.folder))

        # A folder word is shared by everything in the folder, so it says
        # little about one file, unless it is one of the question's specific
        # words. Still less than a filename match.
        named_by = specific & path_words

        score = 2.0 * candidate.agreement
        score += 3.0 * len(terms & name_words)
        score += 1.0 * len((terms & path_words) - named_by)
        score += 2.0 * len(named_by)

        # A near-miss filename is worth roughly one exact term match.
        if candidate.fuzzy_name:
            score += 3.0

        # The person the question names: a strong hint, never a filter,
        # since "the letter from Rahul's employer" is about Rahul.
        if candidate.by_person:
            score += 3.0

        # A verbatim hit provably contains what was typed: enough to reach
        # the shortlist on its own, not to win.
        if candidate.exact:
            score += gdrive_config.literal_prefilter_boost

        # A named folder boosts rather than filters: a half-remembered
        # folder should cost ranking, never recall.
        if candidate.in_folder or (folder_words and folder_words <= path_words):
            score += 4.0

        # Breaks ties without outranking a real content match.
        if ref.modified_time and ref.modified_time == newest:
            score += 0.5

        if ref.size and ref.size < gdrive_config.min_useful_file_bytes:
            score -= 5.0

        # Templated names cost rank, never presence, and not at all when the
        # question names the folder that tells them apart.
        if candidate.template and not named_by:
            score -= gdrive_config.template_name_penalty

        candidate.prefilter_score = score

    candidates.sort(key=lambda c: -c.prefilter_score)

    shortlist = _reserve_content(candidates, gdrive_config.discovery_shortlist)

    # The newest (oldest, largest) files go to the reranker whatever their
    # names say: the newest of thirty payslips scores like the other 29.
    if plan.order:
        chosen = {id(c) for c in shortlist}
        first = [c for c in apply_order(plan, candidates)[:_ORDER_SLOTS] if id(c) not in chosen]

        if first:
            shortlist = sorted(shortlist[: len(shortlist) - len(first)] + first, key=lambda c: -c.prefilter_score)

    return shortlist


def _reserve_content(ranked: list[Candidate], limit: int) -> list[Candidate]:
    """The top ``limit``, with some slots held for content matches.

    Every prefilter score rewards the filename, so a letter saved as
    "Untitled document", found by what it says, ranked below a dozen files
    whose names merely resembled the question and was cut before anything
    read it. Held slots go to the content matches the plain ranking left
    out; unneeded slots return to the plain ranking.
    """
    reserve = min(gdrive_config.discovery_content_slots, limit)
    head = ranked[: limit - reserve]
    rest = ranked[limit - reserve:]

    held = sorted((c for c in rest if c.content_hits), key=lambda c: -c.content_hits)[:reserve]

    held_ids = {id(c) for c in held}
    fill = [c for c in rest if id(c) not in held_ids][: reserve - len(held)]

    return sorted(head + held + fill, key=lambda c: -c.prefilter_score)


def apply_order(plan: QueryPlan, candidates: list[Candidate]) -> list[Candidate]:
    """Re-sort for a superlative. An ordering, not a filter: reading "the
    latest" as a date cutoff returns nothing whenever the latest matching
    file is older than the cutoff."""
    if not plan.order or not candidates:
        return candidates

    if plan.order == "newest":
        key, reverse = (lambda c: c.ref.modified_time or ""), True
    elif plan.order == "oldest":
        # No timestamp sorts last either way.
        key, reverse = (lambda c: c.ref.modified_time or "9999"), False
    elif plan.order == "largest":
        key, reverse = (lambda c: c.ref.size or 0), True
    else:
        return candidates

    return sorted(candidates, key=key, reverse=reverse)


def verdict(plan: QueryPlan, ranked: list[Candidate]) -> str:
    """How sure are we, really?

    A question naming one document is broken by ambiguity; a question about
    a topic is served by several good sources, so its required gap is
    relaxed.
    """
    if not ranked or ranked[0].score < gdrive_config.confidence_min_score:
        return LOST

    if len(ranked) == 1:
        return CONFIDENT

    gap = ranked[0].score - ranked[1].score
    required = gdrive_config.confidence_min_gap

    if not plan.is_entity:
        required *= gdrive_config.confidence_topical_gap_factor

    if gap >= required:
        return CONFIDENT

    # One right file exists and the ranking cannot single it out.
    if plan.is_entity:
        return LOST

    return UNCERTAIN


async def discover(ctx: DriveContext, query: str) -> Discovery:
    """Find candidate files for a question.

    A browse comes back listed and final. Anything with a subject comes back
    as an unscored shortlist for content reranking to judge.
    """
    # The user's own folder and filename vocabulary, so expansion is grounded
    # in this Drive.
    profile = await corpus_profile(ctx)

    index = await listing_index(ctx)
    folders = await get_folders(ctx)

    vocabulary = await path_vocabulary(ctx)
    specific = _specific_words(query, vocabulary)

    # Everything that needs only the typed words starts before the plan
    # exists, so the model's ~2s is spent searching rather than waiting.
    quoted, typed = literal_terms(query)

    if gdrive_config.path_match_enabled:
        typed = _by_rarity(typed, specific, vocabulary)

    phrases, typed_loose = _literal_queries(query, typed)

    early: list[asyncio.Task] = []

    def start(coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        early.append(task)
        return task

    try:
        verbatim_phrases = start(_search_and_prefetch(ctx, phrases, folders))
        verbatim_typed = start(_search_and_prefetch(ctx, typed_loose, folders))

        plan = _filename_plan(query, index.names)

        if plan is None:
            plan = _keep_specific(await understand_query(ctx, query, profile), specific, vocabulary)

        logger.info(f"Drive query plan :: query={query!r}, plan={plan}")

        # Nothing to search for or list by ("check my drive"): the handler
        # asks what to look for.
        if plan.is_empty:
            return Discovery(query=query, plan=plan)

        # A named folder becomes a search of that folder, beside the others.
        # For a question about the folder alone it replaces the filter-only
        # listing.
        named = match_folders(plan.folder, folders) if plan.folder else {}

        if plan.folder and not named:
            plan = _without_folder(plan)

        browse = not plan.term_groups
        order_by = _DRIVE_ORDER.get(plan.order)
        queries = _build_queries(plan)

        # A person is routinely a misreading ("the letter from Rahul's
        # employer" is ABOUT Rahul), so the same searches also run without
        # the person; files that do involve them rank higher by agreeing.
        # Only for an email, the one form Drive filters by; and not for a
        # browse, which lists that person's files or none.
        searches_without_person = bool(plan.person_email) and not browse

        if searches_without_person:
            queries += _build_queries(copy.copy(plan).without_person())

        if named:
            in_folder = [_folder_query(named, plan)]

            if searches_without_person:
                in_folder.append(_folder_query(named, copy.copy(plan).without_person()))

            queries = in_folder if browse else queries + in_folder

        # A second verbatim search on the subject words the model picked,
        # when they differ from the typed words.
        picked = _picked_words(query, plan)
        typed_words = typed[: gdrive_config.literal_max_terms]
        verbatim_picked = None

        if picked and set(picked) != set(typed_words):
            verbatim_picked = start(_search_and_prefetch(ctx, _literal_queries(query, picked)[1], folders))

        # Spelling-tolerant name matching on the subject words: on every word
        # of a long message it matched a quarter of a Drive. Never for a
        # browse: a name resembling "my starred photos" is not starred.
        if browse:
            name_words = set()
        else:
            name_words = _query_words(query) if not picked else set(picked) | {
                word for span in quoted for word in tokens(span) if is_word(word)
            }

        names = start(_fuzzy_name_hits(ctx, name_words))
        # Words the plan added that those did not cover, at the same time.
        extra_words = _plan_words(plan) - name_words
        extra_names = start(_fuzzy_name_hits(ctx, extra_words))

        keep_loose = bool(typed_words) and _keeps_loose_words(query, plan)

        if not queries and not phrases and not keep_loose:
            return Discovery(query=query, plan=plan)

        # A browse for a named person reads further: Drive cannot filter by
        # a name, so their files are picked out of a longer list here.
        page_size = gdrive_config.discovery_person_browse_files if browse and plan.person else None
        found = await _search_drive(ctx, queries, order_by=order_by, page_size=page_size)
        unanswered = found.ran > 0 and found.failed == found.ran

        if browse and plan.person and not plan.person_email:
            found = found.only({file_id for file_id, item in found.files.items() if _involves(item, plan)})

        matched = len(found.files)

        exact = await verbatim_phrases
        typed_only = DriveHits()

        if keep_loose:
            typed_hits = await verbatim_typed

            if verbatim_picked is None:
                exact.merge(typed_hits)
            else:
                # The typed words of a chatty question find whatever their
                # greeting prefixes ("im" matches every IMG_ file), so a
                # typed-word hit is boosted only when the picked words or
                # the plan found it too; the rest have to earn a place.
                exact.merge(await verbatim_picked)
                agreed = set(exact.files) | set(found.files)
                exact.merge(typed_hits.only(agreed & set(typed_hits.files)))
                typed_only = typed_hits.only(set(typed_hits.files) - agreed)

        # Every search refused or unanswered: Drive is down or failing, which
        # "nothing matched" would misreport.
        if unanswered and not exact.files and not typed_only.files:
            raise DriveUnavailable(f"Every Drive search failed :: {found.failed} of {found.ran}")

        fuzzy_hits = await names
        seen = {ref.id for ref in fuzzy_hits}
        fuzzy_hits += [ref for ref in await extra_names if ref.id not in seen]
    finally:
        # Prefetches started by the verbatim searches outlive the question
        # on purpose; only searches nobody will read are stopped.
        for task in early:
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                # Marks a failure as seen when an earlier error ended the search.
                task.exception()

    # Files under a folder the question's specific words name. Only for a
    # question with a subject and no filters: nothing here passes Drive's
    # date, type or starred clauses.
    path_hits = _path_matches(specific, vocabulary) if plan.term_groups and not plan.has_filters else []

    # Relaxed pairs for the groups that found nothing, whatever the others
    # found: one long group dies on a single word while a vaguer group
    # succeeds on the wrong files.
    relaxed = _relax(plan, found.empty)

    if searches_without_person:
        relaxed += _relax(copy.copy(plan).without_person(), found.empty)

    if relaxed:
        found.merge(await _search_drive(ctx, relaxed, order_by=order_by))

    files, found_by, content_by, inside = found.files, found.found_by, found.content_by, found.content_terms
    exact_files, exact_content = exact.files, exact.content_by

    # Degrade one step at a time: filters first (a wrong "last month" is a
    # likelier cause of nothing than absent words), then term precision.
    # Verbatim hits count as results here.
    if not files and not exact_files and plan.has_filters:
        logger.info(f"No Drive hits with filters, retrying unfiltered :: query={query!r}")
        retry = await _search_drive(ctx, _build_queries(plan, filtered=False), order_by=order_by)
        files, found_by, content_by, inside = retry.files, retry.found_by, retry.content_by, retry.content_terms

    if not files and not exact_files:
        retry = await _search_drive(ctx, _widen(plan), order_by=order_by)
        files, found_by, content_by, inside = retry.files, retry.found_by, retry.content_by, retry.content_terms

    exact_ids = set(exact_files)

    for file_id, item in exact_files.items():
        files.setdefault(file_id, item)
        # No expansion agreed with it; the prefilter boost makes its case.
        found_by.setdefault(file_id, 0)

    # Typed-word hits nothing else found: ordinary candidates, after the
    # fallbacks above so they never stand in for a real result.
    for file_id, item in typed_only.files.items():
        if file_id not in files:
            files[file_id] = item
            found_by[file_id] = 0
            content_by[file_id] = typed_only.content_by.get(file_id, 0)

    if not files and not path_hits:
        return Discovery(query=query, plan=plan)

    # A file created since the last listing refresh simply has no folder
    # context: one ranking signal less, not an exclusion.
    templates = index.templates
    named_paths = set(named.values())

    candidates: list[Candidate] = []
    seen_ids: set[str] = set()

    for file_id, item in files.items():
        ref = _to_ref(item, folders)

        if is_noise(ref.name, ref.folder):
            continue

        seen_ids.add(file_id)
        candidates.append(
            Candidate(
                ref=ref,
                agreement=found_by[file_id],
                content_hits=content_by.get(file_id, 0) + exact_content.get(file_id, 0),
                exact=file_id in exact_ids,
                template=ref.normalized in templates,
                in_folder=ref.folder in named_paths,
                content_terms=sorted(
                    set().union(*(hits.get(file_id, ()) for hits in (inside, exact.content_terms, typed_only.content_terms)))
                ),
                by_person=bool(plan.person) and _involves(item, plan),
            )
        )

    # Names Drive could not match on spelling, added from the cache.
    by_id = {c.ref.id: c for c in candidates}

    for ref in fuzzy_hits:
        if ref.id in seen_ids:
            by_id[ref.id].fuzzy_name = True
            continue

        seen_ids.add(ref.id)
        candidate = Candidate(
            ref=ref,
            agreement=0,
            fuzzy_name=True,
            template=ref.normalized in templates,
            in_folder=ref.folder in named_paths,
        )
        candidates.append(candidate)
        by_id[ref.id] = candidate

    # Files under a folder the question names that no search found.
    for ref in path_hits:
        if ref.id in seen_ids:
            continue

        seen_ids.add(ref.id)
        candidates.append(
            Candidate(
                ref=ref,
                agreement=0,
                template=ref.normalized in templates,
                in_folder=ref.folder in named_paths,
            )
        )

    considered = len(candidates)

    # Byte-identical copies are one option, not six.
    by_id = {c.ref.id: c for c in candidates}
    candidates = [
        by_id[ref.id]
        for ref in collapse_duplicates(
            [c.ref for c in candidates],
            keep="oldest" if plan.order == "oldest" else "newest",
        )
    ]

    if not candidates:
        return Discovery(query=query, plan=plan, considered=considered)

    # Captured before the shortlist cut and any ranking: a verbatim hit is
    # reported on its own terms.
    exact_hits = sorted((c for c in candidates if c.exact), key=lambda c: c.ref.modified_time or "", reverse=True)

    # A typed word in a file's NAME was put there deliberately by a person;
    # one in the body can be an incidental mention.
    literal_words = set(typed)

    for span in quoted:
        literal_words |= set(tokens(span))

    for candidate in exact_hits:
        candidate.exact_in_name = bool(literal_words & set(tokens(normalize(candidate.ref.name))))

    exact_hits.sort(key=lambda c: -c.agreement)
    exact_hits.sort(key=lambda c: not c.exact_in_name)
    exact_hits = exact_hits[: gdrive_config.literal_max_results]

    shortlist = _prefilter(query, plan, candidates, specific)

    # A browse has no subject to be relevant TO: the filters did the work
    # and the ordering is the only decision left, so no model is asked.
    if browse:
        # A question about a folder alone is answered by what is in it.
        # Otherwise everything the filters let through, in the order asked:
        # the shortlist was cut on name overlap, which a browse has none of.
        pool = [c for c in candidates if c.in_folder] or candidates

        listed = apply_order(plan, pool) if plan.order else sorted(
            pool, key=lambda c: c.ref.modified_time or "", reverse=True
        )

        for candidate in listed:
            candidate.score = 10.0

        return Discovery(
            query=query,
            plan=plan,
            verdict=CONFIDENT,
            selected=listed[: gdrive_config.discovery_max_files],
            considered=considered,
            exact_matches=exact_hits,
            shortlist=listed,
            matched=matched,
        )

    # Unjudged: the reranker reads these and decides.
    return Discovery(
        query=query,
        plan=plan,
        verdict=UNCERTAIN,
        considered=considered,
        exact_matches=exact_hits,
        shortlist=shortlist,
    )
