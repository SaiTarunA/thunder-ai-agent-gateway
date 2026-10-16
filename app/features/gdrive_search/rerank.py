"""Content reranking: read the candidates, then order them.

Discovery ranks on metadata alone, which is a good filter and a weak judge:
a file named "Untitled document" can be the answer, and one named after the
question an empty template. This stage reads a passage from each candidate
and scores it on what it says, and is the only stage that judges relevance.
The passage is chosen, not truncated: Drive returns no snippets, and a
file's opening is usually a cover page.

Files with no readable text are still scored, on name, folder and type. A
scoring call that fails after its retries fails the search.
"""

import bisect
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from app.ai.prompts.gdrive_search import GDRIVE_RERANK_CONSTANTS
from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import DriveContext
from app.features.gdrive_search.content import FetchedFile, fetch_files, prefetch_native, reads_natively
from app.features.gdrive_search.discovery import Candidate, apply_order, literal_terms
from app.features.gdrive_search.judge import STOPWORDS, QueryPlan, ask
from app.features.gdrive_search.names import FileRef, is_word, tokens

logger = logging.getLogger(__name__)

# One "index,score" line per file: a few tokens each, where JSON objects were
# mostly keys and punctuation, and a bare list of numbers was read as a
# ranking and came back as file positions.
_SCORE_LINE = re.compile(r"^\s*(\d+)\s*,\s*(\d+(?:\.\d+)?)\s*$", re.M)

# The Responses API rejects a smaller max_output_tokens.
_MIN_OUTPUT_TOKENS = 16

_WHITESPACE = re.compile(r"\s+")
_SPACES = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n\s*\n")


def question_terms(query: str, plan: Optional[QueryPlan] = None) -> set[str]:
    """The question's own words and the expansion's, for placing a window."""
    quoted, bare = literal_terms(query)
    terms = set(bare)

    for span in quoted:
        terms.update(tokens(span))

    for group in plan.term_groups if plan else ():
        for term in group:
            terms.update(tokens(term))

    return {t for t in terms if is_word(t) and t not in STOPWORDS}


def _tidy(text: str, flatten: bool) -> str:
    if flatten:
        return _WHITESPACE.sub(" ", text).strip()

    # Rows of a table stay rows: a CSV export on one line is unreadable.
    text = _SPACES.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))

    return _BLANK_LINES.sub("\n\n", text).replace(" \n", "\n").replace("\n ", "\n").strip()


def best_passage(text: str, terms: set[str], size: int, *, flatten: bool = True, table: bool = False) -> str:
    """The window of ``size`` characters holding the most distinct terms,
    total hits breaking ties: one mention each of "invoice", "march" and
    "overdue" beats forty of "invoice". With no term anywhere, the opening.
    ``flatten`` puts it on one line; otherwise its line breaks are kept.
    A ``table``'s window from below the top is led by its header row,
    without which its values are numbers under no column."""
    text = _tidy(text or "", flatten)

    if len(text) <= size:
        return text

    lowered = text.casefold()
    hits: dict[str, list[int]] = {}

    for term in terms:
        found = [m.start() for m in re.finditer(re.escape(term), lowered)]

        if found:
            hits[term] = found

    if not hits:
        return text[:size].rsplit(" ", 1)[0] + " …"

    best_start, best_key = 0, (-1, -1)

    for anchors in hits.values():
        for anchor in anchors[:200]:
            # A little context before the first hit reads better than a
            # passage starting mid-sentence on the matched word.
            start = max(0, min(anchor - size // 5, len(text) - size))
            end = start + size

            distinct = total = 0

            for positions in hits.values():
                inside = bisect.bisect_left(positions, end) - bisect.bisect_left(positions, start)

                if inside:
                    distinct += 1
                    total += inside

            if (distinct, total) > best_key:
                best_key, best_start = (distinct, total), start

    passage = text[best_start: best_start + size]
    head = _table_head(text, best_start) if table and not flatten and best_start > 0 else ""

    return head + ("… " if best_start > 0 else "") + passage + (" …" if best_start + size < len(text) else "")


# The spreadsheet reader starts each tab with this line, then its rows.
_SHEET = "Sheet: "


def _table_head(text: str, start: int) -> str:
    """The lines naming the columns of the table ``start`` is in: its tab's
    name and first row, or a CSV's first line. Empty when they are inside
    the passage already."""
    tab = text.rfind("\n" + _SHEET, 0, start)
    begin = tab + 1 if tab >= 0 else 0
    lines = 2 if text.startswith(_SHEET, begin) else 1

    end = begin - 1

    for _ in range(lines):
        end = text.find("\n", end + 1)

        if end < 0:
            return ""

    if end >= start:
        return ""

    return text[begin:end][:500] + "\n"


def describe(ref: FileRef) -> str:
    bits = [f"name: {ref.name}"]

    if ref.folder:
        bits.append(f"folder: {ref.folder}")

    kind = (ref.mime_type or "").rsplit(".", 1)[-1].rsplit("/", 1)[-1]

    if kind:
        bits.append(f"type: {kind}")

    if ref.modified_time:
        bits.append(f"modified: {ref.modified_time[:10]}")

    if ref.size:
        bits.append(f"size: {ref.size // 1024}KB")

    return " | ".join(bits)


@dataclass
class Reranked:
    """What the reranker kept, and what it read and nearly kept."""

    # At most rerank_max_results, each scoring at least rerank_min_score.
    kept: list[Candidate] = field(default_factory=list)
    # Scored below that floor but within the rubric's "same area" band, best
    # first: what to offer as "did you mean one of these?" when nothing was kept.
    near_misses: list[Candidate] = field(default_factory=list)
    # Scans and photos not kept that Drive's search matched inside: scored
    # here on their names and Drive's words alone, so the answer, which
    # reads them as the files themselves, gets them too. Most words first.
    unread_matches: list[Candidate] = field(default_factory=list)


async def rerank(ctx: DriveContext, query: str, plan: QueryPlan, pool: list[Candidate]) -> Reranked:
    """Read each candidate and return the most useful, best first."""
    pool = pool[: gdrive_config.rerank_pool]

    if not pool:
        return Reranked()

    fetched = await fetch_files(ctx, [c.ref for c in pool], timeout=gdrive_config.rerank_fetch_timeout_seconds)

    pool, fetched = _drop_identical(pool, fetched)

    # A scan or photo kept here is read by the answer as the file itself;
    # its download overlaps this stage's model call. Those Drive matched
    # inside come first: they are the likeliest to be kept or pooled.
    # (A file still downloading at the deadline may well have text.)
    unreadable = [c for c, got in zip(pool, fetched) if not got.text and got.error != "timed out"]
    prefetch_native(ctx, [c.ref for c in sorted(unreadable, key=lambda c: not c.content_terms)])

    terms = question_terms(query, plan)
    blocks = []

    for index, (candidate, got) in enumerate(zip(pool, fetched)):
        candidate.excerpt = best_passage(got.text, terms, gdrive_config.rerank_excerpt_chars) if got.text else ""

        if candidate.excerpt:
            body = candidate.excerpt
        elif candidate.content_terms:
            # A scan or photo has no text here, but Drive read it: what its
            # search found inside says more than the file's name.
            body = f"(no readable text; Drive's search found these words inside it: {', '.join(candidate.content_terms)})"
        else:
            body = "(no readable text; judge on metadata)"

        blocks.append(f"[{index}] {describe(candidate.ref)}\nPASSAGE: {body}")

    # Only the files worth keeping, when set: a shorter reply, the rest 0.
    listed = gdrive_config.rerank_list_min_score
    instructions = None

    if listed is not None:
        instructions = GDRIVE_RERANK_CONSTANTS["instructions_listed"].replace("{min_score}", f"{listed:g}")

    def read(response) -> dict[int, float]:
        text = response.text or ""
        scores = _scores(text, len(pool))
        # A listed reply with no file worth listing is the header alone.
        if not scores and not (listed is not None and "index" in text.lower()):
            raise ValueError(f"no score lines :: {text[:200]!r}")
        return scores

    scores = await ask(
        ctx,
        "rerank",
        f"QUESTION: {query}\n\nFILES:\n\n" + "\n\n".join(blocks),
        read,
        instructions=instructions,
        max_output_tokens=max(
            _MIN_OUTPUT_TOKENS, GDRIVE_RERANK_CONSTANTS["output_tokens_per_line"] * (len(pool) + 1)
        ),
    )

    if listed is None and len(scores) < len(pool):
        logger.info(f"Drive rerank scored {len(scores)} of {len(pool)} files; the rest score 0")

    for index, candidate in enumerate(pool):
        candidate.score = scores.get(index, 0.0)

    # Stable on the incoming order, so equal scores keep discovery's order.
    ordered = sorted(pool, key=lambda c: -c.score)

    kept = _order_kept(plan, [c for c in ordered if c.score >= gdrive_config.rerank_min_score])
    kept = kept[: gdrive_config.rerank_max_results]
    near = [
        c for c in ordered
        if gdrive_config.disambiguation_min_score <= c.score < gdrive_config.rerank_min_score
    ]

    unread = {c.ref.id for c, got in zip(pool, fetched) if not got.text} - {c.ref.id for c in kept}
    unread_matches = sorted(
        (c for c in ordered if c.ref.id in unread and c.content_terms and reads_natively(c.ref)),
        key=lambda c: -len(c.content_terms),
    )

    return Reranked(
        kept=kept,
        near_misses=near[: gdrive_config.disambiguation_max_options],
        unread_matches=unread_matches[: gdrive_config.answer_ocr_max_files],
    )


def _scores(reply: str, size: int) -> dict[int, float]:
    """Index -> score from the "index,score" lines. Anything else (the
    header, a code fence, a stray sentence) is skipped; an index out of range
    is ignored and a repeated one keeps its first score."""
    scores: dict[int, float] = {}

    for index, score in _SCORE_LINE.findall(reply or ""):
        index = int(index)

        if 0 <= index < size and index not in scores:
            scores[index] = max(0.0, min(10.0, float(score)))

    return scores


def _drop_identical(pool: list[Candidate], fetched: list[FetchedFile]) -> tuple[list, list]:
    """One entry per distinct text, first copy kept. Google Docs have no md5,
    so four copies of one Doc reach this stage and could fill every result
    slot; with their text in hand they can finally be told apart."""
    seen: set[str] = set()
    kept_pool, kept_fetched = [], []

    for candidate, got in zip(pool, fetched):
        if got.text:
            digest = hashlib.sha1(_WHITESPACE.sub(" ", got.text).strip().casefold().encode()).hexdigest()

            if digest in seen:
                continue

            seen.add(digest)

        kept_pool.append(candidate)
        kept_fetched.append(got)

    return kept_pool, kept_fetched


def _order_kept(plan: QueryPlan, kept: list[Candidate]) -> list[Candidate]:
    """A superlative ("my latest invoice") applied after relevance: only
    the substantially useful files are reordered, so a newer, merely related
    file does not jump ahead of the one asked for."""
    if not plan.order:
        return kept

    strong = [c for c in kept if c.score >= gdrive_config.confidence_min_score]

    if not strong:
        return apply_order(plan, kept)

    rest = [c for c in kept if c.score < gdrive_config.confidence_min_score]

    return apply_order(plan, strong) + rest
