import json
import logging
from datetime import datetime, timedelta

from fastapi import status
from pydantic import TypeAdapter

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

logger = logging.getLogger(__name__)


class ChatSummaryHandler():

    async def process_chat_summary_request(self, nlp_response: SummaryNLPExtractedData, request_data):
        try:

            request_params = request_data.copy()

            requested_user_name = request_data.get('user_name')
            requested_start = None
            requested_end = None

            if nlp_response.start_date:
                requested_start = datetime.strptime(nlp_response.start_date, "%Y-%m-%d %H:%M:%S")

            if nlp_response.end_date:
                requested_end = datetime.strptime(nlp_response.end_date, "%Y-%m-%d %H:%M:%S")

            requested_message_count: int | None = nlp_response.message_count

            request_data = {**request_data, **await ai_config_builder.prepare_chat_summary_config()}

            should_generate_fresh_summary = False

            if requested_message_count:
                logger.info(f"Generating summary for message count : {requested_message_count} for agentid : {request_data.get('agentid')}")
                should_generate_fresh_summary = True

            elif nlp_response.unread_messages:
                logger.info(f"Generating summary for unread messages for agentid : {request_data.get('agentid')}")
                should_generate_fresh_summary = True

            elif requested_start and requested_end:
                existing_summaries: list[StoredChatSummaryData] = TypeAdapter(list[StoredChatSummaryData]).validate_python(
                    await opensips_db_handler.get_chat_summary_from_db(requested_start, requested_end, request_data)
                )

                if existing_summaries:
                    for s in existing_summaries:
                        if s.start_date == requested_start and s.end_date == requested_end:
                            logger.info(f"A summary covering the entire requested range already exists, returning cached summary. agentid: {request_data.get('agentid')}")
                            s.summary = s.summary.replace(requested_user_name, "you")
                            return {"status": status.HTTP_200_OK, "msg": "Success", "message": s.summary}

                    gaps = self.find_coverage_gaps(requested_start, requested_end, existing_summaries, request_data)
                    logger.info(f"Found {len(existing_summaries)} existing summaries and {len(gaps)} gaps, agentid: {request_data.get('agentid')}")

                    segments = await self.build_chronological_segments(existing_summaries, gaps, request_data)

                    if not segments:
                        return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "msg": "Failed", "error": "No segments to process"}

                    response_data = await self.merge_segments_into_summary(segments, nlp_response, request_data)

                else:
                    logger.info(f"No existing summaries found, generating fresh summary for {request_data.get('agentid')}.")
                    should_generate_fresh_summary = True

            else:
                logger.error(f"User Not specified proper time line or no. of messages count :: agentid :: {request_data.get('agentid')}")
                return {"status": status.HTTP_404_NOT_FOUND, "msg": "Failed", "error": "Please provide a valid timeline or message count."}

            if should_generate_fresh_summary:
                conversations = await self.collect_user_conversations(nlp_response, request_data)

                if not conversations:
                    return {"status": status.HTTP_404_NOT_FOUND, "msg": "Failed", "error": "No conversations found for the selected date range."}

                request_data["user_query"] = json.dumps(conversations, indent=4)
                response_data = await self.generate_chat_summary(request_data)

            if response_data.get("message"):
                if not nlp_response.message_count or not nlp_response.unread_messages:
                    if nlp_response.is_resummarization_request:
                        await opensips_db_handler.update_chat_summary_into_db(response_data["message"], requested_start, requested_end, request_data)
                    else:
                        await opensips_db_handler.insert_chat_summary_into_db(response_data["message"], requested_start, requested_end, request_data)

                response_data["message"] = response_data["message"].replace(requested_user_name, "you")
                logger.info(f"Updated chat summary :: {response_data['message']}, agentid :: {request_data.get('agentid')}")

            response_data["request_params"] = request_params

            return response_data

        except Exception as e:
            logger.info(f"Error :: {str(e)}, agentid : {request_data.get('agentid')} ")
            return {"status": status.HTTP_500_INTERNAL_SERVER_ERROR, "error": str(e), "msg": "Failed"}

    def find_coverage_gaps(self, requested_start: str, requested_end: str, existing_summaries: list[StoredChatSummaryData], request_data: dict) -> list[dict]:
        """Returns all date ranges within [requested_start, requested_end] NOT covered by any existing summary."""
        try:
            intervals: list[tuple[str, str]] = []
            for s in existing_summaries:
                clipped_start = max(s.start_date, requested_start)
                clipped_end = min(s.end_date, requested_end)
                if clipped_start <= clipped_end:
                    intervals.append((clipped_start, clipped_end))

            intervals.sort(key=lambda x: x[0])
            merged: list[tuple[str, str]] = []
            for start, end in intervals:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            gaps: list[dict] = []
            cursor = requested_start

            for cov_start, cov_end in merged:
                if cursor < cov_start:
                    gap_end = cov_start - timedelta(seconds=1)
                    gaps.append({"start": cursor, "end": gap_end})
                cursor = cov_end + timedelta(seconds=1)

            if cursor <= requested_end:
                gaps.append({"start": cursor, "end": requested_end})

            logger.info(f"found gaps as following : \n{gaps}, agentid :: {request_data.get('agentid')}")
            return gaps
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def build_chronological_segments(self, existing_summaries: list[StoredChatSummaryData], gaps: list[dict], request_data: dict) -> list[dict]:
        """Builds a list of segments (existing summaries + newly collected gap chats) sorted chronologically."""
        try:
            segments: list[dict] = []

            for s in existing_summaries:
                segments.append({
                    "type":       "summary",
                    "text":       s.summary,
                    "start":      s.start_date,
                    "end":        s.end_date,
                    "date_range": f"{s.start_date.strftime('%Y-%m-%d %H:%M:%S')} to {s.end_date.strftime('%Y-%m-%d %H:%M:%S')}"
                })

            for gap in gaps:
                gap_start: datetime = gap["start"]
                gap_end: datetime   = gap["end"]

                gap_nlp = SummaryNLPExtractedData(
                    start_date=gap_start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_date=gap_end.strftime("%Y-%m-%d %H:%M:%S"),
                )
                gap_chats = await self.collect_user_conversations(gap_nlp, request_data)
                conversations = gap_chats.get("conversations") if gap_chats else []

                if conversations:
                    segments.append({
                        "type":          "chats",
                        "conversations": conversations,
                        "start":         gap_start,
                        "end":           gap_end,
                        "date_range":    f"{gap_start.strftime('%Y-%m-%d %H:%M:%S')} to {gap_end.strftime('%Y-%m-%d %H:%M:%S')}",
                    })
                else:
                    logger.info(f"No chats found for gap {gap['start']} → {gap['end']}, agentid: {request_data.get('agentid')}")

            logger.info(f"segments as following : \n{segments}, agentid :: {request_data.get('agentid')}")
            segments.sort(key=lambda x: x["start"])
            return segments
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def merge_segments_into_summary(self, segments: list[dict], nlp_response: SummaryNLPExtractedData, request_data: dict) -> dict:
        """Sends all segments to the model to produce one merged cohesive summary."""
        try:
            payload_segments = [
                {k: v for k, v in seg.items() if k not in ("start", "end")}
                for seg in segments
            ]

            merge_payload: dict = {"segments": payload_segments}
            self.append_user_instructions(merge_payload, nlp_response, request_data)

            request_data["instructions"] = request_data["secondary_instructions"]
            request_data["user_query"]   = json.dumps(merge_payload, indent=4)

            response = await self.generate_chat_summary(request_data)
            return response

        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    def append_user_instructions(self, data: dict, nlp_response: SummaryNLPExtractedData, request_data: dict):
        try:
            if nlp_response.summary_type:
                data["summary_type"] = f"Generate a '{nlp_response.summary_type}' style summary."
            if nlp_response.tone:
                data["tone"]         = f"Use a '{nlp_response.tone}' tone throughout."
            if nlp_response.context:
                data["context"]      = nlp_response.context
            if nlp_response.topic_name:
                data["topic_name"]   = f"Keep discussions about '{nlp_response.topic_name}' prominent."
            if nlp_response.buddy_name:
                data["buddy_name"]   = f"Highlight contributions by '{nlp_response.buddy_name}'."
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def collect_user_conversations(self, nlp_response: SummaryNLPExtractedData, request_data):
        try:
            user_chats: list[StreamsUserChatData] = TypeAdapter(list[StreamsUserChatData]).validate_python(
                await streams_db_handler.get_streams_user_chat(
                    request_data,
                    start_date=nlp_response.start_date,
                    end_date=nlp_response.end_date,
                    message_count=nlp_response.message_count,
                    unread_messages=nlp_response.unread_messages,
                )
            )

            logger.info(f"for {request_data.get('sid')}, collected {len(user_chats)} no. of conversations, agentid : {request_data.get('agentid')}")

            if not user_chats:
                return None

            if nlp_response.message_count or nlp_response.unread_messages:
                user_chats.reverse()

            conversations: list[dict] = []
            chat: StreamsUserChatData

            for chat in user_chats:
                conversations.append({
                    "timestamp": chat.messagetime.strftime("%Y-%m-%d %H:%M:%S"),
                    "user": f"{chat.firstname} {chat.lastname}".strip() or utils.extract_user_name(chat.username),
                    "message": chat.message
                })

            conversations = filter_conversations(conversations)

            if not conversations:
                logger.info(f"No valid conversations remaining after filtering noise, agentid : {request_data.get('agentid')}")
                return None

            logger.info(f"length of conversations :: {len(conversations)}, agentid : {request_data.get('agentid')}")

            chat_data = {"conversations": conversations}
            self.append_user_instructions(chat_data, nlp_response, request_data)
            return chat_data

        except Exception as e:
            logger.error(f"Error ========  :: {e}, agentid :: {request_data.get('agentid')}")
            raise e

    async def generate_chat_summary(self, request_data):
        try:

            total_content = str(json.dumps(request_data['user_query'], indent=4) + request_data["instructions"])

            await validate_token_limits(total_content, request_data)

            response = await model_router.generate(request_data)

            chat_summary = response.text or ""

            logger.info(f" Summary :: {chat_summary} length of summary :: {len(chat_summary)}, agentid :: {str(request_data.get('agentid'))}")

            return {"status": "200", "msg": "Success", "message": chat_summary}

        except Exception as e:
            logger.error(f"Error ========  :: {e}, agentid :: {request_data.get('agentid')}")
            raise e


chat_summary_handler = ChatSummaryHandler()
