import logging

from app.ai import ai_constants
from app.ai.config_builder import ai_config_builder
from app.ai.prompts.search_answer import SEARCH_ANSWER_SYNTHESIZE_USER_QUERY_TEMPLATE
from app.ai.router import model_router
from app.ai.tokenizer import validate_token_limits
from app.ai.tools import parse_tool_call_arguments, pydantic_model_to_openai_tool
from app.features.search.domain.enums import ContentType, RetrievalMethodType
from app.features.search.domain.models import SearchContextRequest
from app.features.search.module import search_module
from app.features.search_answer.schemas import (
    ANSWER_TOOL_SCHEMAS,
    CLASSIFY_TOOL_SCHEMAS,
    InsufficientContextArgs,
    ProvideAnswerArgs,
    SearchAnswerRequest,
    SearchAnswerResponse,
)

logger = logging.getLogger(__name__)


class SearchAnswerHandler:
    """Four-stage pipeline behind the Global Search AI-answer endpoint:
    classify (contextual?) -> hybrid retrieve -> zero-candidates check ->
    synthesize (answer-with-citations or decline). See the search feature's
    indexing-pipeline design notes for why stage 3 is only a zero-candidates
    check for now, not a score-based confidence gate - that's an explicitly
    deferred decision, not an oversight.
    """

    def __init__(self):
        self._classify_tools = [
            pydantic_model_to_openai_tool(model, name=name, description=description)
            for name, (model, description) in CLASSIFY_TOOL_SCHEMAS.items()
        ]
        self._answer_tools = [
            pydantic_model_to_openai_tool(model, name=name, description=description)
            for name, (model, description) in ANSWER_TOOL_SCHEMAS.items()
        ]

    async def handle(self, request: SearchAnswerRequest) -> SearchAnswerResponse:
        request_data = request.model_dump()
        agentid = request_data.get("agentid")

        try:
            is_contextual = await self._classify(request_data)

            if not is_contextual:
                logger.info("search_answer :: not contextual, agentid=%s", agentid)
                return SearchAnswerResponse(has_answer=False, reason="not_contextual")

            candidates = await self._retrieve(request)

            if not candidates:
                logger.info("search_answer :: no candidates, agentid=%s", agentid)
                return SearchAnswerResponse(has_answer=False, reason="no_results")

            return await self._synthesize(request_data, candidates)

        except Exception:
            logger.exception("search_answer :: failed, agentid=%s", agentid)
            return SearchAnswerResponse(has_answer=False, reason="error")

    async def _classify(self, request_data: dict) -> bool:
        config = await ai_config_builder.prepare_search_answer_classify_config()

        classify_data = {
            **config,
            **request_data,
            "user_query": request_data["query"],
            "tools": self._classify_tools,
        }

        total_content = f"{classify_data.get('user_query', '')}\n{classify_data.get('instructions', '')}"
        await validate_token_limits(total_content, classify_data)
        response = await model_router.generate(classify_data)

        tool_calls = response.tool_calls or []

        if not tool_calls:
            logger.warning(
                "search_answer :: classify returned no tool call, defaulting to "
                "contextual, agentid=%s",
                request_data.get("agentid"),
            )
            return True

        return tool_calls[0].name == ai_constants.FUNCTION_CONTEXTUAL_QUERY

    async def _retrieve(self, request: SearchAnswerRequest) -> list[dict]:
        search_request = SearchContextRequest(
            **request.model_dump(),
            content_types=[ContentType.MESSAGES],
            retrieval_methods=[
                RetrievalMethodType.LEXICAL,
                RetrievalMethodType.SEMANTIC,
            ],
        )

        search_response = await search_module.search(search_request)

        message_result = search_response.get("results", {}).get(
            ContentType.MESSAGES.value, {}
        )

        return [
            candidate
            for candidate in message_result.get("candidates", [])
            if candidate.get("data")
        ]

    async def _synthesize(
        self,
        request_data: dict,
        candidates: list[dict],
    ) -> SearchAnswerResponse:
        config = await ai_config_builder.prepare_search_answer_synthesize_config()

        results_block = "\n".join(
            f"[{candidate['data']['message_id']}] {candidate['data']['text']}"
            for candidate in candidates
        )

        user_query = SEARCH_ANSWER_SYNTHESIZE_USER_QUERY_TEMPLATE.format(
            query=request_data["query"],
            results_block=results_block,
        )

        synthesize_data = {
            **config,
            **request_data,
            "user_query": user_query,
            "tools": self._answer_tools,
        }

        total_content = f"{user_query}\n{synthesize_data.get('instructions', '')}"
        await validate_token_limits(total_content, synthesize_data)
        response = await model_router.generate(synthesize_data)

        tool_calls = response.tool_calls or []

        if not tool_calls:
            logger.warning(
                "search_answer :: synthesize returned no tool call, agentid=%s",
                request_data.get("agentid"),
            )
            return SearchAnswerResponse(has_answer=False, reason="insufficient_context")

        tool_call = tool_calls[0]

        if tool_call.name == ai_constants.FUNCTION_INSUFFICIENT_SEARCH_CONTEXT:
            parse_tool_call_arguments(InsufficientContextArgs, tool_call.arguments)
            return SearchAnswerResponse(has_answer=False, reason="insufficient_context")

        args = parse_tool_call_arguments(ProvideAnswerArgs, tool_call.arguments)

        return SearchAnswerResponse(
            has_answer=True,
            answer=args.answer,
            citations=args.citations,
        )


search_answer_handler = SearchAnswerHandler()
