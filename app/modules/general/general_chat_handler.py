from fastapi import status
from pydantic import BaseModel

import logging

from app.providers.open_ai.client import openai_client
from app.providers.open_ai.ai_configurator import ai_system_configurator
from app.core.utils import utils

logger = logging.getLogger(__name__)

class GeneralChatHandler():
    
    async def process_upgrade_user_chat_request(self, request_data: dict):
        try:

            upgrade_user_chat_data = {**request_data, **await ai_system_configurator.prepare_upgrade_user_chat_config()}

            total_content = str(f"{upgrade_user_chat_data['user_query']}\n{upgrade_user_chat_data['instructions']}")
            await utils.validate_token_limits(total_content, upgrade_user_chat_data)

            response = await openai_client.process_responses_api_call(upgrade_user_chat_data)

            upgraded_user_chat_msg = ""
            
            for output in response.output:

                if output.type == "message":
                    for c in output.content:
                        if c.type == "output_text":
                            upgraded_user_chat_msg += c.text
                            break
                if upgraded_user_chat_msg:
                    break    

            logger.info(f"upgraded user chat message :: {upgraded_user_chat_msg} and it's length :: {len(upgraded_user_chat_msg)}, agentid :: {str(request_data.get("agentid"))}")
            
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
    
    

general_chat_handler = GeneralChatHandler()