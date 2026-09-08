"""The only file in this codebase that imports the `openai` SDK directly.

Everything above this adapter — every feature handler, the billing service, the
model router — talks to `AIProvider`/`AIResponse` only (see `app.ai.providers.base`
and `app.ai.response`). Swapping or adding a provider means writing a sibling to
this file under `app/ai/providers/<name>/`, not touching anything in `app/features/`.

Behavior notes vs. the old `providers/open_ai/client.py`:
- The two `AsyncOpenAI` clients are now built lazily on first use instead of as class
  attributes evaluated at import time — a missing `OPEN_AI_API_KEY` now only fails the
  first request that needs it, not the import of the whole application.
- Response parsing (`response.output[].type == "message"/"function_call"`) happens
  once, here, instead of being hand-copied into three different feature handlers.
- `generate()` lets exceptions propagate instead of catching them and returning an
  error-shaped dict; every caller already wraps its own top-level logic in a
  try/except that produces the `{"status": 500, ...}` response, so this actually
  surfaces the real error instead of a secondary `AttributeError` from code that
  expected a normal response object.
"""

import logging
import os
import time
from typing import Any, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

from app.ai.providers.base import AIProvider
from app.ai.response import AIResponse, ToolCall, Usage
from app.ai.registry import MODEL_OPENAI_PROVIDER

load_dotenv()

logger = logging.getLogger(__name__)


class OpenAIProvider(AIProvider):

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.client = AsyncOpenAI(api_key=os.environ["OPEN_AI_API_KEY"], timeout=30)
        return cls._instance

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input: str,
        max_output_tokens: int,
        temperature: float,
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        parallel_tool_calls: Optional[bool] = None,
        previous_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agentid: Optional[str] = None,
        **_: Any,
    ) -> AIResponse:
        start = time.perf_counter()
        logger.info("Preparing to call OpenAI Response API, agentid=%s", agentid)

        create_kwargs: dict[str, Any] = dict(
            model=model,
            input=input,
            instructions=instructions,
            max_output_tokens=int(max_output_tokens),
            temperature=float(temperature)
        )

        if tools is not None:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            create_kwargs["parallel_tool_calls"] = parallel_tool_calls

        if conversation_id is not None:
            create_kwargs["conversation"] = conversation_id
        if previous_response_id is not None:
            create_kwargs["previous_response_id"] = previous_response_id

        response = await self.client.responses.create(**create_kwargs)

        duration = time.perf_counter() - start
        logger.info(
            "OpenAI Response API completed in %.2f sec, agentid=%s", duration, agentid
        )

        return self._to_ai_response(response)

    def _to_ai_response(self, response: Any) -> AIResponse:
        """Parse the Responses API's `output` array once, here."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        web_search_response: str

        for output in getattr(response, "output", []) or []:
            if output.type == "message":
                for block in output.content:
                    if block.type == "output_text":
                        text_parts.append(block.text)

            elif output.type == "web_search_call":
                logger.info(f"web_search_call output item: {output}")

            elif output.type == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=getattr(output, "call_id", None) or getattr(output, "id", None),
                        name=output.name,
                        arguments=output.arguments,
                    )
                )

        usage = None
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            cached = 0
            details = getattr(raw_usage, "input_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            usage = Usage(
                input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
                output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
                cached_input_tokens=cached,
            )

        return AIResponse(
            text="".join(text_parts) or None,
            tool_calls=tool_calls,
            usage=usage,
            model=getattr(response, "model", None),
            provider=MODEL_OPENAI_PROVIDER,
            raw=response,
        )

    async def create_conversation(self, *, topic: Optional[str] = None) -> Optional[Any]:
        """OpenAI-specific capability (the server-side Conversations API) — kept as an
        extra method on this adapter only, not part of the generic `AIProvider`
        contract, since no other provider exposes an equivalent primitive."""
        try:
            start = time.perf_counter()
            conversation = await self.generic_client.conversations.create(metadata={"topic": topic})
            logger.info("Conversation created in %.2f sec", time.perf_counter() - start)
            return conversation
        except Exception as e:
            logger.exception(f"Error :: {e}")
            return None


openai_provider = OpenAIProvider()
