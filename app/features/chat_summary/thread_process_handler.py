import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import status
from pydantic import TypeAdapter

from app.ai import ai_constants
from app.ai.ai_constants import SummaryCategory
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
    SummaryNLPExtractedData,
)
from app.ai.prompts.chat_summary import (
    EXCEEDS_MAX_DURATION_RESPONSE,
    EXCEEDS_MAX_MESSAGES_RESPONSE,
    THREAD_PARENT_MESSAGE_INSTRUCTION,
    THREAD_PARENT_MESSAGE_NOTE,
)
from app.ai.prompts.reply_to_thread import (
    THREAD_REPLY_META_RETRY_INSTRUCTION_TEMPLATE,
    THREAD_REPLY_USER_QUERY_TEMPLATE,
)
from app.features.chat_summary.summary_pipeline import BaseSummaryPipeline
from app.workers.attachment_worker import (
    TEMP_ATTACHMENT_DIR,
    process_messages_attachments,
)

logger = logging.getLogger(__name__)


class ThreadProcessHandler(BaseSummaryPipeline):

    async def process_thread_request(self, args: SummaryNLPExtractedData, request_data: dict) -> dict:
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
            if category == SummaryCategory.THREAD_SUMMARY and args.message_count and args.message_count > 10000:
                logger.warning(
                    f"Requested thread summary message count exceeds 10,000 ({args.message_count}), agentid: {request_data.get('agentid')}"
                )
                return {
                    "status": status.HTTP_200_OK,
                    "msg": "Success",
                    "message": EXCEEDS_MAX_MESSAGES_RESPONSE,
                }

            thread_config = await ai_config_builder.prepare_process_thread_config(request_data, category)
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
            parent_dt: datetime = utils.convert_to_utc(parent_chat.messagetime, source_tz="UTC")

            # Dispatch by Category
            if category == SummaryCategory.GENERATE_REPLY:
                return await self._handle_generate_reply(parent_chat, request_data, thread_data)

            elif category == SummaryCategory.THREAD_SUMMARY:
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
            thread_data["user_query"] = THREAD_REPLY_USER_QUERY_TEMPLATE.format(
                current_user=current_user,
                user_req=user_req,
                conversations=conversations,
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
                    f"{THREAD_REPLY_META_RETRY_INSTRUCTION_TEMPLATE.format(message=message)}"
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
        args: SummaryNLPExtractedData,
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
                if args.message_count > 10000:
                    logger.warning(
                        f"Requested thread summary message count exceeds 10,000 ({args.message_count}), agentid: {request_data.get('agentid')}"
                    )
                    return {
                        "status": status.HTTP_200_OK,
                        "msg": "Success",
                        "message": EXCEEDS_MAX_MESSAGES_RESPONSE,
                    }
                logger.info(
                    f"Generating summary for last {args.message_count} messages, "
                    f"agentid: {request_data.get('agentid')}"
                )
                return await self._summarize_by_message_count(
                    args, parent_chat, request_data, thread_data, requested_user_name
                )

            if args.topic_name or args.buddy_name or args.context:
                logger.info(
                    f"Generating fresh summary for thread filter "
                    f"(topic: {args.topic_name}, buddy: {args.buddy_name}, context: {args.context}), "
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

            if args.start_date and args.end_date:
                duration_days = (requested_end - requested_start).total_seconds() / 86400
                if duration_days > 93:
                    logger.warning(
                        f"Requested thread summary period exceeds 3 months ({duration_days:.1f} days), agentid: {request_data.get('agentid')}"
                    )
                    return {
                        "status": status.HTTP_200_OK,
                        "msg": "Success",
                        "message": EXCEEDS_MAX_DURATION_RESPONSE,
                    }

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
                        "message": self.personalize_summary(cached_text, requested_user_name),
                    }

                # Fast-path: When existing summary covers from start and no new replies arrived up to requested_end, return cached summary directly
                primary_summary = existing_summaries[-1]
                if existing_summaries[0].start_date <= requested_start and primary_summary.end_date < requested_end:
                    trailing_messages = await streams_db_handler.get_streams_thread_messages_by_duration(
                        request_data,
                        start_date=primary_summary.end_date + timedelta(seconds=1),
                        end_date=requested_end,
                    )
                    if not trailing_messages:
                        logger.info(
                            f"Thread summary already covers from start ({existing_summaries[0].start_date}) to {primary_summary.end_date} "
                            f"and no new messages exist up to {requested_end}. Returning cached summary directly. agentid: {request_data.get('agentid')}"
                        )
                        return {
                            "status": status.HTTP_200_OK,
                            "msg": "Success",
                            "message": self.personalize_summary(primary_summary.summary, requested_user_name),
                        }

                logger.info(
                    f"existing summaries cover only partial date range, "
                    f"will fetch only the delta messages in coverage gaps, agentid: {request_data.get('agentid')}"
                )

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

            personalized_message = self.personalize_summary(
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
        args: SummaryNLPExtractedData,
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
                        "note": THREAD_PARENT_MESSAGE_NOTE,
                        "parent_message": formatted_parent[0],
                    }

            # Augment instructions to inform model how to treat parent message
            thread_data["instructions"] = (
                f"{thread_data.get('instructions', '')}\n\n"
                f"{THREAD_PARENT_MESSAGE_INSTRUCTION}"
            )

            self.append_user_instructions(chat_data, args, thread_data)
            response_data = await self.summarize_conversations(chat_data, args, thread_data)

            personalized_message = self.personalize_summary(
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
        args: SummaryNLPExtractedData,
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

            personalized_message = self.personalize_summary(
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
        args: SummaryNLPExtractedData,
        parent_dt: datetime,
        user_timezone: Optional[str],
    ) -> tuple[datetime, datetime]:
        """Resolves requested start and end dates in UTC using current UTC time if end_date omitted (zero DB calls)."""
        if args.start_date:
            requested_start = utils.convert_to_utc(args.start_date, user_timezone)
        else:
            requested_start = parent_dt.replace(second=0, microsecond=0)

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
        return self.aggregate_segment_metadata(segments, requested_start, requested_end)

    async def _merge_and_update_partial_summaries(
        self,
        existing_summaries: list[StoredChatSummaryData],
        requested_start: datetime,
        requested_end: datetime,
        parent_chat: StreamsUserChatData,
        parent_dt: datetime,
        args: SummaryNLPExtractedData,
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
            if not self.has_chat_segments(segments):
                logger.info(
                    f"No new thread messages found in gap window since last summary. "
                    f"Returning cached summary without LLM call. agentid: {request_data.get('agentid')}"
                )
                primary_summary = existing_summaries[-1]
                return {
                    "status": status.HTTP_200_OK,
                    "msg": "Success",
                    "message": self.personalize_summary(primary_summary.summary, request_data.get("user_name")),
                }

            primary_summary = existing_summaries[-1]
            summary_start = primary_summary.start_date or requested_start
            summary_extra_data = self._aggregate_segment_metadata(
                segments, summary_start, requested_end
            )

            # Merge cached summary notes and new conversation segments
            response_data = await self.merge_segments_into_summary(segments, args, thread_data)

            # Update the cached summary row with the new extended end_date and merged text
            if response_data.get("message") and not response_data.get("partial_periods"):
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
        args: SummaryNLPExtractedData,
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
                    actual_end = utils.convert_to_utc(last_time, source_tz="UTC") if last_time else parent_dt
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

            # Persist fresh summary in DB if summarization succeeded, not partial, and not filtered/custom
            if response_data.get("message") and not response_data.get("partial_periods") and args.should_store_summary:
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
            return self.normalize_and_filter_chat_messages(all_chats, user_timezone)
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
        async def fetch_gap(gap_start: datetime, gap_end: datetime) -> list[dict]:
            raw_gap_replies = await streams_db_handler.get_streams_thread_messages_by_duration(
                request_data, start_date=gap_start, end_date=gap_end
            )
            include_parent = (gap_start <= parent_dt <= gap_end)
            return self.format_thread_messages(
                parent_chat=parent_chat,
                raw_replies=raw_gap_replies,
                request_data=request_data,
                include_parent=include_parent,
            )

        return await self.build_chronological_segments(
            existing_summaries=existing_summaries,
            gaps=gaps,
            request_data=request_data,
            fetch_gap_conversations=fetch_gap,
        )


thread_process_handler = ThreadProcessHandler()
