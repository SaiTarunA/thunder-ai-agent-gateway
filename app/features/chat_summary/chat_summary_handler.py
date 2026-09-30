import json
import logging
import shutil
from datetime import datetime
from typing import Optional
import re
from fastapi import status
from pydantic import TypeAdapter

from app.ai.config_builder import ai_config_builder
from app.core.utils import utils
from app.db.mysql.repositories.opensips_repo import opensips_db_handler
from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.features.chat_summary.schemas import (
    StoredChatSummaryData,
    StreamsUserChatData,
    SummaryNLPExtractedData,
)
from app.features.chat_summary.summary_pipeline import BaseSummaryPipeline
from app.workers.attachment_worker import (
    TEMP_ATTACHMENT_DIR,
    process_messages_attachments,
)
from app.core.message_cleaner import filter_conversations

logger = logging.getLogger(__name__)


class ChatSummaryHandler(BaseSummaryPipeline):

    async def process_chat_summary_request(self, nlp_response: SummaryNLPExtractedData, request_data):
        try:
            request_params = request_data.copy()

            requested_user_name = request_data.get('user_name')
            user_timezone = request_data.get('timezone')
            requested_start = None
            requested_end = None

            if nlp_response.start_date:
                requested_start = utils.convert_to_utc(nlp_response.start_date, user_timezone)

            if nlp_response.end_date:
                requested_end = utils.convert_to_utc(nlp_response.end_date, user_timezone)

            requested_message_count: int | None = nlp_response.message_count

            request_data = {**request_data, **await ai_config_builder.prepare_chat_summary_config()}

            should_generate_fresh_summary = False
            summary_extra_data: dict = {}

            if requested_message_count:
                logger.info(f"Generating summary for message count : {requested_message_count} for agentid : {request_data.get('agentid')}")
                should_generate_fresh_summary = True

            elif nlp_response.unread_messages:
                logger.info(f"Generating summary for unread messages for agentid : {request_data.get('agentid')}")
                should_generate_fresh_summary = True

            elif nlp_response.topic_name or nlp_response.buddy_name or nlp_response.is_resummarization_request:
                logger.info(
                    f"Generating fresh summary for specific category/resummarization (topic: {nlp_response.topic_name}, "
                    f"buddy: {nlp_response.buddy_name}, resummarize: {nlp_response.is_resummarization_request}) "
                    f"for agentid : {request_data.get('agentid')}"
                )
                should_generate_fresh_summary = True

            elif requested_start and requested_end:
                existing_summaries: list[StoredChatSummaryData] = TypeAdapter(list[StoredChatSummaryData]).validate_python(
                    await opensips_db_handler.get_chat_summary_from_db(requested_start, requested_end, request_data)
                )

                if existing_summaries:
                    existing_summaries = self.deduplicate_and_filter_summaries(existing_summaries)

                    for s in existing_summaries:
                        if s.start_date == requested_start and s.end_date == requested_end:
                            logger.info(f"A summary covering the entire requested range already exists, returning cached summary. agentid: {request_data.get('agentid')}")
                            if requested_user_name:
                                pattern = rf"\b{re.escape(requested_user_name)}\b"
                                s.summary = re.sub(pattern, "you", s.summary, flags=re.IGNORECASE)
                            return {"status": status.HTTP_200_OK, "msg": "Success", "message": s.summary}

                    gaps = self.find_coverage_gaps(requested_start, requested_end, existing_summaries, request_data)
                    logger.info(f"Found {len(existing_summaries)} existing summaries and {len(gaps)} gaps, agentid: {request_data.get('agentid')}")

                    segments = await self.build_chronological_segments(existing_summaries, gaps, request_data)

                    if not segments:
                        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "msg": "Failed", "error": "No segments to process"}

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

                    summary_extra_data = {
                        "no_of_conversations": total_conv_count,
                        "participants_info": all_participants,
                        "total_duration": total_duration,
                    }

                    response_data = await self.merge_segments_into_summary(segments, nlp_response, request_data)

                else:
                    logger.info(f"No existing summaries found, generating fresh summary for {request_data.get('agentid')}.")
                    should_generate_fresh_summary = True

            else:
                logger.error(f"User Not specified proper time line or no. of messages count :: agentid :: {request_data.get('agentid')}")
                return {"status": status.HTTP_404_NOT_FOUND, "msg": "Failed", "error": "Please provide a valid timeline or message count."}

            if should_generate_fresh_summary:
                conversations = await self.collect_user_conversations(
                    nlp_response, request_data, start_date=requested_start, end_date=requested_end
                )

                if not conversations:
                    return {"status": status.HTTP_404_NOT_FOUND, "msg": "Failed", "error": "No conversations found for the selected date range."}

                raw_convs = conversations.get("conversations", [])
                participants = list(dict.fromkeys(c.get("user") for c in raw_convs if c.get("user")))
                total_duration = utils.calculate_total_duration(requested_start, requested_end) if requested_start and requested_end else "00:00:00"

                summary_extra_data = {
                    "no_of_conversations": len(raw_convs),
                    "participants_info": participants,
                    "total_duration": total_duration,
                }

                response_data = await self.summarize_conversations(conversations, nlp_response, request_data)

            if response_data.get("message"):
                if response_data.get("partial_periods"):
                    logger.warning(
                        f"Skipping storing summary in DB as some periods could not be summarized: "
                        f"{response_data['partial_periods']}, agentid: {request_data.get('agentid')}"
                    )
                elif requested_start and requested_end:
                    if nlp_response.is_resummarization_request:
                        await opensips_db_handler.update_chat_summary_into_db(response_data["message"], requested_start, requested_end, request_data, extra_data=summary_extra_data)

                    elif nlp_response.should_store_summary:
                        await opensips_db_handler.insert_chat_summary_into_db(
                            response_data["message"], requested_start, requested_end, request_data, extra_data=summary_extra_data
                        )
                    else:
                        logger.info(
                            f"Skipping storing summary in DB as it is category/parameter-specific "
                            f"(topic: {nlp_response.topic_name}, buddy: {nlp_response.buddy_name}, "
                            f"unread: {nlp_response.unread_messages}, count: {nlp_response.message_count}, agentid: {request_data.get('agentid')}"
                        )

                if requested_user_name:
                    pattern = rf"\b{re.escape(requested_user_name)}\b"
                    response_data["message"] = re.sub(pattern, "you", response_data["message"], flags=re.IGNORECASE)
                logger.info(f"Updated chat summary :: {response_data['message']}, agentid :: {request_data.get('agentid')}")

            response_data["request_params"] = request_params

            return response_data

        except Exception as e:
            logger.info(f"Error :: {str(e)}, agentid : {request_data.get('agentid')} ")
            return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "error": str(e), "msg": "Failed"}


    async def build_chronological_segments(self, existing_summaries: list[StoredChatSummaryData], gaps: list[dict], request_data: dict) -> list[dict]:
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
                    "type":       "summary",
                    "text":       s.summary,
                    "start":      s.start_date,
                    "end":        s.end_date,
                    "date_range": f"{s_start_str} to {s_end_str}",
                    "extra_data": extra,
                })

            # 2. Query and collect raw chats for each uncovered time gap
            for gap in gaps:
                gap_start: datetime = gap["start"]
                gap_end: datetime   = gap["end"]

                gap_nlp = SummaryNLPExtractedData(
                    start_date=gap_start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_date=gap_end.strftime("%Y-%m-%d %H:%M:%S"),
                )
                gap_chats = await self.collect_user_conversations(
                    gap_nlp, request_data, start_date=gap_start, end_date=gap_end
                )
                conversations = gap_chats.get("conversations") if gap_chats else []

                gap_start_str = utils.convert_utc_to_timezone(gap_start, user_timezone)
                gap_end_str = utils.convert_utc_to_timezone(gap_end, user_timezone)

                if conversations:
                    segments.append({
                        "type":          "chats",
                        "conversations": conversations,
                        "start":         gap_start,
                        "end":           gap_end,
                        "date_range":    f"{gap_start_str} to {gap_end_str}",
                    })
                else:
                    logger.info(f"No chats found for gap {gap['start']} → {gap['end']}, agentid: {request_data.get('agentid')}")

            logger.info(f"segments as following : \n{segments}, agentid :: {request_data.get('agentid')}")
            # Sort all segments together chronologically so the LLM gets an unbroken narrative flow
            segments.sort(key=lambda x: x["start"])
            return segments
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def collect_user_conversations(
        self,
        nlp_response: SummaryNLPExtractedData,
        request_data,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ):
        """To collect user conversations for generating chat summary."""
        try:
            user_timezone = request_data.get("timezone")
            utc_start = start_date if start_date is not None else utils.convert_to_utc(nlp_response.start_date, user_timezone)
            utc_end = end_date if end_date is not None else utils.convert_to_utc(nlp_response.end_date, user_timezone)

            user_chats: list[StreamsUserChatData] = TypeAdapter(list[StreamsUserChatData]).validate_python(
                await streams_db_handler.get_streams_user_chat(
                    request_data,
                    start_date=utc_start,
                    end_date=utc_end,
                    message_count=nlp_response.message_count,
                    unread_messages=nlp_response.unread_messages,
                )
            )

            logger.info(f"for {request_data.get('sid')}, collected {len(user_chats)} no. of conversations, agentid : {request_data.get('agentid')}")

            if not user_chats:
                return None

            if not request_data.get("archiveid") and not request_data.get("archive_id"):
                request_data["archive_id"] = getattr(user_chats[0], "archiveid", 0) or 0
            if not request_data.get("siteid") and not request_data.get("site_id"):
                request_data["site_id"] = getattr(user_chats[0], "siteid", 0) or 0

            # The DB query uses ORDER BY DESC to fetch the latest N messages / unread messages.
            # We reverse them here so conversations are in chronological order (oldest to newest) for summarization.
            if nlp_response.message_count or nlp_response.unread_messages:
                user_chats.reverse()

            # Create an isolated temporary directory for downloading and parsing message attachments
            sid = str(request_data.get("sid") or "").strip()
            agentid = str(request_data.get("agentid") or "").strip()
            folder_name = f"{sid}{agentid}".strip()
            target_dir = (TEMP_ATTACHMENT_DIR / folder_name) if folder_name else (TEMP_ATTACHMENT_DIR / f"temp_{agentid}")

            try:
                # conversations = await process_messages_attachments(
                #     user_chats=user_chats,
                #     request_data=request_data,
                #     target_dir=target_dir,
                #     max_concurrent=5,
                # )

                normalized = self.normalize_chat_messages(user_chats, user_timezone)
                conversations = filter_conversations(normalized)

                if not conversations:
                    logger.info(f"No valid conversations remaining after filtering noise, agentid : {request_data.get('agentid')}")
                    return None

                logger.info(f"length of conversations :: {len(conversations)}, agentid : {request_data.get('agentid')}")

                chat_data = {"conversations": conversations}
                self.append_user_instructions(chat_data, nlp_response, request_data)
                return chat_data
            finally:
                # Ensure temporary files/attachments are cleaned up even if processing raises an exception
                if target_dir.exists():
                    logger.info(f"deleting temp directory :: {target_dir}, agentid :: {request_data.get('agentid')}")
                    shutil.rmtree(target_dir, ignore_errors=True)

        except Exception as e:
            logger.error(f"Error ========  :: {e}, agentid :: {request_data.get('agentid')}")
            raise e



chat_summary_handler = ChatSummaryHandler()
