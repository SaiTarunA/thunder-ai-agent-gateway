import asyncio
import logging
import re
from dataclasses import replace
from typing import Optional

from fastapi import status

from app.ai import ai_constants
from app.ai.prompts.gdrive_search import (
    DRIVE_AUTH_REQUIRED_MESSAGE,
    DRIVE_CONNECT_REQUIRED_MESSAGE,
    DRIVE_NO_FILES_MESSAGE,
    DRIVE_NOT_FOUND_MESSAGE,
    DRIVE_SOURCE_QUESTION_FALLBACK,
    DRIVE_UNAVAILABLE_MESSAGE,
    DRIVE_WHAT_TO_FIND_MESSAGE,
    DRIVE_WHICH_FILE_QUESTION,
    GDRIVE_LISTING_NOTE_TEMPLATE,
)
from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import (
    DriveAccess,
    DriveAuthRequired,
    DriveContext,
    DriveNotConnected,
    DriveUnavailable,
    DriveUser,
    drive_client,
)
from app.features.gdrive_search.answer import answer_question, renumber, uncited
from app.features.gdrive_search.content import prefetch
from app.features.gdrive_search.discovery import (
    LOST,
    UNCERTAIN,
    Candidate,
    Discovery,
    discover,
    find_by_name,
    literal_terms,
    looks_like_filename,
    verdict,
)
from app.features.gdrive_search.judge import QueryPlan
from app.features.gdrive_search.names import FileRef, NameResolution, listing_index, resolve_name, tokens
from app.features.gdrive_search.rerank import rerank
from app.features.gdrive_search.schemas import (
    Citation,
    DriveSearchArgs,
    DriveSearchParams,
    DriveSearchResult,
    FileOption,
)
from app.integrations.google_drive.client import DriveRequestError

logger = logging.getLogger(__name__)

_FILE_FIELDS = "id,name,mimeType,size,modifiedTime,md5Checksum,webViewLink"

# The user saying where to look: "Google Drive", "gdrive", "in my drive",
# "on the drive". Not "drive" alone, which is also a word ("test drive").
_NAMES_DRIVE = re.compile(
    r"\bg(?:oogle)?\s?drive\b|\b(?:in|on|from|inside|into)\s+(?:my\s+|our\s+|the\s+)?drive\b",
    re.IGNORECASE,
)

# Searches started while routing decides, by user and message (see
# DriveSearchHandler.head_start).
_head_starts: dict[tuple[str, str], asyncio.Task] = {}


class DriveSearchHandler:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def head_start(self, user: Optional[DriveUser], request_data: dict) -> None:
        """Start the Drive search for a message that names Drive while
        routing, about two seconds, decides what it is: such a message is
        routed to Drive search all but always. search_from_luna takes the
        search over; end_head_start cancels one nothing took over, so a
        misrouted message costs a query understanding call at most."""
        question = (request_data.get("user_query") or "").strip()

        if user is None or not _NAMES_DRIVE.search(question) or (user.principal, question) in _head_starts:
            return

        _head_starts[(user.principal, question)] = asyncio.create_task(
            self.search(user, DriveSearchParams(query=question), dict(request_data))
        )

    def end_head_start(self, user: Optional[DriveUser], request_data: dict) -> None:
        """Cancel the head start for this message if nothing took it over."""
        if user is not None:
            task = _head_starts.pop((user.principal, (request_data.get("user_query") or "").strip()), None)

            if task is not None:
                task.cancel()

    async def search_from_luna(self, args: DriveSearchArgs, request_data: dict, user: Optional[DriveUser]) -> dict:
        """The drive_search intent. When the router could not tell whether the
        user meant Drive or chats, ask instead of searching."""
        question = drive_question(request_data.get("user_query") or "", args.query)

        # Searching since routing began.
        started = _head_starts.pop((user.principal, question), None) if user else None

        if started is not None:
            return await started

        # Asked "Drive or chats?" after saying Drive, the user has not been
        # listened to; the router does it about one time in five when the
        # request also mentions messages.
        if args.confirm_source and not _NAMES_DRIVE.search(question):
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "type": "drive_source_choice",
                "message": args.source_question.strip() or DRIVE_SOURCE_QUESTION_FALLBACK,
                "query": question,
                # What the frontend sends back for each answer: Drive searches
                # directly; chats resends the message with Drive left out of routing.
                "options": [
                    {
                        "source": "drive",
                        "label": "Google Drive",
                        "reqtype": ai_constants.FUNCTION_DRIVE_SEARCH,
                        "params": {"query": question},
                    },
                    {
                        "source": "chats",
                        "label": "Chats",
                        "user_query": request_data.get("user_query"),
                        "exclude_drive": True,
                    },
                ],
            }

        return await self.search(user, DriveSearchParams(query=question), request_data)

    async def search(self, user: Optional[DriveUser], params: DriveSearchParams, request_data: dict) -> dict:
        """Search the user's Drive. ``request_data`` supplies the billing
        identity (siteid, agentid, sitename) for the model calls."""
        try:
            if user is None:
                raise DriveNotConnected("No authenticated user for this request")

            ctx = DriveContext(
                drive=DriveAccess(user),
                billing={key: request_data.get(key) for key in ("siteid", "agentid", "sitename")},
                timezone=request_data.get("timezone") or "UTC",
            )
            result = await self._search(ctx, params)

            logger.info(
                f"Drive search :: verdict={result.verdict}, documents={len(result.documents)}, "
                f"alternatives={len(result.alternatives)}, answered={bool(result.answer)}, "
                f"agentid :: {request_data.get('agentid')}"
            )
            return {"status": status.HTTP_200_OK, "msg": "Success", "type": "drive_search", **result.model_dump()}

        except DriveUnavailable as e:
            logger.warning(f"Drive did not answer :: {e}, agentid :: {request_data.get('agentid')}")
            result = DriveSearchResult(
                query=params.query, verdict="none", answer=DRIVE_UNAVAILABLE_MESSAGE, error="Google Drive did not respond"
            )
            return {"status": status.HTTP_200_OK, "msg": "Success", "type": "drive_search", **result.model_dump()}
        except DriveNotConnected:
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "type": "drive_connect_required",
                "message": DRIVE_CONNECT_REQUIRED_MESSAGE,
            }
        except DriveAuthRequired:
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "type": "drive_auth_required",
                "message": DRIVE_AUTH_REQUIRED_MESSAGE,
            }
        except Exception as e:
            logger.exception(f"Error : {e}, agentid :: {request_data.get('agentid')}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def _search(self, ctx: DriveContext, params: DriveSearchParams) -> DriveSearchResult:
        """Find the files that answer a question, with no pre-built index.

        Three ways in:
          chosen_file_id   a file picked from a previous picker: no discovery
          typed_filename   a name typed instead of picking: the filename ladder
          query only       a fresh question: discovery, then content reranking,
                           or the filename ladder when the query is plainly a
                           filename that exists
        Several strong matches come back as documents, not a picker; a picker
        (verdict "lost") comes back only when no file clearly matched. Every
        result carries a reply in ``answer``: the question answered from the
        files found (a search's documents, a browse's list, a picked or named
        file), or, when there is nothing to answer from, a question back:
        which of these files, or what the file was called or held.
        """
        query = params.query.strip()

        # A file picked, or named, for the question: answered from that file.
        if params.chosen_file_id:
            try:
                meta = await ctx.drive.call(
                    lambda token: drive_client.get_file(token, params.chosen_file_id, fields=_FILE_FIELDS)
                )
            except DriveRequestError:
                return DriveSearchResult(query=query, verdict="none", error="That file is no longer accessible.")

            chosen = _file_ref_from_metadata(meta)

            return await self._answered(
                ctx,
                DriveSearchResult(query=query, verdict="confident", documents=[_ref_to_option(chosen)]),
                [chosen],
                fetch_timeout=gdrive_config.rerank_fetch_timeout_seconds,
            )

        typed = (params.typed_filename or "").strip()

        if typed:
            resolution = await resolve_name(ctx, typed)

            # The listing can lack it: a file added since it was built, or
            # one older than a large Drive's newest files.
            if not resolution.matches:
                resolution = _resolution(typed, await find_by_name(ctx, typed))

            if not resolution.matches:
                return DriveSearchResult(query=query, verdict="none", answer=DRIVE_NOT_FOUND_MESSAGE)

            return await self._named_answer(ctx, query, resolution)

        # A query that is plainly a filename ("DSC_0042.jpg", "final_v3")
        # is answered by the filename ladder: the reranker scores whether a
        # passage answers "final_v3", and nothing ever does. Only an exact name,
        # or a match narrow enough to choose from, is taken.
        if query and looks_like_filename(query, (await listing_index(ctx)).names):
            resolution = await resolve_name(ctx, query, allow_semantic=False)

            if resolution.matches and (
                resolution.tier == "exact"
                or len(resolution.matches) <= gdrive_config.disambiguation_max_options
            ):
                return await self._named_answer(ctx, query, resolution)

        if not query:
            return DriveSearchResult(query=query, verdict="none", error="Empty question.")

        found = await discover(ctx, query)

        if found.plan.is_empty:
            return DriveSearchResult(query=query, verdict="none", answer=DRIVE_WHAT_TO_FIND_MESSAGE)

        if found.plan.term_groups:
            # One list, ordered by what the files say. A browse has no subject
            # to judge content against and keeps discovery's order below.
            reranked = await rerank(ctx, query, found.plan, _rerank_pool(found))
            documents = reranked.kept

            # The verdict reads the gap between the top two scores, so it is
            # taken on score order; a superlative may have reordered them.
            by_score = sorted(documents, key=lambda c: -c.score)

            if by_score and by_score[0].score >= gdrive_config.confidence_min_score:
                # Answered from its documents, a close call between strong
                # files is a best guess, not a choice to make.
                label = verdict(found.plan, by_score)

                return await self._search_answer(
                    ctx, query, found.plan, documents, reranked.unread_matches, UNCERTAIN if label == LOST else label
                )

            # Nothing the reranker could vouch for, but it judged the scans
            # and photos Drive matched inside on their names alone. The
            # answer reads them as the files themselves, beside the files
            # nearly kept: if it finds what was asked, that is the result.
            if reranked.unread_matches:
                answered = await self._search_answer(
                    ctx, query, found.plan, [], documents + reranked.unread_matches, "uncertain"
                )

                if answered is not None:
                    return answered

            # Not found. The closest files become a choice (pick one, or type
            # the name) rather than results or a flat "nothing matched".
            options = (documents + reranked.near_misses)[: gdrive_config.disambiguation_max_options]

            if not options:
                return DriveSearchResult(query=query, verdict="none", answer=DRIVE_NOT_FOUND_MESSAGE)

            return DriveSearchResult(
                query=query,
                verdict="lost",
                alternatives=[_candidate_to_option(c) for c in options],
                answer=DRIVE_WHICH_FILE_QUESTION,
            )

        listed = found.selected
        exact = _exact_options(found, listed)

        # A verbatim hit is a result even when nothing cleared the relevance bar.
        if not listed and not exact:
            return DriveSearchResult(query=query, verdict="none", answer=DRIVE_NO_FILES_MESSAGE)

        result = DriveSearchResult(
            query=query,
            verdict=found.verdict,
            documents=[_candidate_to_option(c) for c in listed],
            exact_matches=exact,
        )

        # A browse ("my starred files") is answered from its list, told how
        # many files matched in all for "how many", not just how many it lists.
        matched = max(found.matched, len(listed))
        note = GDRIVE_LISTING_NOTE_TEMPLATE.format(
            matched=f"at least {matched}" if matched >= gdrive_config.discovery_max_candidates else matched,
            listed=len(listed),
        )

        # A listing names its scans and photos; it need not upload them.
        return await self._answered(ctx, result, [c.ref for c in listed], found.plan, note=note, natives=False)

    async def _search_answer(
        self,
        ctx: DriveContext,
        query: str,
        plan: QueryPlan,
        kept: list[Candidate],
        extras: list[Candidate],
        verdict_label: str,
    ) -> Optional[DriveSearchResult]:
        """The question answered from the files the reranker kept and from
        ``extras``, which the answer reads too and may prefer. The kept files
        are the result's documents whatever the answer cites; an extra joins
        them only when the answer cites it. With nothing kept, only an answer
        that found what was asked is a result: None otherwise."""
        files = kept + extras

        try:
            answered = await answer_question(ctx, query, [c.ref for c in files], plan, read_all=True)
        except Exception as e:
            logger.warning(
                f"Drive answer failed, returning the documents alone :: {e}, agentid :: {ctx.billing.get('agentid')}",
                exc_info=True,
            )
            kept_only = DriveSearchResult(
                query=query, verdict=verdict_label, documents=[_candidate_to_option(c) for c in kept]
            )
            return kept_only if kept else None

        # The kept files in order, then the extras the answer cites, each
        # citation renumbered to its file's place among them.
        cited_extras = [n for n in range(len(kept) + 1, len(files) + 1) if n in answered.cited]

        # With nothing kept, only an answer that found what was asked, in a
        # file it names, is a result.
        if not kept and not (answered.found and cited_extras):
            return None

        # The answer read the files and says they do not hold it: they are
        # what came closest, to pick from, not results.
        if kept and not answered.found and gdrive_config.answer_found_demotes:
            return DriveSearchResult(
                query=query,
                verdict="lost",
                alternatives=[_candidate_to_option(c) for c in files[: gdrive_config.disambiguation_max_options]],
                answer=uncited(answered.text),
            )

        places = list(range(1, len(kept) + 1)) + cited_extras
        numbers = {old: new for new, old in enumerate(places, start=1)}
        documents = [_candidate_to_option(files[old - 1]) for old in places]

        return DriveSearchResult(
            query=query,
            verdict=verdict_label,
            documents=documents,
            answer=renumber(answered.text, numbers),
            citations=[_citation(numbers[n], documents[numbers[n] - 1]) for n in answered.cited],
        )

    async def _named_answer(self, ctx: DriveContext, query: str, resolution: NameResolution) -> DriveSearchResult:
        """The files a typed name resolved to: one is answered from, several
        are a choice to make."""
        # Newest first.
        matches = sorted(resolution.matches, key=lambda m: m.modified_time or "", reverse=True)

        if not resolution.confident:
            return DriveSearchResult(
                query=query,
                verdict="lost",
                alternatives=[_ref_to_option(m) for m in matches[: gdrive_config.disambiguation_max_options]],
                resolution_tier=resolution.tier,
                answer=DRIVE_WHICH_FILE_QUESTION,
            )

        listed = matches[0]

        # The listing can be an hour old, and its checksum decides which
        # text is read: the file's current version is asked for, while its
        # text is fetched as listed, which is what it usually still is.
        current = asyncio.create_task(
            ctx.drive.call(lambda token: drive_client.get_file(token, listed.id, fields=_FILE_FIELDS))
        )
        prefetch(ctx, [listed])

        try:
            ref = replace(_file_ref_from_metadata(await current), folder=listed.folder)
        except DriveRequestError:
            return DriveSearchResult(query=query, verdict="none", error="That file is no longer accessible.")

        named = DriveSearchResult(
            query=query, verdict="confident", documents=[_ref_to_option(ref)], resolution_tier=resolution.tier
        )

        return await self._answered(ctx, named, [ref], fetch_timeout=gdrive_config.rerank_fetch_timeout_seconds)

    async def _answered(
        self,
        ctx: DriveContext,
        result: DriveSearchResult,
        refs: list[FileRef],
        plan: Optional[QueryPlan] = None,
        *,
        note: str = "",
        natives: bool = True,
        fetch_timeout: Optional[float] = None,
    ) -> DriveSearchResult:
        """``result`` with its question answered from ``refs``, its documents
        in order. A failed answer call leaves the documents to stand alone:
        they were found either way."""
        try:
            answered = await answer_question(
                ctx, result.query, refs, plan, note=note, natives=natives, fetch_timeout=fetch_timeout
            )
        except Exception as e:
            logger.warning(
                f"Drive answer failed, returning the documents alone :: {e}, agentid :: {ctx.billing.get('agentid')}",
                exc_info=True,
            )
            return result

        result.answer = answered.text
        result.citations = [_citation(n, result.documents[n - 1]) for n in answered.cited]

        return result


def _citation(n: int, document: FileOption) -> Citation:
    return Citation(
        n=n,
        file_id=document.file_id,
        file_name=document.file_name,
        folder=document.folder,
        web_view_link=document.web_view_link,
    )


def _candidate_to_option(candidate: Candidate) -> FileOption:
    ref = candidate.ref

    return FileOption(
        file_id=ref.id,
        file_name=ref.name,
        folder=ref.folder,
        mime_type=ref.mime_type,
        modified_time=ref.modified_time,
        web_view_link=ref.web_view_link,
        score=candidate.score,
        excerpt=candidate.excerpt,
        matched_in=("name" if candidate.exact_in_name else "contents") if candidate.exact else "",
    )


def _ref_to_option(ref: FileRef) -> FileOption:
    return FileOption(
        file_id=ref.id,
        file_name=ref.name,
        folder=ref.folder,
        mime_type=ref.mime_type,
        modified_time=ref.modified_time,
        web_view_link=ref.web_view_link,
    )


def _exact_options(found: Discovery, shown: list[Candidate]) -> list[FileOption]:
    """Verbatim hits the ranked results do not already show: listing a file
    under two headings reads as two findings."""
    already = {c.ref.id for c in shown}

    return [_candidate_to_option(c) for c in found.exact_matches if c.ref.id not in already]


def _rerank_pool(found: Discovery) -> list[Candidate]:
    """Everything worth reading: discovery's shortlist, then every verbatim
    hit it left out, without repeats."""
    pool, seen = [], set()

    for candidate in found.shortlist + found.exact_matches:
        if candidate.ref.id not in seen:
            seen.add(candidate.ref.id)
            pool.append(candidate)

    return pool


def _file_ref_from_metadata(meta: dict) -> FileRef:
    return FileRef(
        id=meta.get("id", ""),
        name=meta.get("name", ""),
        mime_type=meta.get("mimeType", ""),
        size=int(meta.get("size") or 0),
        modified_time=meta.get("modifiedTime", ""),
        md5=meta.get("md5Checksum", ""),
        web_view_link=meta.get("webViewLink", ""),
    )


def _resolution(typed: str, matches: list[FileRef]) -> NameResolution:
    """Files Drive's own name search found for a typed name."""
    return NameResolution(matches=matches, tier="contains", confident=len(matches) == 1, query=typed)


def drive_question(typed: str, routed: str) -> str:
    """The question to search for a Luna message: the user's own message,
    as typed, rather than the router's version of it, which reads as
    keywords ("roof repair cost" for "how much did the roof repair
    cost us"), losing what the question asks; the answer answers whatever
    was searched. Words like "in my Drive" stay: they tell query
    understanding the question is about the whole Drive.

    The router's query instead when the message is a short follow-up that
    names little of it ("yes, check my drive", "when does it expire?"):
    the router rebuilt the request from the conversation."""
    typed, routed = typed.strip(), (routed or "").strip()

    if not typed or not routed:
        return typed or routed

    wanted, have = _content_words(routed), _content_words(typed)

    if not wanted or len(have) > 3:
        return typed

    shared = sum(1 for word in wanted if any(_same_word(word, mine) for mine in have))

    return typed if shared * 2 >= len(wanted) else routed


def _content_words(text: str) -> list[str]:
    quoted, words = literal_terms(text)
    return words + [w for span in quoted for w in tokens(span) if w not in words]


def _same_word(a: str, b: str) -> bool:
    """Equal, or sharing their first four letters: "expire" and "expiry"."""
    return a == b or (min(len(a), len(b)) >= 4 and a[:4] == b[:4])


drive_search_handler = DriveSearchHandler()
