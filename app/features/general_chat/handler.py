import asyncio
import logging
import shutil

from fastapi import status
from pydantic import TypeAdapter

from app.ai.config_builder import ai_config_builder
from app.ai.ai_constants import ThreadCategory
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.features.chat_summary.schemas import StreamsUserChatData
from app.features.intent_detection.schemas import ProcessThreadArgs, ThreadArgs
from app.workers.attachment_worker import (
    TEMP_ATTACHMENT_DIR,
    process_messages_attachments,
)

logger = logging.getLogger(__name__)


class GeneralChatHandler():

    async def process_upgrade_user_chat_request(self, request_data: dict):
        try:

            upgrade_user_chat_data = {**request_data, **await ai_config_builder.prepare_upgrade_user_chat_config()}

            total_content = str(f"{upgrade_user_chat_data['user_query']}\n{upgrade_user_chat_data['instructions']}")
            await validate_token_limits(total_content, upgrade_user_chat_data)

            response = await model_router.generate(upgrade_user_chat_data)

            upgraded_user_chat_msg = response.text or ""

            logger.info(f"upgraded user chat message :: {upgraded_user_chat_msg} and it's length :: {len(upgraded_user_chat_msg)}, agentid :: {str(request_data.get('agentid'))}")

            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message" : upgraded_user_chat_msg
            }
        except Exception as e:
            logger.exception(f"Error : {e}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def process_thread_request(self, args: ProcessThreadArgs, request_data: dict):
        """Unified handler for thread summarization and thread replies.

        Consolidates configuration preparation, concurrent message fetching, token
        validation, and model generation into a single DRY pipeline.
        """
        try:
            category = args.category
            thread_config = await ai_config_builder.prepare_process_thread_config(category)
            thread_data = {**request_data, **thread_config}

            conversations = await self.collect_thread_messages(request_data)

            action_hint = "summarize the thread" if category == ThreadCategory.SUMMARIZE else "answer/reply to the user query"
            
            thread_data["user_query"] = f"{thread_data['user_query']}\nThe Following are the conversations that took place in the thread which helps you to {action_hint} :: {conversations}"

            total_content = str(f"{thread_data['user_query']}\n{thread_data['instructions']}")
            await validate_token_limits(total_content, thread_data)

            response = await model_router.generate(thread_data)
            message = response.text or ""

            logger.info(f"thread {category} message :: {message} and its length :: {len(message)}, agentid :: {str(request_data.get('agentid'))}")

            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message": message,
            }
        except Exception as e:
            logger.exception(f"Error in process_thread_request: {e}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def collect_thread_messages(self, request_data: dict) -> list[dict]:
        try:
            raw_parent_messages, raw_thread_messages = await asyncio.gather(
                streams_db_handler.get_streams_parent_messages(request_data),
                streams_db_handler.get_streams_thread_messages(request_data),
            )

            all_chats: list[StreamsUserChatData] = []

            if raw_parent_messages:
                parent = TypeAdapter(StreamsUserChatData).validate_python(raw_parent_messages)
                all_chats.append(parent)

            if raw_thread_messages:
                thread_messages = TypeAdapter(list[StreamsUserChatData]).validate_python(raw_thread_messages)
                all_chats.extend(thread_messages)

            if not all_chats:
                return []

            sid = str(request_data.get("sid") or "").strip()
            agentid = str(request_data.get("agentid") or "").strip()
            folder_name = f"thread_{sid}{agentid}".strip()
            target_dir = (TEMP_ATTACHMENT_DIR / folder_name) if folder_name else (TEMP_ATTACHMENT_DIR / f"temp_{agentid}")

            try:
                conversations = await process_messages_attachments(
                    user_chats=all_chats,
                    request_data=request_data,
                    target_dir=target_dir,
                    max_concurrent=5,
                )
                return conversations
            finally:
                if target_dir.exists():
                    shutil.rmtree(target_dir, ignore_errors=True)

        except Exception as e:
            logger.exception(f"Error collecting thread messages: {e}")
            return []

    async def process_general_query_request(self, request_data: dict) -> dict:
        """Processes a general query directly with AI model and web search tool,
        ensuring a substantive answer is returned without meta-talk.
        """
        try:
            general_query_config = await ai_config_builder.prepare_general_query_config()
            general_query_data = {**request_data, **general_query_config}

            total_content = str(f"{general_query_data['user_query']}\n{general_query_data['instructions']}")
            await validate_token_limits(total_content, general_query_data)

            response = await model_router.generate(general_query_data)
            message = response.text or ""

            logger.info(
                f"general query message length :: {len(message)}, agentid :: {str(request_data.get('agentid'))}"
            )

            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message": message,
            }
        except Exception as e:
            logger.exception(f"Error in process_general_query_request: {e}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

general_chat_handler = GeneralChatHandler()
