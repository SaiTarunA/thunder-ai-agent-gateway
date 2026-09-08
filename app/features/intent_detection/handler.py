import logging

from fastapi import status

from app.ai import constants as ai_constants
from app.ai.config_builder import ai_config_builder
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.ai.tools import parse_tool_call_arguments, pydantic_model_to_openai_tool
from app.features.chat_summary.handler import chat_summary_handler
from app.features.general_chat.handler import general_chat_handler
from app.features.intent_detection.schemas import INTENT_TOOL_SCHEMAS, WEB_SEARCH_TOOL, LunaRequest

logger = logging.getLogger(__name__)


class IntentDetectionHandler():

    def _build_tool_definitions(self) -> list[dict]:
        """Manual, LangChain-free equivalent of `bind_tools()`: derives each intent's
        OpenAI tool schema directly from its Pydantic model in `INTENT_TOOL_SCHEMAS`,
        instead of the old hand-written ~200-line JSON Schema block that had to be
        kept in sync with those models by hand."""
        tools: list[dict] = [
            pydantic_model_to_openai_tool(model, name=function_name, description=description)
            for function_name, (model, description) in INTENT_TOOL_SCHEMAS.items()
        ]
        tools.append(WEB_SEARCH_TOOL)
        return tools

    async def detect_intent(self, data: LunaRequest):
        try:

            request_data = data.model_dump()

            logger.info(f"request_data :: {request_data}, agentid :: {data.agentid}")

            intent_detection_data = {
                **request_data,
                **await ai_config_builder.prepare_intent_detection_config(),
                "tools": self._build_tool_definitions(),
            }

            total_content = str(f"{intent_detection_data['user_query']}\n{intent_detection_data['instructions']}")
            await validate_token_limits(total_content, intent_detection_data)

            response = await model_router.generate(intent_detection_data)

            if not response.tool_calls:
                # tool_choice="required" means the model is expected to always call
                # exactly one function; a text-only response means something
                # unexpected happened upstream.
                logger.warning(f"No tool call returned, agentid :: {data.agentid}, text :: {response.text}")
                raise Exception("Expected exactly one function call, got none")

            if len(response.tool_calls) > 1:
                logger.warning(
                    f"Multiple tool calls returned ({len(response.tool_calls)}), using the first, agentid :: {data.agentid}"
                )

            tool_call = response.tool_calls[0]

            if tool_call.name not in INTENT_TOOL_SCHEMAS:
                logger.warning(f"an unknown function name is called :: {tool_call.name}, agentid :: {data.agentid}")
                raise Exception(f"Unknown function name :: {tool_call.name}")

            model_cls, _ = INTENT_TOOL_SCHEMAS[tool_call.name]
            # Every branch is validated against its Pydantic model here — previously
            # only generate_summary's arguments were parsed into one before use.
            validated_args = parse_tool_call_arguments(model_cls, tool_call.arguments)

            logger.info(f"Function name :: {tool_call.name}, arguments :: {validated_args}, agentid :: {data.agentid}")

            function_response_data = await self.handle_function_calls(tool_call.name, validated_args, request_data)

            function_response_data["request_params"] = {**request_data}

            return function_response_data

        except Exception as e:
            logger.exception(f"Error : {e}, agentid :: {data.agentid}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def handle_function_calls(self, function_name, args, request_data):
        try:

            tool_call_response = {}

            match function_name:

                case ai_constants.FUNCTION_UPGRADE_USER_CHAT:
                    tool_call_response = await general_chat_handler.process_upgrade_user_chat_request(request_data)
                    tool_call_response["type"] = "chat_formatter"

                case ai_constants.FUNCTION_REPLY_TO_THREAD:
                    tool_call_response = await general_chat_handler.process_reply_to_thread_request(request_data)
                    tool_call_response["type"] = "generative_reply"

                case ai_constants.FUNCTION_GENERATE_SUMMARY:
                    tool_call_response = await chat_summary_handler.process_chat_summary_request(args, request_data)
                    tool_call_response["type"] = "chat_summary"

                case ai_constants.FUNCTION_GENERAL_QUERY:
                    tool_call_response = args.model_dump()
                    tool_call_response["type"] = "general_reply"

                case ai_constants.FUNCTION_CLARIFY_USER_QUERY:
                    tool_call_response = args.model_dump()
                    tool_call_response["type"] = "clarification_needed"

                case ai_constants.FUNCTION_DOCUMENT_INTELLIGENCE:
                    tool_call_response = args.model_dump()
                    tool_call_response["type"] = "document_intellegence"

                case ai_constants.FUNCTION_OUT_OF_SCOPE:
                    tool_call_response = args.model_dump()
                    tool_call_response["type"] = "out_of_scope"

                case _:
                    logger.warning(f"an unknown function name is called :: {function_name}, arguments :: {args}, agentid :: {request_data.get('agentid')}")
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
