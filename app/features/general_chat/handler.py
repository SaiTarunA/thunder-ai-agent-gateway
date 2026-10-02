import logging

from fastapi import status

from app.ai.config_builder import ai_config_builder
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits

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

    async def process_general_query_request(self, request_data: dict) -> dict:
        """Processes a general query directly with AI model and web search tool,
        ensuring a substantive answer is returned without meta-talk.
        """
        try:
            general_query_config = await ai_config_builder.prepare_general_query_config(request_data)
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
