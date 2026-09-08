"""Builds per-feature AI request configuration (model, instructions, temperature,
max tokens) from `app.ai.registry` (model catalog) and `app.ai.prompts.*` (per-feature
instruction text).

Moved out of the old `providers/open_ai/ai_configurator.py` — this logic decides
*what* to ask for, which is provider-agnostic, not *how* to call a particular
vendor's API, so it doesn't belong under a provider-specific package.

Note: the old version's `load_ai_system_settings` (DB + local-file cache) has moved to
`app.ai.settings_cache` — it wasn't actually called by any of the four `prepare_*`
methods below (they all read directly from the `app.ai.prompts.*` constants), so
nothing here depends on it.
"""

import logging
from datetime import datetime

from app.ai import constants as ai_constants
from app.ai import registry
from app.ai.prompts import chat_summary, intent_detection, reply_to_thread, upgrade_user_chat

logger = logging.getLogger(__name__)


class AIConfigBuilder:

    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _prepare_default_settings(self, operation_info: dict, default_constants: dict, model_info: dict):
        try:
            return {
                "model_info": operation_info.get("openai_model", model_info),
                "model_provider": operation_info.get("model_provider", registry.MODEL_OPENAI_PROVIDER),
                "instructions": operation_info.get("system_instructions", default_constants.get("instructions")),
                "max_output_tokens": int(
                    operation_info.get("max_response_output_tokens", default_constants.get("max_response_output_tokens"))
                ),
                "temperature": float(operation_info.get("temperature", default_constants.get("temperature"))),
            }
        except Exception as e:
            logger.error(f"Error :: {str(e)}")
            return None

    async def prepare_intent_detection_config(self):
        try:
            cfg = intent_detection.INTENT_DETECTION_CONSTANTS

            intent_detection_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                "tool_choice": cfg.get("tool_choice"),
                "parallel_tool_calls": cfg.get("parallel_tool_calls"),
                "operation_type": ai_constants.OPERATION_INTENT_DETECTION,
            }

            now_datetime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Prepend at the TOP so it sets authoritative context for all prompt rules below.
            intent_detection_info["instructions"] = (
                f"CURRENT CONTEXT:\n- current_datetime: {now_datetime}\n\n"
                + intent_detection_info["instructions"]
            )

            logger.info(f"intent_detection_info :: \n{intent_detection_info}")
            return intent_detection_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None

    async def prepare_chat_summary_config(self):
        try:
            cfg = chat_summary.CHAT_SUMMARY_CONSTANTS

            chat_summary_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4O_MINI),
                "secondary_instructions": cfg.get("secondary_instructions"),
                "operation_type": ai_constants.OPERATION_CHAT_SUMMARY,
            }

            logger.info(f"chat_summary_info :: \n{chat_summary_info}")
            return chat_summary_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None

    async def prepare_upgrade_user_chat_config(self):
        try:
            cfg = upgrade_user_chat.UPGRADE_USER_CHAT_CONSTANTS

            upgrade_user_chat_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                "operation_type": ai_constants.OPERATION_UPGRADE_USER_CHAT,
            }

            logger.info(f"upgrade_user_chat_info :: \n{upgrade_user_chat_info}")
            return upgrade_user_chat_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None

    async def prepare_reply_to_thread_config(self):
        try:
            cfg = reply_to_thread.REPLY_TO_THREAD_CONSTANTS

            reply_to_thread_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                "operation_type": ai_constants.OPERATION_REPLY_TO_THREAD,
            }

            logger.info(f"reply_to_thread_info :: \n{reply_to_thread_info}")
            return reply_to_thread_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None


ai_config_builder = AIConfigBuilder()
