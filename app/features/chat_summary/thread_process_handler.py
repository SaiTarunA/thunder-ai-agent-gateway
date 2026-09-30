import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import status
from pydantic import TypeAdapter

from app.ai import ai_constants
from app.ai.ai_constants import ThreadCategory
from app.ai.config_builder import ai_config_builder
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.core.message_cleaner import filter_conversations
from app.core.utils import utils
from app.db.mysql.repositories.opensips_repo import opensips_db_handler
from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.features.chat_summary.schemas import (
    StoredChatSummaryData,
    StreamsUserChatData,
)
from app.features.chat_summary.summary_pipeline import BaseSummaryPipeline
from app.features.intent_detection.schemas import ProcessThreadArgs
from app.workers.attachment_worker import (
    TEMP_ATTACHMENT_DIR,
    process_messages_attachments,
)

logger = logging.getLogger(__name__)


class ThreadProcessHandler(BaseSummaryPipeline):

    async def process_thread_request(self, args: ProcessThreadArgs, request_data: dict) -> dict:
        """Unified handler for thread summarization and thread replies.

        Handles:
        1. Thread Replies (GENERATE_REPLY):
           - Fetches parent message and thread replies
           - Generates sendable response from current_user's viewpoint
           - Detects and retries on meta-announcements

        2. Thread Summarization (SUMMARIZE):
           - Checks cache first before bulk message loading
           - For message_count: fetches strictly the last N replies without parent injection
           - For exact cached summary: returns immediately with zero message fetching
           - For partial cached summary: fetches only delta messages after the cached timestamp
           - For no cached summary: fetches thread messages once, summarizes, and caches
        """
        try:
            category = args.category
            thread_config = await ai_config_builder.prepare_process_thread_config(category)
            if not thread_config:
                return {
                    "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "msg": "Failed",
                    "error": "Failed to prepare thread processing configuration.",
                }
            thread_data = {**request_data, **thread_config}

            smsgid = request_data.get("smsgid")
            if not smsgid:
                return {
                    "status": status.HTTP_400_BAD_REQUEST,
                    "msg": "Failed",
                    "error": "Missing 'smsgid' parameter in request.",
                }

            user_timezone = request_data.get("timezone")

            # Fetch the thread root / parent message once from DB
            raw_parent = await streams_db_handler.get_streams_parent_messages(request_data)
            if not raw_parent:
                logger.warning(f"Parent message not found for smsgid: {smsgid}, agentid: {request_data.get('agentid')}")
                return {
                    "status": status.HTTP_404_NOT_FOUND,
                    "msg": "Failed",
                    "error": "Parent message not found for this thread.",
                }

            parent_chat: StreamsUserChatData = TypeAdapter(StreamsUserChatData).validate_python(raw_parent)
            parent_dt: datetime = utils.convert_to_utc(parent_chat.messagetime, user_timezone)

            # Dispatch by Category
            if category == ThreadCategory.GENERATE_REPLY:
                return await self._handle_generate_reply(parent_chat, request_data, thread_data)

            elif category == ThreadCategory.SUMMARIZE:
                return await self._handle_summarize_thread(
                    args, parent_chat, parent_dt, request_data, thread_data
                )

            else:
                raise ValueError(f"Unsupported thread category: {category}")

        except Exception as e:
            logger.exception(f"Error in process_thread_request: {e}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    # ------------------------------------------------------------------
    # Branch 1: Thread Reply Generation
    # ------------------------------------------------------------------

    async def _handle_generate_reply(
        self,
        parent_chat: StreamsUserChatData,
        request_data: dict,
        thread_data: dict,
    ) -> dict:
        """Collects thread replies once and generates a reply from the current user's viewpoint."""
        try:
            raw_replies = await streams_db_handler.get_streams_thread_messages(request_data)
            conversations = self.format_thread_messages(
                parent_chat=parent_chat,
                raw_replies=raw_replies,
                request_data=request_data,
                include_parent=True,
            )

            current_user = request_data.get("user_name") or request_data.get("agentid") or "User"
            user_req = thread_data.get("user_query") or ""
            thread_data["user_query"] = (
                f"current_user: {current_user}\n"
                f"user_request: {user_req}\n\n"
                f"The following are the messages in the thread in chronological order:\n{conversations}\n\n"
                f"Generate the appropriate, sendable reply as {current_user}. "
                f"If answering the thread requires current knowledge, latest events, real-time facts, or external documentation, "
                f"use the web_search tool to look up accurate information and incorporate it directly into the reply without any meta-talk."
            )

            total_content = str(f"{thread_data['user_query']}\n{thread_data['instructions']}")
            await validate_token_limits(total_content, thread_data)

            response = await model_router.generate(thread_data)
            message = response.text or ""

            # Retry on placeholder / meta responses
            if utils.is_meta_response(message):
                logger.warning(
                    f"Meta-response detected in thread reply: '{message}', retrying with reinforced prompt, "
                    f"agentid: {request_data.get('agentid')}"
                )
                retry_data = thread_data.copy()
                retry_data["instructions"] = (
                    f"{retry_data['instructions']}\n\n"
                    f"CRITICAL OVERRIDE: Your previous output was identified as a meta-announcement ('{message}'). "
                    f"Do NOT output search announcements, status updates, or phrases like 'Searching...', 'Let me look that up...'. "
                    f"Directly return the finalized, substantive reply to be sent in the thread."
                )
                try:
                    retry_resp = await model_router.generate(retry_data)
                    retry_msg = retry_resp.text or ""
                    if retry_msg and not utils.is_meta_response(retry_msg):
                        message = retry_msg
                except Exception as retry_err:
                    logger.error(f"Error during thread reply retry: {retry_err}, agentid: {request_data.get('agentid')}")

            logger.info(f"Thread reply generated: len={len(message)}, agentid: {request_data.get('agentid')}")
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message": message,
            }
        except Exception as e:
            logger.error(f"Error: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    # ------------------------------------------------------------------
    # Branch 2: Thread Summarization
    # ------------------------------------------------------------------

    async def _handle_summarize_thread(
        self,
        args: ProcessThreadArgs,
        parent_chat: StreamsUserChatData,
        parent_dt: datetime,
        request_data: dict,
        thread_data: dict,
    ) -> dict:
        """Handles ThreadCategory.SUMMARIZE: cache resolution, gap filling, or fresh summarization."""
        try:
            smsgid = request_data.get("smsgid")
            ref_id = int(smsgid)
            user_timezone = request_data.get("timezone")
            requested_user_name = request_data.get("user_name")

            # ------------------------------------------------------------------
            # Scenario A: Filtered queries (message_count, topic_name, buddy_name)
            # Custom/filtered slices bypass and do not overwrite the canonical summary cache.
            # ------------------------------------------------------------------
            if args.message_count:
                logger.info(
                    f"Generating summary for last {args.message_count} messages, "
                    f"agentid: {request_data.get('agentid')}"
                )
                return await self._summarize_by_message_count(
                    args, parent_chat, request_data, thread_data, requested_user_name
                )

            if args.topic_name or args.buddy_name:
                logger.info(
                    f"Generating fresh summary for thread filter "
                    f"(topic: {args.topic_name}, buddy: {args.buddy_name}), "
                    f"agentid: {request_data.get('agentid')}"
                )
                return await self._summarize_filtered_thread(
                    args, parent_chat, request_data, thread_data, requested_user_name
                )

            # ------------------------------------------------------------------
            # Scenario B: Canonical / Timeline Thread Summary
            # ------------------------------------------------------------------
            # 1. Resolve timeline bounds without loading bulk messages (zero DB queries)
            requested_start, requested_end = self._resolve_thread_bounds(
                args, parent_dt, user_timezone
            )

            # 2. Check summary cache FIRST
            raw_summaries = await opensips_db_handler.get_chat_summary_from_db(
                start_date=requested_start,
                end_date=requested_end,
                request_data=request_data,
                source_type=ai_constants.OPERATION_PROCESS_THREAD,
                ref_id=ref_id,
            )
            existing_summaries: list[StoredChatSummaryData] = (
                TypeAdapter(list[StoredChatSummaryData]).validate_python(raw_summaries)
                if raw_summaries
                else []
            )

            if existing_summaries:
                existing_summaries = self.deduplicate_and_filter_summaries(existing_summaries)

                # Check if an exact cached summary already covers the requested timeline
                cached_text = self._find_exact_cached_summary(
                    existing_summaries, requested_start, requested_end
                )
                if cached_text:
                    logger.info(
                        f"Cached thread summary exists covering [{requested_start} to {requested_end}], "
                        f"returning cached summary without fetching messages. agentid: {request_data.get('agentid')}"
                    )
                    return {
                        "status": status.HTTP_200_OK,
                        "msg": "Success",
                        "message": self._personalize_summary(cached_text, requested_user_name),
                    }

                # Partial summary exists: fetch ONLY the delta messages in coverage gaps
                response_data = await self._merge_and_update_partial_summaries(
                    existing_summaries=existing_summaries,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    parent_chat=parent_chat,
                    parent_dt=parent_dt,
                    args=args,
                    request_data=request_data,
                    thread_data=thread_data,
                )
                if "error" in response_data:
                    return response_data
            else:
                # No cached summary exists: fetch full thread messages, summarize, and persist
                logger.info(f"No existing summary found for thread smsgid: {smsgid}, generating fresh summary.")
                response_data = await self._generate_fresh_thread_summary(
                    args=args,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    parent_chat=parent_chat,
                    parent_dt=parent_dt,
                    ref_id=ref_id,
                    request_data=request_data,
                    thread_data=thread_data,
                )
                if "error" in response_data:
                    return response_data

            personalized_message = self._personalize_summary(
                response_data.get("message", ""), requested_user_name
            )

            logger.info(
                f"Thread summary complete: len={len(personalized_message)}, "
                f"agentid: {request_data.get('agentid')}"
            )
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message": personalized_message,
            }
        except Exception as e:
            logger.error(f"Error: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _summarize_by_message_count(
        self,
        args: ProcessThreadArgs,
        parent_chat: Optional[StreamsUserChatData],
        request_data: dict,
        thread_data: dict,
        requested_user_name: Optional[str],
    ) -> dict:
        """Summarizes strictly the last N replies. Provides parent message context with clear instructions to use only if necessary."""
        try:
            raw_replies = await streams_db_handler.get_streams_thread_messages(
                request_data, message_count=args.message_count
            )
            if not raw_replies:
                return {
                    "status": status.HTTP_404_NOT_FOUND,
                    "msg": "Failed",
                    "error": "No messages found for this thread.",
                }

            # Reverse from DESC (latest first) to chronological order (oldest to newest)
            raw_replies.reverse()

            # Format strictly the tail replies
            conversations = self.format_thread_messages(
                parent_chat=None,
                raw_replies=raw_replies,
                request_data=thread_data,
                include_parent=False,
            )

            if not conversations:
                return {
                    "status": status.HTTP_404_NOT_FOUND,
                    "msg": "Failed",
                    "error": "No valid conversations found for this thread.",
                }

            chat_data: dict = {"conversations": conversations}

            # Provide parent message for optional background context
            if parent_chat:
                formatted_parent = self.format_thread_messages(
                    parent_chat=parent_chat,
                    raw_replies=None,
                    request_data=thread_data,
                    include_parent=True,
                )
                if formatted_parent:
                    chat_data["thread_parent_message"] = {
                        "note": (
                            "This is the parent message of the thread. Use this only when it is necessary "
                            "for context, not compulsory. When the summary does not need the parent message, "
                            "please ignore it."
                        ),
                        "parent_message": formatted_parent[0],
                    }

            # Augment instructions to inform model how to treat parent message
            thread_data["instructions"] = (
                f"{thread_data.get('instructions', '')}\n\n"
                "NOTE ON PARENT MESSAGE: A parent message is provided under 'thread_parent_message' for background context. "
                "Use this only when it is necessary for context, not compulsory. When the summary of the requested "
                "messages does not need the parent message, please ignore it."
            )

            self.append_user_instructions(chat_data, args, thread_data)
            response_data = await self.summarize_conversations(chat_data, args, thread_data)

            personalized_message = self._personalize_summary(
                response_data.get("message", ""), requested_user_name
            )
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message": personalized_message,
            }
        except Exception as e:
            logger.error(f"Error in _summarize_by_message_count: {e}")
            raise e

    async def _summarize_filtered_thread(
        self,
        args: ProcessThreadArgs,
        parent_chat: StreamsUserChatData,
        request_data: dict,
        thread_data: dict,
        requested_user_name: Optional[str],
    ) -> dict:
        """Generates summary for thread filtered by topic or buddy without caching."""
        try:
            raw_replies = await streams_db_handler.get_streams_thread_messages(request_data)

            # Include parent message only if buddy filter does not exclude it
            include_parent = True
            if args.buddy_name and parent_chat:
                author = parent_chat.username or f"{parent_chat.firstname} {parent_chat.lastname}".strip()
                if args.buddy_name.lower() not in author.lower():
                    include_parent = False

            conversations = self.format_thread_messages(
                parent_chat=parent_chat,
                raw_replies=raw_replies,
                request_data=thread_data,
                include_parent=include_parent,
            )

            if not conversations:
                return {
                    "status": status.HTTP_404_NOT_FOUND,
                    "msg": "Failed",
                    "error": "No conversations found for this thread filter.",
                }

            chat_data = {"conversations": conversations}
            self.append_user_instructions(chat_data, args, thread_data)
            response_data = await self.summarize_conversations(chat_data, args, thread_data)

            personalized_message = self._personalize_summary(
                response_data.get("message", ""), requested_user_name
            )
            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message": personalized_message,
            }
        except Exception as e:
            logger.error(f"Error in _summarize_filtered_thread: {e}")
            raise e

    def _resolve_thread_bounds(
        self,
        args: ProcessThreadArgs,
        parent_dt: datetime,
        user_timezone: Optional[str],
    ) -> tuple[datetime, datetime]:
        """Resolves requested start and end dates in UTC using current UTC time if end_date omitted (zero DB calls)."""
        if args.start_date:
            requested_start = utils.convert_to_utc(args.start_date, user_timezone)
        else:
            requested_start = parent_dt

        if args.end_date:
            requested_end = utils.convert_to_utc(args.end_date, user_timezone)
        else:
            requested_end = datetime.now(timezone.utc)

        return requested_start, requested_end

    def _find_exact_cached_summary(
        self,
        summaries: list[StoredChatSummaryData],
        requested_start: datetime,
        requested_end: datetime,
    ) -> Optional[str]:
        """Returns cached summary text if an existing summary fully covers [requested_start, requested_end]."""
        for s in summaries:
            if s.start_date <= requested_start and requested_end <= s.end_date:
                return s.summary
        return None

    def _aggregate_segment_metadata(
        self,
        segments: list[dict],
        requested_start: datetime,
        requested_end: datetime,
    ) -> dict:
        """Aggregates conversation count, unique participant names, and duration across segments."""
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

        total_duration = utils.calculate_total_duration(requested_start, requested_end)
        return {
            "no_of_conversations": total_conv_count,
            "participants_info": all_participants,
            "total_duration": total_duration,
        }

    async def _merge_and_update_partial_summaries(
        self,
        existing_summaries: list[StoredChatSummaryData],
        requested_start: datetime,
        requested_end: datetime,
        parent_chat: StreamsUserChatData,
        parent_dt: datetime,
        args: ProcessThreadArgs,
        request_data: dict,
        thread_data: dict,
    ) -> dict:
        """Finds gaps in cached coverage, fetches ONLY delta messages for gaps, and merges."""
        try:
            gaps = self.find_coverage_gaps(requested_start, requested_end, existing_summaries, thread_data)
            logger.info(
                f"Thread has {len(existing_summaries)} existing summaries and {len(gaps)} uncovered gaps, "
                f"agentid: {request_data.get('agentid')}"
            )

            # Build segments: existing summaries + newly fetched delta messages for gaps only
            segments = await self.build_chronological_thread_segments(
                existing_summaries=existing_summaries,
                gaps=gaps,
                parent_chat=parent_chat,
                parent_dt=parent_dt,
                request_data=thread_data,
            )
            if not segments:
                return {
                    "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "msg": "Failed",
                    "error": "No segments to process for thread summary.",
                }

            # Check if any new chat messages were actually found in the gap window
            has_chat_segments = any(seg.get("type") == "chats" for seg in segments)
            if not has_chat_segments:
                logger.info(
                    f"No new thread messages found in gap window since last summary. "
                    f"Returning cached summary without LLM call. agentid: {request_data.get('agentid')}"
                )
                primary_summary = existing_summaries[-1]
                return {
                    "status": status.HTTP_200_OK,
                    "msg": "Success",
                    "message": primary_summary.summary,
                }

            summary_extra_data = self._aggregate_segment_metadata(
                segments, requested_start, requested_end
            )

            # Merge cached summary notes and new conversation segments
            response_data = await self.merge_segments_into_summary(segments, args, thread_data)

            # Update the cached summary row with the new extended end_date and merged text
            if response_data.get("message") and not response_data.get("partial_periods"):
                primary_summary = existing_summaries[-1]
                await opensips_db_handler.update_chat_summary_by_id(
                    summary_id=primary_summary.id,
                    summary=response_data["message"],
                    end_date=requested_end,
                    request_data=request_data,
                    extra_data=summary_extra_data,
                )
                logger.info(
                    f"Updated cached thread summary ID {primary_summary.id} to new end_date {requested_end}, "
                    f"agentid: {request_data.get('agentid')}"
                )

            return response_data
        except Exception as e:
            logger.error(f"Error: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def _generate_fresh_thread_summary(
        self,
        args: ProcessThreadArgs,
        requested_start: datetime,
        requested_end: datetime,
        parent_chat: StreamsUserChatData,
        parent_dt: datetime,
        ref_id: int,
        request_data: dict,
        thread_data: dict,
    ) -> dict:
        """Fetches thread replies once, generates fresh summary, and persists to DB if unfiltered."""
        try:
            user_timezone = request_data.get("timezone")

            # If start_date and end_date were specified, fetch by duration
            if args.start_date and args.end_date:
                raw_replies = await streams_db_handler.get_streams_thread_messages_by_duration(
                    request_data, start_date=requested_start, end_date=requested_end
                )
                include_parent = (requested_start <= parent_dt <= requested_end)
                actual_end = requested_end
            else:
                raw_replies = await streams_db_handler.get_streams_thread_messages(request_data)
                include_parent = True
                if raw_replies:
                    last_msg = raw_replies[-1]
                    last_time = (
                        last_msg.get("messagetime")
                        if isinstance(last_msg, dict)
                        else getattr(last_msg, "messagetime", None)
                    )
                    actual_end = utils.convert_to_utc(last_time, user_timezone) if last_time else parent_dt
                else:
                    actual_end = parent_dt

            conversations = self.format_thread_messages(
                parent_chat=parent_chat,
                raw_replies=raw_replies,
                request_data=thread_data,
                include_parent=include_parent,
            )

            if not conversations:
                return {
                    "status": status.HTTP_404_NOT_FOUND,
                    "msg": "Failed",
                    "error": "No conversations found for this thread.",
                }

            participants = list(dict.fromkeys(c.get("user") for c in conversations if c.get("user")))
            total_duration = utils.calculate_total_duration(requested_start, actual_end)
            summary_extra_data = {
                "no_of_conversations": len(conversations),
                "participants_info": participants,
                "total_duration": total_duration,
            }

            chat_data = {"conversations": conversations}
            self.append_user_instructions(chat_data, args, thread_data)
            response_data = await self.summarize_conversations(chat_data, args, thread_data)

            # Persist fresh summary in DB if summarization succeeded and not partial
            if response_data.get("message") and not response_data.get("partial_periods"):
                await opensips_db_handler.insert_chat_summary_into_db(
                    response_data["message"],
                    requested_start,
                    actual_end,
                    request_data,
                    extra_data=summary_extra_data,
                    source_type=ai_constants.OPERATION_PROCESS_THREAD,
                    ref_id=ref_id,
                )
                logger.info(
                    f"Persisted new thread summary in DB for smsgid: {ref_id}, "
                    f"start: {requested_start}, end: {actual_end}, agentid: {request_data.get('agentid')}"
                )
            return response_data

        except Exception as e:
            logger.error(f"Error in _generate_fresh_thread_summary: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    def _personalize_summary(self, summary_text: str, requested_user_name: Optional[str]) -> str:
        """Personalizes 2nd person pronoun if requested user is named in summary."""
        if requested_user_name and summary_text:
            pattern = rf"\b{re.escape(requested_user_name)}\b"
            return re.sub(pattern, "you", summary_text, flags=re.IGNORECASE)
        return summary_text

    # ------------------------------------------------------------------
    # Thread Message Formatting & Chronological Segment Building
    # ------------------------------------------------------------------

    def format_thread_messages(
        self,
        parent_chat: Optional[StreamsUserChatData],
        raw_replies: Optional[list],
        request_data: dict,
        include_parent: bool = True,
    ) -> list[dict]:
        """Combines parent chat and thread replies, filters conversations, and localizes timestamps."""
        try:
            all_chats: list[StreamsUserChatData] = []
            if include_parent and parent_chat:
                all_chats.append(parent_chat)

            if raw_replies:
                thread_replies = TypeAdapter(list[StreamsUserChatData]).validate_python(raw_replies)
                all_chats.extend(thread_replies)

            if not all_chats:
                return []

            # conversations = await process_messages_attachments(
            #     user_chats=user_chats,
            #     request_data=request_data,
            #     target_dir=target_dir,
            #     max_concurrent=5,
            # )

            user_timezone = request_data.get("timezone")
            normalized = self.normalize_chat_messages(all_chats, user_timezone)
            conversations = filter_conversations(normalized)
            return conversations
        except Exception as e:
            logger.exception(f"Error formatting thread messages: {e}")
            return []

    async def build_chronological_thread_segments(
        self,
        existing_summaries: list[StoredChatSummaryData],
        gaps: list[dict],
        parent_chat: StreamsUserChatData,
        parent_dt: datetime,
        request_data: dict,
    ) -> list[dict]:
        """Builds chronological segments (stored summaries + delta messages fetched for gaps)."""
        try:
            segments: list[dict] = []
            user_timezone = request_data.get("timezone")

            # 1. Convert cached summaries from DB into 'summary' segments
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

            # 2. Fetch delta messages strictly for each gap window
            for gap in gaps:
                gap_start: datetime = gap["start"]
                gap_end: datetime = gap["end"]

                raw_gap_replies = await streams_db_handler.get_streams_thread_messages_by_duration(
                    request_data, start_date=gap_start, end_date=gap_end
                )

                # Only include parent if it actually falls within this specific gap window
                include_parent = (gap_start <= parent_dt <= gap_end)
                gap_chats = self.format_thread_messages(
                    parent_chat=parent_chat,
                    raw_replies=raw_gap_replies,
                    request_data=request_data,
                    include_parent=include_parent,
                )

                gap_start_str = utils.convert_utc_to_timezone(gap_start, user_timezone)
                gap_end_str = utils.convert_utc_to_timezone(gap_end, user_timezone)

                if gap_chats:
                    segments.append({
                        "type": "chats",
                        "conversations": gap_chats,
                        "start": gap_start,
                        "end": gap_end,
                        "date_range": f"{gap_start_str} to {gap_end_str}",
                    })
                else:
                    logger.info(
                        f"No thread messages found for gap {gap['start']} → {gap['end']}, "
                        f"agentid: {request_data.get('agentid')}"
                    )

            # Sort all segments together chronologically so the LLM receives an unbroken narrative flow
            segments.sort(key=lambda x: x["start"])
            logger.info(f"Thread segments assembled: count={len(segments)}, agentid: {request_data.get('agentid')}")
            return segments
        except Exception as e:
            logger.exception(f"Error building chronological thread segments: {e}")
            raise e


thread_process_handler = ThreadProcessHandler()
