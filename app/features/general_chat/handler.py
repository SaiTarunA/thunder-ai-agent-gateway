import asyncio
import logging
import shutil

from fastapi import status
from pydantic import TypeAdapter

from app.ai.config_builder import ai_config_builder
from app.ai.ai_constants import ThreadCategory
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.core.utils import utils
from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.features.chat_summary.schemas import StreamsUserChatData
from app.features.intent_detection.schemas import ProcessThreadArgs
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

            if category == ThreadCategory.SUMMARIZE:
                thread_data["user_query"] = (
                    f"{thread_data['user_query']}\n"
                    f"The Following are the conversations that took place in the thread which helps you to summarize the thread :: {conversations}"
                )
            else:
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

            if category == ThreadCategory.GENERATE_REPLY and utils.is_meta_response(message):
                logger.warning(
                    f"Meta-response detected in thread reply: '{message}' and Retrying generation with reinforced instruction..., agentid :: {request_data.get('agentid')}"
                )
                retry_thread_data = thread_data.copy()
                retry_thread_data["instructions"] = (
                    f"{retry_thread_data['instructions']}\n\n"
                    f"CRITICAL OVERRIDE: Your previous output was identified as a meta-announcement ('{message}'). "
                    f"Do NOT output search announcements, status updates, or phrases like 'Searching...', 'Let me look that up...', or 'Based on my search...'. "
                    f"You must directly return the finalized, substantive reply to be sent in the thread."
                )
                try:
                    retry_response = await model_router.generate(retry_thread_data)
                    retry_message = retry_response.text or ""
                    if retry_message and not utils.is_meta_response(retry_message):
                        logger.info(
                            f"Retry succeeded for thread reply, new message length: {len(retry_message)}, agentid :: {request_data.get('agentid')}"
                        )
                        message = retry_message
                    else:
                        logger.warning(
                            f"Retry still resulted in meta-response or empty: '{retry_message}', agentid :: {request_data.get('agentid')}"
                        )
                except Exception as retry_err:
                    logger.error(
                        f"Error during thread reply retry: {retry_err}, agentid :: {request_data.get('agentid')}"
                    )

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
