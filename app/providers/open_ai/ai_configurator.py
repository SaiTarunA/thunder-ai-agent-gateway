import os
import json
import aiofiles

from pathlib import Path
from datetime import date

from app.providers.open_ai import constants as openai_constants
from app.db.mysql.repositories.opensips_repo import opensips_db_handler
from app.core import constants

import logging

logger = logging.getLogger(__name__)


class AISystemConfigurator:

    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super(AISystemConfigurator, cls).__new__(cls)
            cls._instance.cache_path = (
                Path().resolve() / constants.CACHE_FOLDER_PATH
            )
        return cls._instance

    def prepare_default_ai_system_settings(
        self, operation_info, default_constants, model_info
    ):
        try:
            ai_system_data = {
                "model_info": operation_info.get("openai_model", model_info),
                "model_provider": operation_info.get(
                    "model_provider", openai_constants.MODEL_OPENAI_PROVIDER
                ),
                "instructions": operation_info.get(
                    "system_instructions", default_constants.get("instructions")
                ),
                "max_output_tokens": int(
                    operation_info.get(
                        "max_response_output_tokens",
                        default_constants.get("max_response_output_tokens"),
                    )
                ),
                "temperature": float(
                    operation_info.get(
                        "temperature", default_constants.get("temperature")
                    )
                ),
            }
            return ai_system_data
        except Exception as e:
            logger.error(f"Error :: {str(e)}")
            return None

    async def load_ai_system_settings(
        self,
        file_name: str,
        feature_name: str,
    ):
        try:
            file_path = os.path.join(self.cache_path, file_name)

            if not os.path.exists(file_path):
                await opensips_db_handler.load_ai_system_settings_into_memory(
                    file_name,
                    feature_name,
                )

            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            return json.loads(content)

        except Exception as e:
            logger.error(f"Error :: {e}")
            return {}

    async def prepare_intent_detection_config(self):
        try:
            intent_detection_constants = (
                openai_constants.INTENT_DETECTION_FOR_STREAMS_THUNDER_CONSTANTS
            )

            intent_detection_info = {
                **self.prepare_default_ai_system_settings(
                    {},
                    intent_detection_constants,
                    openai_constants.MODEL_GPT_4_1_MINI,
                ),
                "tools": intent_detection_constants.get("tools"),
                "tool_choice": intent_detection_constants.get("tool_choice"),
                "operation_type": constants.OPERATION_INTENT_DETECTION,
            }

            today_date = date.today().strftime("%Y-%m-%d")
            intent_detection_info["instructions"] += (
                f"Note: today's date is {today_date},"
            )

            logger.info(
                f"intent_detection_info :: \n{intent_detection_info}"
            )
            return intent_detection_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None

    async def prepare_chat_summary_config(self):
        try:
            chat_summary_constants = (
                openai_constants.CHAT_SUMMARY_CONSTANTS
            )

            chat_summary_info = {
                **self.prepare_default_ai_system_settings(
                    {},
                    chat_summary_constants,
                    openai_constants.MODEL_GPT_4O_MINI,
                ),
                "secondary_instructions": (
                    chat_summary_constants.get("secondary_instructions")
                ),
                "operation_type": constants.OPERATION_CHAT_SUMMARY,
            }

            logger.info(
                f"chat_summary_info :: \n{chat_summary_info}"
            )
            return chat_summary_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None


    async def prepare_upgrade_user_chat_config(self):
        try:
            upgrade_user_chat_constants = (
                openai_constants.UPGRADE_USER_CHAT_CONSTANTS
            )

            upgrade_user_chat_info = {
                **self.prepare_default_ai_system_settings(
                    {},
                    upgrade_user_chat_constants,
                    openai_constants.MODEL_GPT_4_1_MINI,
                ),
                "operation_type": constants.OPERATION_UPGRADE_USER_CHAT,
            }

            logger.info(
                f"upgrade_user_chat_info :: \n{upgrade_user_chat_info}"
            )
            return upgrade_user_chat_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None


ai_system_configurator = AISystemConfigurator()