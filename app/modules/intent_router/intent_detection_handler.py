from ast import arguments
from fastapi import status
import json
import logging

from app.providers.open_ai.client import openai_client
from app.providers.open_ai import constants as openai_constants
from app.providers.open_ai.ai_configurator import ai_system_configurator
from app.modules.intent_router.schemas import LunaRequest
from app.modules.ai_summary.schemas import SummaryNLPExtractedData
from app.modules.ai_summary.chat_summary_handler import chat_summary_handler
from app.modules.general.general_chat_handler import general_chat_handler

from app.core.utils import utils

logger = logging.getLogger(__name__)

class IntentDetectionHandler():

    async def detect_intent(self, data: LunaRequest):
        try:

            request_data = data.model_dump()

            logger.info(f"request_data :: {request_data}, agentid :: {data.agentid}")
            
            intent_detection_data = {**request_data, **await ai_system_configurator.prepare_intent_detection_config()}

            total_content = str(f"{intent_detection_data['user_query']}\n{intent_detection_data['instructions']}")
            await utils.validate_token_limits(total_content, intent_detection_data)

            intent_detection_response = await openai_client.process_responses_api_call(intent_detection_data)

            if isinstance(intent_detection_response, dict) and "error" in intent_detection_response:
                raise Exception(intent_detection_response["error"])

            loop_count = 0
            function_response_data = {}
            for output in intent_detection_response.output:
                loop_count += 1
                logger.info(f"loop {loop_count} ====> output : {output}, agentid :: {data.agentid}")

                if output.type == "function_call":
                    function_name = output.name
                    arguments = json.loads(output.arguments) if getattr(output, "arguments", None) else {}
                    logger.info(f"Function name :: {function_name}, arguments :: {arguments}, agentid :: {data.agentid}")
                    function_response_data = await self.handle_function_calls(function_name, arguments, request_data)

                elif output.type == "message":
                    function_response_data["response_text"] = output.content[0].text
                    logger.info(f"response message: {function_response_data['response_text']}, agentid :: {data.agentid}")

                elif output.type == "web_search_call":
                    logger.info(f"web_search_call output item: {output}, agentid :: {data.agentid}")

                else:
                    logger.info(f"unexpected output type: {output.type}, agentid :: {data.agentid}")
                    function_response_data["response_text"] = "Please try again, i am unable to understand your request."
                    raise Exception(f"Unexpected output type: {output.type}", function_response_data["response_text"])

            logger.info(f"function call response data: {function_response_data}, agentid :: {data.agentid}")

            function_response_data["request_params"] = {**request_data}

            return function_response_data
            
        except Exception as e:
            logger.exception(f"Error : {e}, agentid :: {data.agentid}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def handle_function_calls(self, function_name, arguments, request_data):
        try:

            tool_call_response = {}

            match function_name:

                case openai_constants.FUNCTION_UPGRADE_USER_CHAT:
                    tool_call_response = await general_chat_handler.process_upgrade_user_chat_request(request_data)
                    tool_call_response["type"] = "chat_formatter"

                case openai_constants.FUNCTION_REPLY_TO_THREAD:
                    tool_call_response = await general_chat_handler.process_reply_to_thread_request(request_data)
                    tool_call_response["type"] = "generative_reply"
                
                case openai_constants.FUNCTION_GENERATE_SUMMARY:
                    nlp_response = SummaryNLPExtractedData(**arguments)
                    tool_call_response = await chat_summary_handler.process_chat_summary_request(nlp_response, request_data)
                    tool_call_response["type"] = "chat_summary"

                case openai_constants.FUNCTION_GENERAL_QUERY:
                    tool_call_response = arguments
                    tool_call_response["type"] = "general_reply"
                
                case openai_constants.FUNCTION_CLARIFY_USER_QUERY:
                    tool_call_response = arguments
                    tool_call_response["type"] = "clarification_needed"
                
                case openai_constants.FUNCTION_DOCUMENT_INTELLIGENCE:
                    tool_call_response = arguments
                    tool_call_response["type"] = "document_intellegence"

                case openai_constants.FUNCTION_OUT_OF_SCOPE:
                    tool_call_response = arguments
                    tool_call_response["type"] = "out_of_scope"

                case _:
                    logger.warning(f"an unknown function name is called :: {function_name}, arguments :: {arguments}, agentid :: {request_data.get('agentid')}")
                    raise Exception(f"Unknown function name :: {function_name}")

            return tool_call_response
            
        except Exception as e:
            logger.exception(f"Error : {e}, agentid :: {request_data.get('agentid')}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

intent_detection_handler = IntentDetectionHandler()
