"""The model-backed decisions in Drive search. None of them sees file content:

  understand_query   question -> Drive search terms, filters and intent
  match_filename     which file the user meant by a name typed from memory

Every call goes through model_router (so it is billed). A reply that cannot
be read is asked for again, up to gdrive_config.model_attempts times in all,
and then the search fails with the error, as other features do.
"""

import logging
from datetime import datetime
from typing import Callable, Optional, Type, TypeVar, Union

from pydantic import BaseModel

from app.ai.config_builder import ai_config_builder
from app.ai.prompts.gdrive_search import (
    GDRIVE_MATCH_FILENAME_CONSTANTS,
    GDRIVE_UNDERSTAND_CONSTANTS,
)
from app.ai.response import AIResponse
from app.ai.router import model_router
from app.ai.tools import parse_tool_call_arguments, pydantic_model_to_openai_tool
from app.core.utils import utils
from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import DriveContext
from app.features.gdrive_search.names import CorpusProfile, FileRef, tokens
from app.features.gdrive_search.schemas import (
    FilenameMatches,
    QueryUnderstanding,
    QueryUnderstandingWithKeywords,
)

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)
R = TypeVar("R")


async def generate(
    ctx: DriveContext,
    stage: str,
    user_input: Union[str, list[dict]],
    *,
    instructions: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    tool: Optional[dict] = None,
) -> AIResponse:
    """One model call for a search stage, billed under the request's
    identity. ``tool`` makes the reply that function's arguments. A failed
    call raises; the SDK has already retried it."""
    config = await ai_config_builder.prepare_gdrive_search_config(stage)

    if config is None:
        raise ValueError(f"No model settings for Drive search stage :: {stage}")

    request_data = {**ctx.billing, **config, "user_query": user_input}

    if instructions is not None:
        request_data["instructions"] = instructions
    if max_output_tokens is not None:
        request_data["max_output_tokens"] = max_output_tokens
    if tool is not None:
        request_data.update(tools=[tool], tool_choice="required", parallel_tool_calls=False)

    return await model_router.generate(request_data)


async def ask(
    ctx: DriveContext,
    stage: str,
    user_input: Union[str, list[dict]],
    read: Callable[[AIResponse], R],
    **kwargs,
) -> R:
    """A stage's model call, read into its result. A reply ``read`` rejects
    with ValueError (JSON and validation errors included) is asked for again,
    up to gdrive_config.model_attempts times in all, then raised."""
    attempts = max(1, gdrive_config.model_attempts)

    for attempt in range(1, attempts + 1):
        response = await generate(ctx, stage, user_input, **kwargs)

        try:
            return read(response)
        except ValueError as e:
            logger.warning(
                f"Drive search {stage} reply unusable, attempt {attempt}/{attempts} :: {e}, "
                f"agentid :: {ctx.billing.get('agentid')}"
            )

    raise ValueError(f"Drive search {stage} returned no usable reply")


async def structured(
    ctx: DriveContext,
    stage: str,
    user_input: Union[str, list[dict]],
    model: Type[M],
    read: Callable[[M], R],
    *,
    description: str,
    instructions: Optional[str] = None,
) -> R:
    """A reply in the shape of ``model``, as a forced tool call validated the
    way intent detection validates its own, then ``read`` into the result."""
    tool = pydantic_model_to_openai_tool(model, name=f"gdrive_{stage}", description=description)

    def parse(response: AIResponse) -> R:
        if not response.tool_calls:
            raise ValueError("no tool call in the reply")
        return read(parse_tool_call_arguments(model, response.tool_calls[0].arguments))

    return await ask(ctx, stage, user_input, parse, instructions=instructions, tool=tool)


# ---------------------------------------------------------------------
# 1. Query understanding
# ---------------------------------------------------------------------


class QueryPlan:
    """What to ask Drive, and how to judge what comes back.

    Everything past ``term_groups`` NARROWS the search and is a recall risk as
    much as a precision gain: a wrongly inferred "last month" hides a
    two-year-old file completely. Discovery applies them first and drops them
    wholesale on an empty result.
    """

    def __init__(
        self,
        term_groups: list[list[str]],
        intent: str = "topic",
        file_type: str = "any",
        recency_days: int = 0,
        person: str = "",
        *,
        # Which timestamp a date range applies to: modified, created, viewed.
        date_field: str = "modified",
        date_from: str = "",
        date_to: str = "",
        # any, mine (owned), shared (shared with them)
        ownership: str = "any",
        # A folder the question names, matched locally against cached paths.
        folder: str = "",
        starred: bool = False,
        # A superlative is an ordering, not a filter: newest, oldest, largest.
        order: str = "",
        # The question's subject words as the model picked them, for the
        # verbatim pass. Unvalidated here; see discovery._picked_words.
        keywords: Optional[list[str]] = None,
        # The user's, which the dates were resolved in: Drive reads a time
        # without an offset as UTC.
        timezone: str = "UTC",
    ):
        self.term_groups = term_groups
        self.intent = intent
        self.file_type = file_type
        self.recency_days = recency_days
        self.person = person
        self.date_field = date_field or "modified"
        self.date_from = date_from
        self.date_to = date_to
        self.ownership = ownership or "any"
        self.folder = folder
        self.starred = starred
        self.order = order
        self.keywords = keywords or []
        self.timezone = timezone or "UTC"

    @property
    def is_entity(self) -> bool:
        """Entity queries have one right answer; ambiguity breaks them."""
        return self.intent == "entity"

    @property
    def person_email(self) -> str:
        """The person, when given as an email: Drive's owners and writers
        clauses match emails only, so a name is matched locally instead
        (see discovery._involves)."""
        return self.person if "@" in self.person else ""

    @property
    def is_empty(self) -> bool:
        """Nothing to search for and nothing to list by: "check my drive"."""
        return not (self.term_groups or self.has_filters or self.order)

    @property
    def has_filters(self) -> bool:
        """Is anything narrowing this beyond the search terms?"""
        return bool(
            self.date_from
            or self.date_to
            or self.recency_days
            or self.ownership != "any"
            or self.file_type != "any"
            or self.person
            or self.folder
            or self.starred
        )

    def without_person(self) -> "QueryPlan":
        """This plan, minus the person. Mutates; call on a copy."""
        self.person = ""
        return self

    def __repr__(self) -> str:
        bits = [f"intent={self.intent!r}", f"groups={self.term_groups!r}"]

        if self.keywords:
            bits.append(f"keywords={self.keywords!r}")
        if self.file_type != "any":
            bits.append(f"type={self.file_type!r}")
        if self.ownership != "any":
            bits.append(f"owner={self.ownership!r}")
        if self.person:
            bits.append(f"person={self.person!r}")
        if self.date_from or self.date_to:
            bits.append(f"dates={self.date_from or '..'}->{self.date_to or '..'}")
        if self.folder:
            bits.append(f"folder={self.folder!r}")
        if self.order:
            bits.append(f"order={self.order!r}")
        if self.starred:
            bits.append("starred")

        return f"QueryPlan({', '.join(bits)})"


# English only. A question in another language keeps its particles as search
# terms, which narrows the verbatim pass rather than breaking anything.
STOPWORDS = {
    "the", "a", "an", "my", "our", "your", "his", "her", "their", "its",
    "what", "which", "who", "whom", "whose", "when", "where", "why",
    "how", "is", "are", "was", "were", "do", "does", "did", "can",
    "could", "would", "should", "find", "show", "give", "get", "tell",
    "me", "i", "we", "you", "about", "for", "from", "with", "that",
    "this", "these", "those", "and", "or", "of", "in", "on", "to",
    "any", "all", "some", "please", "file", "files", "document",
    "documents", "drive",
    # Two-letter function words, now that two-letter content words
    # ("cv", "id", "w2") are kept.
    "am", "as", "at", "be", "by", "if", "it", "no", "so", "up", "us",
}


def _searchable(term: str) -> bool:
    """Does the term contain a word at all? Drive accepts a fullText search
    for pure punctuation and matches files arbitrarily."""
    return bool(tokens(term))


def _vocabulary_block(profile: Optional[CorpusProfile]) -> str:
    """The user's own words, as evidence for the expansion."""
    if profile is None or not profile.is_useful:
        return ""

    lines = [f"\nTHIS DRIVE ({profile.file_count} files)"]

    if profile.folders:
        lines.append("folders: " + ", ".join(profile.folders))

    if profile.terms:
        lines.append("words used in filenames: " + ", ".join(profile.terms))

    return "\n".join(lines) + "\n"


def _understanding_request() -> tuple[str, Type[QueryUnderstanding]]:
    """The instructions and reply model, with keywords only when they are used."""
    instructions = GDRIVE_UNDERSTAND_CONSTANTS["instructions"]

    if not gdrive_config.literal_keywords:
        return instructions.replace("{keywords}", ""), QueryUnderstanding

    return (
        instructions.replace("{keywords}", GDRIVE_UNDERSTAND_CONSTANTS["keywords_instructions"]),
        QueryUnderstandingWithKeywords,
    )


async def understand_query(ctx: DriveContext, query: str, profile: Optional[CorpusProfile] = None) -> QueryPlan:
    """Turn a question into Drive search terms plus an intent label.

    ``profile`` carries the user's own folder and filename vocabulary, which
    keeps the expansion grounded in this Drive. Optional, so a cold cache
    degrades to plain expansion rather than failing.
    """
    # Relative dates are unresolvable without the date, and "yesterday" is
    # the user's yesterday, not UTC's.
    today = datetime.now(utils.get_zoneinfo(ctx.timezone)).date().isoformat()

    instructions, reply_model = _understanding_request()

    def read(parsed: QueryUnderstanding) -> QueryPlan:
        groups = [
            [term.strip() for term in group if _searchable(term)]
            for group in parsed.term_groups
        ]
        groups = [g for g in groups if g][: gdrive_config.discovery_max_queries]

        plan = QueryPlan(
            term_groups=groups,
            intent=parsed.intent,
            file_type=parsed.file_type,
            person=parsed.person.strip(),
            date_field=parsed.date_field,
            date_from=_iso_date(parsed.date_from),
            date_to=_iso_date(parsed.date_to),
            ownership=parsed.ownership,
            folder=parsed.folder.strip(),
            starred=parsed.starred,
            order="" if parsed.order == "none" else parsed.order,
            keywords=[word.strip() for word in getattr(parsed, "keywords", []) if word.strip()],
            timezone=ctx.timezone,
        )

        # No terms is a browse when filters or an order say what to list
        # ("everything shared with me last month", "my biggest files"). With
        # neither ("check my drive") the plan is empty and the handler asks
        # what to look for: asking the model again gets the same reply.
        return plan

    return await structured(
        ctx,
        "understand",
        f"{_vocabulary_block(profile)}\nTODAY: {today}\nQUESTION: {query}\n",
        reply_model,
        read,
        description=GDRIVE_UNDERSTAND_CONSTANTS["tool_description"],
        instructions=instructions,
    )


def _iso_date(value: str) -> str:
    """Accept a date only if it really is one: a malformed date becomes a
    Drive clause that matches nothing."""
    text = (value or "").strip()[:10]

    if not text:
        return ""

    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""

    return text


# ---------------------------------------------------------------------
# 2. Semantic filename matching
# ---------------------------------------------------------------------


async def match_filename(ctx: DriveContext, typed: str, candidates: list[FileRef]) -> list[FileRef]:
    """Which of these files could the typed name have meant? Best first."""
    if not candidates:
        return []

    blocks = [
        f"[{index}] {ref.name}" + (f"  (in {ref.folder})" if ref.folder else "")
        for index, ref in enumerate(candidates)
    ]

    def read(parsed: FilenameMatches) -> list[FileRef]:
        # An empty list is an answer: nothing plausibly matches.
        chosen: list[FileRef] = []
        seen: set[int] = set()

        for row in parsed.items:
            if 0 <= row.index < len(candidates) and row.index not in seen:
                seen.add(row.index)
                chosen.append(candidates[row.index])

        return chosen

    return await structured(
        ctx,
        "match_filename",
        f"THEY TYPED: {typed}\n\nFILES:\n" + "\n".join(blocks),
        FilenameMatches,
        read,
        description=GDRIVE_MATCH_FILENAME_CONSTANTS["tool_description"],
    )
