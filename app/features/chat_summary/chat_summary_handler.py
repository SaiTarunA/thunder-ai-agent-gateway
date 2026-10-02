import json
import logging
import shutil
from datetime import datetime
from typing import Optional
import re
from fastapi import status
from pydantic import TypeAdapter

from app.ai.config_builder import ai_config_builder
from app.ai.prompts.chat_summary import (
    EXCEEDS_MAX_DURATION_RESPONSE,
    EXCEEDS_MAX_MESSAGES_RESPONSE,
)
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

            if requested_start and requested_end:
                duration_days = (requested_end - requested_start).total_seconds() / 86400
                if duration_days > 93:
                    logger.warning(
                        f"Requested summary period exceeds 3 months ({duration_days:.1f} days), agentid : {request_data.get('agentid')}"
                    )
                    return {
                        "status": status.HTTP_200_OK,
                        "msg": "Success",
                        "message": EXCEEDS_MAX_DURATION_RESPONSE,
                    }

            requested_message_count: int | None = nlp_response.message_count
            if requested_message_count and requested_message_count > 10000:
                logger.warning(
                    f"Requested summary message count exceeds 10,000 ({requested_message_count}), agentid : {request_data.get('agentid')}"
                )
                return {
                    "status": status.HTTP_200_OK,
                    "msg": "Success",
                    "message": EXCEEDS_MAX_MESSAGES_RESPONSE,
                }

            request_data = {**request_data, **await ai_config_builder.prepare_chat_summary_config()}

            should_generate_fresh_summary = False
            summary_extra_data: dict = {}

            if requested_message_count:
                logger.info(f"Generating summary for message count : {requested_message_count} for agentid : {request_data.get('agentid')}")
                should_generate_fresh_summary = True

            elif nlp_response.unread_messages:
                logger.info(f"Generating summary for unread messages for agentid : {request_data.get('agentid')}")
                should_generate_fresh_summary = True

            elif nlp_response.topic_name or nlp_response.buddy_name or nlp_response.context or nlp_response.is_resummarization_request:
                logger.info(
                    f"Generating fresh summary for specific category/resummarization (topic: {nlp_response.topic_name}, "
                    f"buddy: {nlp_response.buddy_name}, context: {nlp_response.context}, resummarize: {nlp_response.is_resummarization_request}) "
                    f"for agentid : {request_data.get('agentid')}"
                )
                should_generate_fresh_summary = True

            elif requested_start and requested_end:
                existing_summaries: list[StoredChatSummaryData] = TypeAdapter(list[StoredChatSummaryData]).validate_python(
                    await opensips_db_handler.get_chat_summary_from_db(requested_start, requested_end, request_data)
                )

                if existing_summaries:
                    existing_summaries = self.deduplicate_and_filter_summaries(existing_summaries)

                    exact_cached = self.find_exact_cached_summary(existing_summaries, requested_start, requested_end)
                    if exact_cached:
                        logger.info(
                            f"A summary covering the entire requested range already exists, returning cached summary. "
                            f"agentid: {request_data.get('agentid')}"
                        )
                        return {
                            "status": status.HTTP_200_OK,
                            "msg": "Success",
                            "message": self.personalize_summary(exact_cached, requested_user_name),
                        }

                    gaps = self.find_coverage_gaps(requested_start, requested_end, existing_summaries, request_data)
                    logger.info(f"Found {len(existing_summaries)} existing summaries and {len(gaps)} gaps, agentid: {request_data.get('agentid')}")

                    segments = await self.build_chronological_segments(existing_summaries, gaps, request_data)

                    if not segments:
                        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "msg": "Failed", "error": "No segments to process"}

                    # When the existing summary covers from start to finish and no new chat messages exist in any gap, return cached summary directly
                    if not self.has_chat_segments(segments):
                        logger.info(
                            f"Existing summary covers start to finish and no new chat messages found in gaps. "
                            f"Returning cached summary without AI call. agentid: {request_data.get('agentid')}"
                        )
                        primary_summary = existing_summaries[-1]
                        return {
                            "status": status.HTTP_200_OK,
                            "msg": "Success",
                            "message": self.personalize_summary(primary_summary.summary, requested_user_name),
                        }

                    summary_extra_data = self.aggregate_segment_metadata(segments, requested_start, requested_end)
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
                            f"(topic: {nlp_response.topic_name}, buddy: {nlp_response.buddy_name}, context: {nlp_response.context}, "
                            f"unread: {nlp_response.unread_messages}, count: {nlp_response.message_count}, agentid: {request_data.get('agentid')}"
                        )

                response_data["message"] = self.personalize_summary(response_data["message"], requested_user_name)
                logger.info(f"Updated chat summary :: {response_data['message']}, agentid :: {request_data.get('agentid')}")

            response_data["request_params"] = request_params

            return response_data

        except Exception as e:
            logger.info(f"Error :: {str(e)}, agentid : {request_data.get('agentid')} ")
            return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "error": str(e), "msg": "Failed"}


    async def build_chronological_segments(self, existing_summaries: list[StoredChatSummaryData], gaps: list[dict], request_data: dict) -> list[dict]:
        """Builds a list of segments (existing summaries + newly collected gap chats) sorted chronologically."""
        async def fetch_gap(gap_start: datetime, gap_end: datetime) -> list[dict]:
            gap_nlp = SummaryNLPExtractedData(
                start_date=gap_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=gap_end.strftime("%Y-%m-%d %H:%M:%S"),
            )
            gap_chats = await self.collect_user_conversations(
                gap_nlp, request_data, start_date=gap_start, end_date=gap_end
            )
            return gap_chats.get("conversations") if gap_chats else []

        return await super().build_chronological_segments(
            existing_summaries=existing_summaries,
            gaps=gaps,
            request_data=request_data,
            fetch_gap_conversations=fetch_gap,
        )

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

                conversations = self.normalize_and_filter_chat_messages(user_chats, user_timezone)

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
