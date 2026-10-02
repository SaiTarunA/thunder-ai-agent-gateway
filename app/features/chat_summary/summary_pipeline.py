import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from app.ai.router import model_router
from app.ai.tokenizer import count_tokens_for, validate_token_limits
from app.core.message_cleaner import filter_conversations
from app.features.chat_summary.batching import chat_summary_batcher
from app.features.chat_summary.schemas import (
    DEFAULT_BATCHING_SETTINGS,
    FOCUS_KEYS,
    StoredChatSummaryData,
    SummaryNLPExtractedData,
    TokenLimits,
)
from app.ai.prompts.chat_summary import (
    BUDDY_NAME_INSTRUCTION_TEMPLATE,
    CONTEXT_INSTRUCTION_TEMPLATE,
    GROUP_NAME_INSTRUCTION_TEMPLATE,
    SUMMARY_TYPE_DETAILED_INSTRUCTION,
    SUMMARY_TYPE_GENERIC_TEMPLATE,
    TONE_INSTRUCTION_TEMPLATE,
    TOPIC_NAME_INSTRUCTION_TEMPLATE,
    USER_FOCUS_INSTRUCTION_TEMPLATE,
)
from app.core.utils import utils

logger = logging.getLogger(__name__)

class BaseSummaryPipeline:
    """Shared summarization pipeline for both chat summaries and thread summaries.

    Provides core map-reduce, hierarchical tree reduction, batching, caching deduplication,
    and gap calculation algorithms without duplicating code between handlers.
    """

    def normalize_chat_messages(
        self,
        chats: list,
        user_timezone: Optional[str] = "UTC",
    ) -> list[dict]:
        """Normalizes chat entries (StreamsUserChatData or dicts) into standard conversation dicts with localized timestamps."""
        result: list[dict] = []
        for chat in chats:
            if isinstance(chat, dict):
                entry = dict(chat)
                time_val = entry.get("timestamp") or entry.get("messagetime")
                if time_val:
                    entry["timestamp"] = utils.convert_utc_to_timezone(time_val, user_timezone)
                result.append(entry)
                continue

            firstname = getattr(chat, "firstname", "") or ""
            lastname = getattr(chat, "lastname", "") or ""
            username = getattr(chat, "username", "") or ""
            user_display = f"{firstname} {lastname}".strip() or utils.extract_user_name(username) or "User"

            messagetime = getattr(chat, "messagetime", None)
            msg_val = getattr(chat, "message", None)
            parsed_json = utils.safe_parse_json(msg_val)
            if isinstance(parsed_json, dict):
                text_val = None
                for k in ("text", "msg", "message"):
                    if isinstance(parsed_json.get(k), str) and parsed_json[k].strip():
                        text_val = parsed_json[k].strip()
                        break
                final_text = text_val or (str(msg_val) if msg_val else "")
            else:
                final_text = str(msg_val) if msg_val else ""

            user_time = utils.convert_utc_to_timezone(messagetime, user_timezone) if messagetime else ""
            result.append({
                "user": user_display,
                "message": final_text,
                "timestamp": user_time,
            })
        return result

    def normalize_and_filter_chat_messages(
        self,
        chats: list,
        user_timezone: Optional[str] = "UTC",
    ) -> list[dict]:
        """Normalizes chat messages and applies noise filtering."""
        normalized = self.normalize_chat_messages(chats, user_timezone)
        return filter_conversations(normalized)

    # ------------------------------------------------------------------
    # Caching & Interval Utilities
    # ------------------------------------------------------------------

    def deduplicate_and_filter_summaries(
        self, summaries: list[StoredChatSummaryData]
    ) -> list[StoredChatSummaryData]:
        """Filters out duplicate summaries and any summary fully contained within another of greater duration
        (e.g., [Sep 1, Sep 10] is dropped if [Sep 1, Sep 15] exists).
        """
        try:
            if not summaries or len(summaries) <= 1:
                return summaries

            # Pass 1: Deduplicate identical (start_date, end_date) intervals by retaining
            # only the latest entry (highest DB id).
            unique_date_map: dict[tuple[datetime, datetime], StoredChatSummaryData] = {}
            for s in summaries:
                key = (s.start_date, s.end_date)
                if key not in unique_date_map or s.id > unique_date_map[key].id:
                    unique_date_map[key] = s
            candidates = list(unique_date_map.values())

            # Pass 2: Subsumption filter.
            # If summary A [Sep 1, Sep 10] is completely enclosed within summary B [Sep 1, Sep 15],
            # discard A so we do not process or present redundant overlapping summaries.
            filtered: list[StoredChatSummaryData] = []
            for i, s1 in enumerate(candidates):
                s1_duration = (s1.end_date - s1.start_date).total_seconds()
                is_subsumed = False
                for j, s2 in enumerate(candidates):
                    if i == j:
                        continue
                    s2_duration = (s2.end_date - s2.start_date).total_seconds()
                    # Check if s1 is entirely covered by s2 and s2 has a longer duration
                    if s2.start_date <= s1.start_date and s1.end_date <= s2.end_date:
                        if s2_duration > s1_duration:
                            is_subsumed = True
                            logger.info(
                                f"Discarding summary [{s1.start_date} to {s1.end_date}] (duration: {s1_duration}s) "
                                f"as it is subsumed by [{s2.start_date} to {s2.end_date}] (duration: {s2_duration}s)"
                            )
                            break
                if not is_subsumed:
                    filtered.append(s1)

            # Sort chronologically by start and end dates
            filtered.sort(key=lambda x: (x.start_date, x.end_date))
            return filtered
        except Exception as e:
            logger.error(f"Error deduplicating and filtering summaries: {str(e)}")
            return summaries

    def find_coverage_gaps(
        self,
        requested_start: datetime,
        requested_end: datetime,
        existing_summaries: list[StoredChatSummaryData],
        request_data: dict,
    ) -> list[dict]:
        """Returns all date ranges within [requested_start, requested_end] NOT covered by any existing summary."""
        try:
            # Phase 1: Clip each summary's date range to stay strictly within the requested window
            intervals: list[tuple[datetime, datetime]] = []
            for s in existing_summaries:
                clipped_start = max(s.start_date, requested_start)
                clipped_end = min(s.end_date, requested_end)
                if clipped_start <= clipped_end:
                    intervals.append((clipped_start, clipped_end))

            # Phase 2: Merge overlapping or adjacent covered intervals
            intervals.sort(key=lambda x: x[0])
            merged: list[tuple[datetime, datetime]] = []
            for start, end in intervals:
                if merged and start <= merged[-1][1]:
                    # Extend current interval if the next one overlaps
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            # Phase 3: Sweep across the requested timeline to find uncovered 'holes' (gaps)
            gaps: list[dict] = []
            cursor = requested_start

            for cov_start, cov_end in merged:
                # If the covered interval starts after cursor, the period in between is a gap
                if cursor < cov_start:
                    gap_end = cov_start - timedelta(seconds=1)
                    gaps.append({"start": cursor, "end": gap_end})
                # Advance cursor past the covered window
                cursor = cov_end + timedelta(seconds=1)

            # Trailing gap: from the end of the last covered interval to requested_end
            if cursor <= requested_end:
                gaps.append({"start": cursor, "end": requested_end})

            logger.info(f"found gaps as following : \n{gaps}, agentid :: {request_data.get('agentid')}")
            return gaps
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    def find_exact_cached_summary(
        self,
        summaries: list[StoredChatSummaryData],
        requested_start: datetime,
        requested_end: datetime,
    ) -> Optional[str]:
        """Returns the summary text if a single cached summary exactly covers the requested range."""
        for s in summaries:
            if s.start_date == requested_start and s.end_date == requested_end:
                return s.summary
        return None

    def has_chat_segments(self, segments: list[dict]) -> bool:
        """Returns True if any segment contains raw chat messages."""
        return any(
            seg.get("type") == "chats" and len(seg.get("conversations", [])) > 0
            for seg in segments
        )

    def aggregate_segment_metadata(
        self,
        segments: list[dict],
        requested_start: datetime,
        requested_end: datetime,
    ) -> dict:
        """Aggregates conversation count, unique participants, and total duration across all segments."""
        all_participants: list[str] = []
        total_conv_count = 0
        for seg in segments:
            if seg.get("type") == "summary":
                extra = seg.get("extra_data") or {}
                total_conv_count += extra.get("no_of_conversations", 0)
                for p in extra.get("participants_info", []):
                    if p and p not in all_participants:
                        all_participants.append(p)
            elif seg.get("type") == "chats":
                convs = seg.get("conversations", [])
                total_conv_count += len(convs)
                for c in convs:
                    u = c.get("user")
                    if u and u not in all_participants:
                        all_participants.append(u)

        return {
            "no_of_conversations": total_conv_count,
            "participants_info": all_participants,
            "total_duration": utils.calculate_total_duration(requested_start, requested_end),
        }

    def personalize_summary(self, summary_text: str, requested_user_name: Optional[str]) -> str:
        """Personalizes 2nd person pronoun if requested user is named in summary."""
        if requested_user_name and summary_text:
            pattern = rf"\b{re.escape(requested_user_name)}\b"
            return re.sub(pattern, "you", summary_text, flags=re.IGNORECASE)
        return summary_text

    async def build_chronological_segments(
        self,
        existing_summaries: list[StoredChatSummaryData],
        gaps: list[dict],
        request_data: dict,
        fetch_gap_conversations: Optional[Callable[[datetime, datetime], Awaitable[list[dict]]]] = None,
    ) -> list[dict]:
        """Builds a list of segments (existing summaries + newly collected gap chats) sorted chronologically."""
        try:
            segments: list[dict] = []
            user_timezone = request_data.get("timezone")

            # 1. Convert cached summaries from DB into 'summary' segments with localized date strings
            for s in existing_summaries:
                s_start_str = utils.convert_utc_to_timezone(s.start_date, user_timezone)
                s_end_str = utils.convert_utc_to_timezone(s.end_date, user_timezone)
                extra = s.parsed_extra_data if hasattr(s, "parsed_extra_data") else (
                    json.loads(s.extra_data) if isinstance(s.extra_data, str) else (s.extra_data or {})
                )
                segments.append({
                    "type": "summary",
                    "text": s.summary,
                    "start": s.start_date,
                    "end": s.end_date,
                    "date_range": f"{s_start_str} to {s_end_str}",
                    "extra_data": extra,
                })

            # 2. Query and collect raw chats for each uncovered time gap
            if fetch_gap_conversations:
                for gap in gaps:
                    gap_start: datetime = gap["start"]
                    gap_end: datetime = gap["end"]

                    conversations = await fetch_gap_conversations(gap_start, gap_end)

                    gap_start_str = utils.convert_utc_to_timezone(gap_start, user_timezone)
                    gap_end_str = utils.convert_utc_to_timezone(gap_end, user_timezone)

                    if conversations:
                        segments.append({
                            "type": "chats",
                            "conversations": conversations,
                            "start": gap_start,
                            "end": gap_end,
                            "date_range": f"{gap_start_str} to {gap_end_str}",
                        })
                    else:
                        logger.info(
                            f"No chats found for gap {gap['start']} → {gap['end']}, agentid: {request_data.get('agentid')}"
                        )

            # Sort all segments together chronologically so the LLM gets an unbroken narrative flow
            segments.sort(key=lambda x: x["start"])
            logger.info(f"Segments assembled: count={len(segments)}, agentid: {request_data.get('agentid')}")
            return segments
        except Exception as e:
            logger.error(f"Error building chronological segments: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    # ------------------------------------------------------------------
    # Batching & Prompt Helpers
    # ------------------------------------------------------------------

    def _batching_settings(self, request_data: dict) -> dict:
        return {**DEFAULT_BATCHING_SETTINGS, **(request_data.get("batching") or {})}

    async def _count_tokens(self, text: str, request_data: dict) -> int:
        return await asyncio.to_thread(count_tokens_for, text, request_data["model_info"])

    def _focus_from_nlp(self, nlp_response: SummaryNLPExtractedData, request_data: dict) -> dict:
        """The requested focus (context/topic/buddy), without the style keys."""
        data: dict = {}
        self.append_user_instructions(data, nlp_response, request_data)
        return {k: v for k, v in data.items() if k in FOCUS_KEYS}

    def append_user_instructions(self, data: dict, nlp_response: SummaryNLPExtractedData, request_data: dict):
        """Appends user instructions based on nlp_response (supports SummaryNLPExtractedData)."""
        try:
            summary_type = getattr(nlp_response, "summary_type", None)
            tone = getattr(nlp_response, "tone", None)
            context = getattr(nlp_response, "context", None)
            topic_name = getattr(nlp_response, "topic_name", None)
            buddy_name = getattr(nlp_response, "buddy_name", None)
            group_name = getattr(nlp_response, "group_name", None)

            # Preserve the original user prompt if it had specific instructions/focus
            original_query = request_data.get("user_query")
            if original_query and isinstance(original_query, str):
                orig_stripped = original_query.strip()
                # Ensure it is natural text and not already a JSON dump
                if not (orig_stripped.startswith("{") or orig_stripped.startswith("[")):
                    request_data["original_user_query"] = orig_stripped
                    data["user_focus"] = USER_FOCUS_INSTRUCTION_TEMPLATE.format(query=orig_stripped)

            if summary_type:
                if summary_type in ("detailed", "long"):
                    data["summary_type"] = SUMMARY_TYPE_DETAILED_INSTRUCTION
                else:
                    data["summary_type"] = SUMMARY_TYPE_GENERIC_TEMPLATE.format(summary_type=summary_type)
            if tone:
                data["tone"] = TONE_INSTRUCTION_TEMPLATE.format(tone=tone)
            if context:
                data["context"] = CONTEXT_INSTRUCTION_TEMPLATE.format(context=context)
            if topic_name:
                data["topic_name"] = TOPIC_NAME_INSTRUCTION_TEMPLATE.format(topic_name=topic_name)
            if buddy_name:
                data["buddy_name"] = BUDDY_NAME_INSTRUCTION_TEMPLATE.format(buddy_name=buddy_name)
            if group_name:
                data["group_name"] = GROUP_NAME_INSTRUCTION_TEMPLATE.format(group_name=group_name)
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    # ------------------------------------------------------------------
    # Summarization & Map-Reduce Pipeline
    # ------------------------------------------------------------------

    async def merge_segments_into_summary(
        self, segments: list[dict], nlp_response: SummaryNLPExtractedData, request_data: dict
    ) -> dict:
        """Sends all segments to the model to produce one merged cohesive summary.

        When the segments together are too large for one call, the raw-chat segments are first
        condensed into working notes in time-aligned batches (see `_condense_conversations`).
        """
        try:
            limits = chat_summary_batcher.calculate_token_limits(
                model_info=request_data["model_info"],
                instructions=request_data["secondary_instructions"],
                max_output_tokens=int(request_data["max_output_tokens"]),
                count_tokens=lambda t: count_tokens_for(t, request_data["model_info"]),
                batching_settings=self._batching_settings(request_data),
            )

            # Check if all combined segments (summaries + raw chats) fit inside single_pass
            merge_query = json.dumps({"segments": chat_summary_batcher.strip_payload_segments(segments)}, indent=4)
            if await self._count_tokens(merge_query, request_data) > limits.single_pass:
                # If too large, condense only the raw chat segments into notes before merging
                logger.info(
                    f"Segments exceed the single-call limit ({limits.single_pass} tokens), condensing in batches, "
                    f"agentid :: {request_data.get('agentid')}"
                )
                segments = await self._condense_chat_segments(segments, nlp_response, request_data, limits)

            return await self._final_merge(segments, nlp_response, request_data)

        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def summarize_conversations(
        self, conversations: dict, nlp_response: SummaryNLPExtractedData, request_data: dict
    ) -> dict:
        """Summarizes collected conversations in one call, or in time-aligned batches when too large."""
        try:
            limits = chat_summary_batcher.calculate_token_limits(
                model_info=request_data["model_info"],
                instructions=request_data["instructions"],
                max_output_tokens=int(request_data["max_output_tokens"]),
                count_tokens=lambda t: count_tokens_for(t, request_data["model_info"]),
                batching_settings=self._batching_settings(request_data),
            )
            user_query = json.dumps(conversations, indent=4)
            tokens = await self._count_tokens(user_query, request_data)

            if tokens <= limits.single_pass:
                request_data["user_query"] = user_query
                return await self.generate_chat_summary(request_data)

            agent_id = request_data.get("agentid")
            logger.info(
                f"History is {tokens} tokens which exceeds the single-call limit of {limits.single_pass}; "
                f"summarizing in batches of up to {limits.batch_target} tokens, agentid :: {agent_id}"
            )
            focus = self._focus_from_nlp(nlp_response, request_data)
            segments = await self._condense_conversations(
                conversations.get("conversations", []), focus, request_data, limits
            )
            return await self._final_merge(segments, nlp_response, request_data)
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _condense_chat_segments(
        self, segments: list[dict], nlp_response: SummaryNLPExtractedData, request_data: dict, limits: TokenLimits
    ) -> list[dict]:
        """Replaces raw chat segments with working-notes segments in parallel while preserving stored summaries."""
        try:
            focus = self._focus_from_nlp(nlp_response, request_data)

            # Nested worker: handles individual segment dispatch.
            # - If segment is already a stored 'summary', it is kept as-is (no redundant re-summarization).
            # - If segment is raw 'chats', it is batched and condensed into working notes.
            async def process_segment(seg: dict) -> list[dict]:
                if seg.get("type") == "chats":
                    return await self._condense_conversations(seg.get("conversations", []), focus, request_data, limits)
                return [seg]

            # Process all segments concurrently
            processed = await asyncio.gather(*(process_segment(s) for s in segments))
            # Flatten the list of lists into a single chronological segment list
            return [seg for group in processed for seg in group]
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _condense_conversations(
        self, conversations: list[dict], focus: dict, request_data: dict, limits: TokenLimits
    ) -> list[dict]:
        """Splits raw conversations into batches and turns each into a working-notes segment concurrently."""
        try:
            if not conversations:
                return []

            settings = self._batching_settings(request_data)
            model_info = request_data["model_info"]
            agent_id = request_data.get("agentid")

            # Offload CPU-bound token calculations and boundary snapping to a worker thread
            batches = await asyncio.to_thread(
                chat_summary_batcher.split_into_batches, conversations, limits.batch_target, lambda t: count_tokens_for(t, model_info)
            )

            # Limit parallel LLM calls to prevent rate-limiting or starving the model router
            semaphore = asyncio.Semaphore(max(1, int(settings["max_parallel_batches"])))
            batch_attempts = max(1, int(settings["batch_attempts"]))
            max_output_tokens = settings["batch_max_output_tokens"]
            temperature = settings["batch_temperature"]
            instructions = request_data["batch_notes_instructions"]
            unavailable_text = request_data.get("unavailable_period_text") or "[This period could not be summarized.]"

            # Nested worker: generates working notes for a single time-aligned batch.
            # Handles prompt construction, concurrency throttling via semaphore, and retry attempts.
            async def summarize_batch(index: int, batch) -> dict:
                # Inject period date range and query focus (topic/buddy/context) into the prompt payload
                payload = {"period": batch.date_range, "conversations": batch.conversations, **focus}
                user_query = json.dumps(payload, indent=4)
                notes: Optional[str] = None

                # Throttle concurrent model requests
                async with semaphore:
                    for attempt in range(1, batch_attempts + 1):
                        try:
                            notes = await self._call_model(
                                request_data,
                                instructions=instructions,
                                user_query=user_query,
                                max_output_tokens=max_output_tokens,
                                temperature=temperature,
                            )
                            break  # Success, exit retry loop
                        except Exception as e:
                            logger.warning(
                                f"Batch {index + 1} ({batch.date_range}) attempt {attempt} failed :: {e}, agentid :: {agent_id}"
                            )
                            if attempt < batch_attempts:
                                await asyncio.sleep(1)

                # Return a summary segment. If all retry attempts failed, mark as unavailable
                # so downstream reduce can handle the gap gracefully without crashing the whole request.
                return {
                    "type": "summary",
                    "text": notes if notes is not None else unavailable_text,
                    "date_range": batch.date_range,
                    "condensed": True,
                    "unavailable": notes is None,
                }

            # Run batch summarization concurrently across all batches
            results = await asyncio.gather(*(summarize_batch(i, b) for i, b in enumerate(batches)))

            # If every batch failed, we cannot generate a meaningful summary; raise an exception
            if results and all(r["unavailable"] for r in results):
                raise Exception("Unable to summarize any period of the requested range")

            logger.info(
                f"Condensed {len(batches)} batches, {sum(r['unavailable'] for r in results)} unavailable, "
                f"agentid :: {agent_id}"
            )
            return list(results)
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _collapse_until_fits(
        self, segments: list[dict], nlp_response: SummaryNLPExtractedData, request_data: dict
    ) -> list[dict]:
        """Merges adjacent notes segments (hierarchical reduce) until the final merge fits in one call."""
        try:
            settings = self._batching_settings(request_data)
            limits = chat_summary_batcher.calculate_token_limits(
                model_info=request_data["model_info"],
                instructions=request_data["secondary_instructions"],
                max_output_tokens=int(request_data["max_output_tokens"]),
                count_tokens=lambda t: count_tokens_for(t, request_data["model_info"]),
                batching_settings=self._batching_settings(request_data),
            )
            focus = self._focus_from_nlp(nlp_response, request_data)
            agent_id = request_data.get("agentid")
            max_collapse_rounds = int(settings["max_collapse_rounds"])

            # Iterative reduction: keep collapsing adjacent notes in rounds until the combined
            # payload fits within the model's single-pass token limit.
            for round_number in range(1, max_collapse_rounds + 1):
                payload_segs = chat_summary_batcher.strip_payload_segments(segments)
                query = json.dumps({"segments": payload_segs}, indent=4)

                # Exit early as soon as all segments fit comfortably in a single merge call
                if await self._count_tokens(query, request_data) <= limits.single_pass:
                    return segments

                logger.info(
                    f"Collapse round {round_number}: {len(segments)} segments still exceed {limits.single_pass} tokens, "
                    f"agentid :: {agent_id}"
                )

                # Calculate tokens for each individual segment in parallel
                sizes = await asyncio.gather(*(
                    self._count_tokens(json.dumps(s, indent=4), request_data) for s in payload_segs
                ))

                # Bin-pack adjacent segments into groups that do not exceed the batch target limit
                groups = chat_summary_batcher.group_segments_by_token_limit(segments, sizes, limits.batch_target)

                # If no grouping could be made (all groups are size 1), further collapsing is impossible
                if all(len(g) == 1 for g in groups):
                    break

                # Nested worker: merges a group of adjacent notes into a single combined note segment.
                async def collapse(group: list[dict]) -> dict:
                    if len(group) == 1:
                        return group[0]  # Nothing to merge if only 1 segment in this bin

                    payload = {"segments": chat_summary_batcher.strip_payload_segments(group), **focus}
                    text = await self._call_model(
                        request_data,
                        instructions=request_data["collapse_notes_instructions"],
                        user_query=json.dumps(payload, indent=4),
                        max_output_tokens=settings["batch_max_output_tokens"],
                        temperature=settings["batch_temperature"],
                    )
                    # Merge the date ranges across the collapsed group (e.g., 'Jan 1 to Jan 5' + 'Jan 6 to Jan 10' -> 'Jan 1 to Jan 10')
                    first_range = str(group[0].get("date_range") or "").split(" to ")[0]
                    last_range = str(group[-1].get("date_range") or "").split(" to ")[-1]
                    combined_range = f"{first_range} to {last_range}".strip() if first_range and last_range else ""
                    return {
                        "type": "summary",
                        "text": text,
                        "date_range": combined_range,
                        "condensed": True,
                        "unavailable": all(s.get("unavailable") for s in group),
                    }

                # Collapse all groups concurrently for this round
                segments = list(await asyncio.gather(*(collapse(g) for g in groups)))

            return segments
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _final_merge(
        self, segments: list[dict], nlp_response: SummaryNLPExtractedData, request_data: dict
    ) -> dict:
        """Builds the user-facing summary from summary/notes/chat segments with the merge prompt."""
        try:
            # Step 1: Ensure segments fit inside the single-pass prompt limit via tree collapse
            segments = await self._collapse_until_fits(segments, nlp_response, request_data)

            # Step 2: Strip internal metadata keys (e.g. 'condensed', 'unavailable') before sending to LLM
            merge_payload: dict = {"segments": chat_summary_batcher.strip_payload_segments(segments)}
            self.append_user_instructions(merge_payload, nlp_response, request_data)

            # Step 3: If multiple batched periods exist and user did not ask for a short/brief summary,
            # instruct the model to organize the output with clear chronological date-period sections.
            was_batched = any(seg.get("condensed") for seg in segments)
            summary_type = getattr(nlp_response, "summary_type", None)
            if was_batched and len(segments) > 1 and summary_type not in (None, "brief", "short"):
                merge_payload["period_layout"] = request_data.get("period_layout_instruction")

            # Step 4: Handle any periods that failed intermediate batch summarization.
            # Explicitly instruct the LLM about missing periods to avoid hallucination or inventing events.
            unavailable_periods = [seg.get("date_range") for seg in segments if seg.get("unavailable")]
            if unavailable_periods:
                merge_payload["unavailable_periods"] = {
                    "periods": unavailable_periods,
                    "instruction": request_data.get("unavailable_periods_instruction"),
                }

            # Step 5: Switch prompt instructions to the reduce prompt (secondary_instructions)
            request_data["instructions"] = request_data["secondary_instructions"]
            request_data["user_query"] = json.dumps(merge_payload, indent=4)

            # Step 6: Invoke LLM to generate the final cohesive summary
            response = await self.generate_chat_summary(request_data)

            # Step 7: Attach partial_periods to response dictionary so client knows the summary is partial
            if unavailable_periods:
                response["partial_periods"] = unavailable_periods
            return response
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _call_model(
        self,
        request_data: dict,
        instructions: str,
        user_query: str,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """One internal model call on a private copy of `request_data`."""
        try:
            # Create an isolated clone of request_data.
            # Strips conversation_id, previous_response_id, tools, and tool choices
            # to prevent state pollution or recursive agent tool invocation inside internal batch calls.
            call_data = {
                **request_data,
                "instructions": instructions,
                "user_query": user_query,
                "previous_response_id": None,
                "conversation_id": None,
                "tools": None,
                "tool_choice": None,
                "parallel_tool_calls": None,
            }
            if max_output_tokens is not None:
                call_data["max_output_tokens"] = int(max_output_tokens)
            if temperature is not None:
                call_data["temperature"] = float(temperature)

            await validate_token_limits(f"{user_query}\n{instructions}", call_data)
            response = await model_router.generate(call_data)
            text = (response.text or "").strip()
            if not text:
                raise ValueError("Model returned an empty response")
            return text
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def generate_chat_summary(self, request_data: dict) -> dict:
        """To generate chat summary for the conversation/messages."""
        try:
            total_content = f"{request_data['user_query']}\n{request_data['instructions']}"

            await validate_token_limits(total_content, request_data)

            response = await model_router.generate(request_data)

            chat_summary = response.text or ""

            logger.info(f" Summary :: {chat_summary} length of summary :: {len(chat_summary)}, agentid :: {str(request_data.get('agentid'))}")

            return {"status": "200", "msg": "Success", "message": chat_summary}

        except Exception as e:
            logger.error(f"Error ========  :: {e}, agentid :: {request_data.get('agentid')}")
            raise e


base_summary_pipeline = BaseSummaryPipeline()
