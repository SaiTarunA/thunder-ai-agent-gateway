import logging
from typing import Any, Optional
from pydantic import TypeAdapter
from fastapi import status

from app.ai import ai_constants
from app.ai.config_builder import ai_config_builder
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.ai.tools import parse_tool_call_arguments, pydantic_model_to_openai_tool
from app.features.chat_summary.handler import chat_summary_handler
from app.features.general_chat.handler import general_chat_handler
from app.features.intent_detection.schemas import INTENT_TOOL_SCHEMAS, WEB_SEARCH_TOOL, LunaRequest
from app.core.utils import utils
from app.features.search.module import search_module
from app.features.search.domain.models import SearchContextRequest

logger = logging.getLogger(__name__)

class IntentDetectionHandler():

    def __init__(self):
        self._tool_definitions: list[dict] = self._build_tool_definitions()

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

            # Direct function call bypass when intent is already known from predefined actions
            reqtype = data.reqtype
            if reqtype:
                schema_entry = INTENT_TOOL_SCHEMAS.get(reqtype)
                if schema_entry:
                    model_cls, _ = schema_entry
                    raw_args = data.params if isinstance(data.params, dict) else {}
                    validated_args = model_cls.model_validate(raw_args)
                    logger.info(
                        f"Direct invocation bypassing LLM intent detection :: function={reqtype}, args={validated_args}, agentid={data.agentid}"
                    )
                    function_response_data = await self.handle_function_calls(
                        reqtype, validated_args, request_data
                    )
                    function_response_data["request_params"] = request_data
                    return function_response_data
                else:
                    logger.warning(
                        f"Unknown direct reqtype '{reqtype}', falling back to intent detection, agentid :: {data.agentid}"
                    )

            intent_config = await ai_config_builder.prepare_intent_detection_config(request_data)
            intent_detection_data = {
                **request_data,
                **(intent_config or {}),
                "tools": self._tool_definitions,
            }

            total_content = f"{intent_detection_data.get('user_query', '')}\n{intent_detection_data.get('instructions', '')}"
            await validate_token_limits(total_content, intent_detection_data)

            response = await model_router.generate(intent_detection_data)

            tool_calls = response.tool_calls
            if not tool_calls:
                # tool_choice="required" means the model is expected to always call
                # exactly one function; a text-only response means something
                # unexpected happened upstream.
                logger.warning(f"No tool call returned, agentid :: {data.agentid}, text :: {response.text}")
                raise Exception("Expected exactly one function call, got none")

            if len(tool_calls) > 1:
                logger.warning(
                    f"Multiple tool calls returned ({len(tool_calls)}), using the first, agentid :: {data.agentid}"
                )

            tool_call = tool_calls[0]
            tool_name = tool_call.name

            schema_entry = INTENT_TOOL_SCHEMAS.get(tool_name)
            if not schema_entry:
                logger.warning(f"an unknown function name is called :: {tool_name}, agentid :: {data.agentid}")
                raise Exception(f"Unknown function name :: {tool_name}")

            # Cross-check 1: Misrouted upgrade_user_chat for questions with grammatical errors
            if tool_name == ai_constants.FUNCTION_UPGRADE_USER_CHAT and utils.is_inquiry_or_question(data.user_query):
                logger.warning(
                    f"Misrouted intent corrected: model called '{tool_name}', but user_query was detected as an inquiry/question: '{data.user_query}'. Re-routing to general query, agentid :: {data.agentid}"
                )
                function_response_data = await general_chat_handler.process_general_query_request(request_data)
                function_response_data["type"] = "general_reply"
                function_response_data["request_params"] = request_data
                return function_response_data

            model_cls, _ = schema_entry
            # Every branch is validated against its Pydantic model here — previously
            # only generate_summary's arguments were parsed into one before use.
            validated_args = parse_tool_call_arguments(model_cls, tool_call.arguments)

            logger.info(f"Function name :: {tool_name}, arguments :: {validated_args}, agentid :: {data.agentid}")

            function_response_data = await self.handle_function_calls(
                tool_name, validated_args, request_data, raw_response_text=response.text
            )

            function_response_data["request_params"] = request_data

            return function_response_data

        except Exception as e:
            logger.exception(f"Error : {e}, agentid :: {data.agentid}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def handle_function_calls(
        self, function_name: str, args: Any, request_data: dict, raw_response_text: Optional[str] = None
    ):
        try:

            tool_call_response = {}

            match function_name:

                case ai_constants.FUNCTION_UPGRADE_USER_CHAT:
                    tool_call_response = await general_chat_handler.process_upgrade_user_chat_request(request_data)
                    tool_call_response["type"] = "chat_formatter"

                case ai_constants.FUNCTION_PROCESS_THREAD:
                    tool_call_response = await general_chat_handler.process_thread_request(args, request_data)
                    tool_call_response["type"] = "generative_reply"

                case ai_constants.FUNCTION_GENERATE_SUMMARY:
                    tool_call_response = await chat_summary_handler.process_chat_summary_request(args, request_data)
                    tool_call_response["type"] = "chat_summary"

                case ai_constants.FUNCTION_GENERAL_QUERY:
                    message = args.message
                    if utils.is_meta_response(message):
                        logger.warning(
                            f"Meta-response detected in general_query: '{message}', agentid :: {request_data.get('agentid')}"
                        )
                        # Attempt to recover substantive text from raw_response_text if available
                        if raw_response_text and not utils.is_meta_response(raw_response_text) and len(raw_response_text.strip()) > 15:
                            logger.info("Using substantive raw response.text to replace meta-response")
                            message = raw_response_text.strip()
                        else:
                            logger.info("Invoking general_chat_handler to generate substantive answer for general query")
                            gen_res = await general_chat_handler.process_general_query_request(request_data)
                            if gen_res.get("status") == status.HTTP_200_OK and gen_res.get("message"):
                                message = gen_res["message"]

                    tool_call_response = {
                        "message": message,
                        "type": "general_reply",
                    }

                case ai_constants.FUNCTION_CLARIFY_USER_QUERY:
                    tool_call_response = args.model_dump()
                    tool_call_response["type"] = "clarification_needed"

                case ai_constants.FUNCTION_DOCUMENT_INTELLIGENCE:
                    tool_call_response = args.model_dump()
                    tool_call_response["type"] = "document_intellegence"

                case ai_constants.FUNCTION_INTENT_SEARCH:
                    intent_search_request = args.model_dump()
                    user_tz = request_data.get("timezone") or request_data.get("time_zone") or "UTC"
                    for key in ("after", "before"):
                        val = intent_search_request.get(key)
                        if val is not None:
                            intent_search_request[key] = utils.convert_to_timestamp(val, source_tz=user_tz)
                    logger.info(
                        f"Converted intent_search date filters to timestamps :: after={intent_search_request.get('after')}, before={intent_search_request.get('before')}, timezone={user_tz}"
                    )
                    tool_call_response = await search_module.search(TypeAdapter(SearchContextRequest).validate_python({**intent_search_request, **request_data}))
                    tool_call_response["type"] = "search"

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
