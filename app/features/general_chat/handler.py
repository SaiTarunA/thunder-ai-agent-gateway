import logging

from fastapi import status
from pydantic import TypeAdapter

from app.ai.config_builder import ai_config_builder
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.core.message_cleaner import filter_conversations
from app.core.utils import utils
from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.features.chat_summary.schemas import StreamsUserChatData

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


    async def process_reply_to_thread_request(self, request_data: dict):
        try:

            reply_to_thread_data = {**request_data, **await ai_config_builder.prepare_reply_to_thread_config()}

            conversations = await self.collect_thread_messages(request_data)
            reply_to_thread_data["user_query"] = reply_to_thread_data["user_query"] + f"\nThe Following are the conversations that took place in the thread which helps you to answer/reply to the user query :: {conversations}"

            total_content = str(f"{reply_to_thread_data['user_query']}\n{reply_to_thread_data['instructions']}")
            await validate_token_limits(total_content, reply_to_thread_data)

            response = await model_router.generate(reply_to_thread_data)

            reply_to_thread_msg = response.text or ""

            logger.info(f"reply to thread message :: {reply_to_thread_msg} and it's length :: {len(reply_to_thread_msg)}, agentid :: {str(request_data.get('agentid'))}")

            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "message" : reply_to_thread_msg
            }
        except Exception as e:
            logger.exception(f"Error : {e}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def collect_thread_messages(self, request_data):
        try:

            raw_parent_messages = await streams_db_handler.get_streams_parent_messages(request_data)
            raw_thread_messages = await streams_db_handler.get_streams_thread_messages(request_data)

            parent_messages: StreamsUserChatData = TypeAdapter(StreamsUserChatData).validate_python(raw_parent_messages)
            thread_messages: list[StreamsUserChatData] = TypeAdapter(list[StreamsUserChatData]).validate_python(raw_thread_messages)

            conversations: list[dict] = []

            if parent_messages:
                conversations.append({
                    "timestamp": parent_messages.messagetime.strftime("%Y-%m-%d %H:%M:%S"),
                    "user": f"{parent_messages.firstname} {parent_messages.lastname}".strip() or utils.extract_user_name(parent_messages.username),
                    "message": parent_messages.message
                })

            for chat in thread_messages:
                conversations.append({
                    "timestamp": chat.messagetime.strftime("%Y-%m-%d %H:%M:%S"),
                    "user": f"{chat.firstname} {chat.lastname}".strip() or utils.extract_user_name(chat.username),
                    "message": chat.message
                })

            conversations = filter_conversations(conversations)

            return conversations

        except Exception as e:
            logger.exception(f"Error : {e}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

general_chat_handler = GeneralChatHandler()
