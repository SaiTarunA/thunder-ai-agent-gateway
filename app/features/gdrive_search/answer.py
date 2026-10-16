"""Answering the question from the files search returned.

Search decides which files are worth reading; this reads them and answers,
citing each fact as [n], n being the file's position in the result's
documents. Only files the asking user's own Drive access returned are read.

The reranker has just read the same files, so their text is normally in the
content cache: answering costs one model call, not another round of
downloads. A long file is cut to its passage richest in the question's
words, with its line breaks kept so a table still reads as rows. A scan or a
photo has no text to extract and is sent to the model as the file itself,
downloaded while the reranker's model call ran.
"""

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Union

from app.ai.prompts.gdrive_search import GDRIVE_ANSWER_CONSTANTS
from app.core.utils import utils
from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import DriveContext
from app.features.gdrive_search.content import (
    PDF,
    FetchedFile,
    NativeFile,
    fetch_files,
    is_table,
    native_file,
    reads_natively,
)
from app.features.gdrive_search.judge import QueryPlan, structured
from app.features.gdrive_search.names import FileRef
from app.features.gdrive_search.rerank import best_passage, describe, question_terms
from app.features.gdrive_search.schemas import DriveAnswer

logger = logging.getLogger(__name__)

# [1], [1][3] or [1, 3]: every number in a bracket of numbers.
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass
class Answer:
    text: str = ""
    # The files the answer cites, as positions in the files it was given
    # (from 1), in order of first citation.
    cited: list[int] = field(default_factory=list)
    # Whether the files held the answer, by the model's own account: a
    # reply saying they do not still cites the files it looked at.
    found: bool = False


async def answer_question(
    ctx: DriveContext,
    query: str,
    refs: list[FileRef],
    plan: Optional[QueryPlan] = None,
    *,
    note: str = "",
    read_all: bool = False,
    natives: bool = True,
    fetch_timeout: Optional[float] = None,
) -> Answer:
    """Answer ``query`` from ``refs``, best first. ``plan`` adds the search
    terms to the question's own words when choosing a long file's passage.
    ``note`` says what the files are (a listing, and of how many). Files past
    answer_max_files are given by their details only, so a listing can name
    them all, unless ``read_all``. Without ``natives``, scans and photos are
    given by their details too: a listing names them, it need not read them.
    ``fetch_timeout`` is for files nothing has read yet."""
    if not query.strip() or not refs:
        return Answer()

    read = refs if read_all else refs[: gdrive_config.answer_max_files]
    fetched = await fetch_files(ctx, read, timeout=fetch_timeout or gdrive_config.answer_fetch_timeout_seconds)
    files = await _native_files(ctx, fetched) if natives else {}

    def parse(reply: DriveAnswer) -> Answer:
        text = reply.answer.strip()

        if not text:
            raise ValueError("empty answer")

        return Answer(text=text, cited=_cited(text, len(refs)), found=reply.found)

    async def ask(shown: dict[str, NativeFile]) -> Answer:
        return await structured(
            ctx,
            "answer",
            _model_input(_parts(ctx, query, refs, fetched, shown, plan, note)),
            DriveAnswer,
            parse,
            description=GDRIVE_ANSWER_CONSTANTS["tool_description"],
        )

    try:
        return await ask(files)
    except Exception as e:
        # One file the model refuses (a corrupt scan, a format it does not
        # take) must not cost the answer every other file holds.
        if not files or getattr(e, "status_code", None) not in (400, 413, 422):
            raise

        logger.warning(f"Drive answer refused the files sent as themselves, answering without them :: {e}")
        return await ask({})


async def _native_files(ctx: DriveContext, fetched: list[FetchedFile]) -> dict[str, NativeFile]:
    """The scans and photos to send as the files themselves, by file id:
    the first answer_native_max_files, each in time, all within
    answer_native_total_bytes."""
    unreadable = [got.ref for got in fetched if not got.text and reads_natively(got.ref)]
    unreadable = unreadable[: gdrive_config.answer_native_max_files]

    loaded = await asyncio.gather(
        *(native_file(ctx, ref, timeout=gdrive_config.answer_native_timeout_seconds) for ref in unreadable)
    )

    files: dict[str, NativeFile] = {}
    room = gdrive_config.answer_native_total_bytes

    for ref, native in zip(unreadable, loaded):
        if native is not None and len(native.data) <= room:
            files[ref.id] = native
            room -= len(native.data)

    return files


def _parts(
    ctx: DriveContext,
    query: str,
    refs: list[FileRef],
    fetched: list[FetchedFile],
    files: dict[str, NativeFile],
    plan: Optional[QueryPlan],
    note: str,
) -> list[Union[str, dict]]:
    """The answer's input: the question, then each file's text, the file
    itself, or why neither."""
    # "This month" and "the latest" are read against the user's today.
    today = datetime.now(utils.get_zoneinfo(ctx.timezone)).date().isoformat()

    terms = question_terms(query, plan)
    sizes = _allot([len(got.text) for got in fetched], gdrive_config.answer_file_chars, gdrive_config.answer_total_chars)
    parts: list[Union[str, dict]] = [f"TODAY: {today}\nQUESTION: {query}\n" + (f"{note}\n" if note else "") + "\nFILES:"]

    for n, (got, size) in enumerate(zip(fetched, sizes), start=1):
        text = best_passage(got.text, terms, size, flatten=False, table=is_table(got.ref)) if got.text else ""

        label = f"\n\n[{n}] {describe(got.ref)}\n"
        native = files.get(got.ref.id)

        if text:
            parts.append(f"{label}TEXT:\n{text}")
        elif native:
            parts += [f"{label}THE FILE ITSELF:", _file_part(got.ref, native)]
        else:
            parts.append(f"{label}(no readable text: {got.error or 'empty'})")

    for n, ref in enumerate(refs[len(fetched):], start=len(fetched) + 1):
        parts.append(f"\n\n[{n}] {describe(ref)}\n(listed, not shown in full)")

    return parts


def _allot(lengths: list[int], cap: int, total: int) -> list[int]:
    """Characters for each text, within ``cap`` each and ``total`` in all.
    Half the total is shared equally, so a fifth long file is still read;
    the rest goes best file first, so the files most likely to hold the
    answer are read most."""
    wanting = [i for i, length in enumerate(lengths) if length]

    if not wanting:
        return [0] * len(lengths)

    floor = total // (2 * len(wanting))
    sizes = [min(length, cap, floor) for length in lengths]
    left = total - sum(sizes)

    for i in wanting:
        more = min(min(lengths[i], cap) - sizes[i], left)
        sizes[i] += more
        left -= more

    return sizes


def renumber(text: str, numbers: dict[int, int]) -> str:
    """``text`` with its citations renumbered: [3] becomes [numbers[3]].
    A number not in ``numbers`` is left as it is."""

    def replace(match: re.Match) -> str:
        cited = [int(n) for n in match.group(1).split(",")]
        return "[" + ", ".join(str(numbers.get(n, n)) for n in cited) + "]"

    return _CITATION.sub(replace, text)


def uncited(text: str) -> str:
    """``text`` without its citations, for a reply shown without its files."""
    return re.sub(r"[ \t]{2,}", " ", _CITATION.sub("", text)).replace(" .", ".").strip()


def _file_part(ref: FileRef, native: NativeFile) -> dict:
    encoded = base64.b64encode(native.data).decode()

    if native.mime_type == PDF:
        return {"type": "input_file", "filename": ref.name, "file_data": f"data:{PDF};base64,{encoded}"}

    return {"type": "input_image", "image_url": f"data:{native.mime_type};base64,{encoded}"}


def _model_input(parts: list[Union[str, dict]]) -> Union[str, list[dict]]:
    """Plain text when every file is text; otherwise one user message of
    text and file parts, in order."""
    if all(isinstance(part, str) for part in parts):
        return "".join(part for part in parts if isinstance(part, str))

    content = [{"type": "input_text", "text": part} if isinstance(part, str) else part for part in parts]

    return [{"role": "user", "content": content}]


def _cited(text: str, count: int) -> list[int]:
    """The file numbers the answer cites. A bracketed number out of range
    ("[2025]") is not a citation."""
    cited: list[int] = []

    for group in _CITATION.findall(text):
        for number in group.split(","):
            n = int(number)

            if 1 <= n <= count and n not in cited:
                cited.append(n)

    return cited
