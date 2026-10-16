
import logging
from datetime import datetime, timezone
from typing import Optional

from app.ai import ai_constants
from app.ai import registry
from app.ai.ai_constants import SummaryCategory
from app.ai.prompts import chat_summary, gdrive_search, intent_detection, reply_to_thread, upgrade_user_chat
from app.core.utils import utils
from app.features.gdrive_search.config import gdrive_config
from app.features.intent_detection.schemas import WEB_SEARCH_TOOL

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

    async def prepare_intent_detection_config(self, request_data: dict, drive_connected: Optional[bool] = None):
        """``drive_connected`` is None unless Google Drive search is part of this
        routing call; only then are the Drive context line and routing rules added."""
        try:
            cfg = intent_detection.INTENT_DETECTION_CONSTANTS

            intent_detection_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                "tool_choice": cfg.get("tool_choice"),
                "parallel_tool_calls": cfg.get("parallel_tool_calls"),
                "operation_type": ai_constants.OPERATION_INTENT_DETECTION,
            }
            tz_str = request_data.get("timezone") or "UTC"
            user_tz = utils.get_zoneinfo(tz_str)

            now_dt = datetime.now(user_tz)
            now_datetime = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            thread_context = ""
            if request_data.get("smsgid"):
                thread_context = intent_detection.THREAD_CONTEXT_INSTRUCTION_TEMPLATE.format(
                    smsgid=request_data.get("smsgid"),
                    function_generate_summary=ai_constants.FUNCTION_GENERATE_SUMMARY,
                    thread_summary_category=ai_constants.SummaryCategory.THREAD_SUMMARY,
                    generate_reply_category=ai_constants.SummaryCategory.GENERATE_REPLY,
                    chat_summary_category=ai_constants.SummaryCategory.CHAT_SUMMARY,
                )

            drive_context = ""
            if drive_connected is not None:
                drive_context = gdrive_search.DRIVE_CONTEXT_LINE_TEMPLATE.format(
                    connected=str(drive_connected).lower()
                )
                intent_detection_info["instructions"] += gdrive_search.DRIVE_SEARCH_ROUTING_INSTRUCTIONS

            # Prepend current user timezone, datetime, and thread context
            intent_detection_info["instructions"] = intent_detection.INTENT_DETECTION_CONTEXT_HEADER_TEMPLATE.format(
                tz_str=tz_str,
                now_datetime=now_datetime,
                thread_context=thread_context + drive_context,
                instructions=intent_detection_info["instructions"],
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
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                "secondary_instructions": cfg.get("secondary_instructions"),
                "batch_notes_instructions": cfg.get("batch_notes_instructions"),
                "collapse_notes_instructions": cfg.get("collapse_notes_instructions"),
                "period_layout_instruction": cfg.get("period_layout_instruction"),
                "unavailable_periods_instruction": cfg.get("unavailable_periods_instruction"),
                "unavailable_period_text": cfg.get("unavailable_period_text"),
                "batching": cfg.get("batching"),
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

    async def prepare_process_thread_config(
        self, request_data: dict, category: SummaryCategory | str | None = None
    ):
        try:
            tools = []
            if category == SummaryCategory.THREAD_SUMMARY:
                cfg = chat_summary.CHAT_SUMMARY_CONSTANTS
                process_thread_info = {
                    **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                    "secondary_instructions": cfg.get("secondary_instructions"),
                    "batch_notes_instructions": cfg.get("batch_notes_instructions"),
                    "collapse_notes_instructions": cfg.get("collapse_notes_instructions"),
                    "period_layout_instruction": cfg.get("period_layout_instruction"),
                    "unavailable_periods_instruction": cfg.get("unavailable_periods_instruction"),
                    "unavailable_period_text": cfg.get("unavailable_period_text"),
                    "batching": cfg.get("batching"),
                    "tools": tools,
                    "tool_choice": cfg.get("tool_choice"),
                    "parallel_tool_calls": cfg.get("parallel_tool_calls", False),
                    "operation_type": ai_constants.OPERATION_PROCESS_THREAD,
                }
            elif category == SummaryCategory.GENERATE_REPLY:
                cfg = reply_to_thread.REPLY_TO_THREAD_CONSTANTS
                tools = cfg.get("tools", [WEB_SEARCH_TOOL])
                process_thread_info = {
                    **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                    "tools": tools,
                    "tool_choice": cfg.get("tool_choice"),
                    "parallel_tool_calls": cfg.get("parallel_tool_calls", False),
                    "operation_type": ai_constants.OPERATION_PROCESS_THREAD,
                }
            else:
                raise ValueError(f"Invalid category :: {category}")

            tz_str = request_data.get("timezone") or "UTC"
            user_tz = utils.get_zoneinfo(tz_str)
            now_dt = datetime.now(user_tz)
            now_datetime = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            process_thread_info["instructions"] = intent_detection.USER_DATETIME_CONTEXT_HEADER_TEMPLATE.format(
                tz_str=tz_str,
                now_datetime=now_datetime,
                instructions=process_thread_info["instructions"],
            )

            logger.info(f"process_thread_info :: \n{process_thread_info}")
            return process_thread_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None

    async def prepare_general_query_config(self, request_data: dict | None = None):
        try:
            request_data = request_data or {}
            cfg = intent_detection.GENERAL_QUERY_CONSTANTS

            general_query_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_4_1_MINI),
                "tools": cfg.get("tools", [{"type": "web_search"}]),
                "tool_choice": cfg.get("tool_choice"),
                "parallel_tool_calls": cfg.get("parallel_tool_calls"),
                "operation_type": ai_constants.OPERATION_GENERAL_QUERY,
            }

            tz_str = request_data.get("timezone") or "UTC"
            user_tz = utils.get_zoneinfo(tz_str)

            now_dt = datetime.now(user_tz)
            now_datetime = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            general_query_info["instructions"] = intent_detection.USER_DATETIME_CONTEXT_HEADER_TEMPLATE.format(
                tz_str=tz_str,
                now_datetime=now_datetime,
                instructions=general_query_info["instructions"],
            )

            logger.info(f"general_query_info :: \n{general_query_info}")
            return general_query_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None

    async def prepare_gdrive_search_config(self, stage: str):
        """Model settings for one Google Drive search stage: "understand",
        "match_filename", "rerank" or "answer". The caller adds the reply tool
        and, for rerank, the per-call output cap."""
        try:
            cfg = {
                "understand": gdrive_search.GDRIVE_UNDERSTAND_CONSTANTS,
                "match_filename": gdrive_search.GDRIVE_MATCH_FILENAME_CONSTANTS,
                "rerank": gdrive_search.GDRIVE_RERANK_CONSTANTS,
                "answer": gdrive_search.GDRIVE_ANSWER_CONSTANTS,
            }[stage]

            gdrive_search_info = {
                **self._prepare_default_settings({}, cfg, registry.MODEL_GPT_6_LUNA),
                "operation_type": ai_constants.OPERATION_DRIVE_SEARCH,
                "reasoning_effort": gdrive_config.model_reasoning_effort,
                "timeout": gdrive_config.model_timeout_seconds,
                "service_tier": gdrive_config.model_service_tier,
                # Drive content is not kept on the provider's side after the call.
                "store": False,
            }

            logger.debug(f"gdrive_search_info :: stage={stage}")
            return gdrive_search_info

        except Exception as e:
            logger.error(f"Error :: {e}")
            return None


ai_config_builder = AIConfigBuilder()
