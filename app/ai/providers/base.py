"""The provider-neutral contract every AI backend implements.

This replaces the old `app/providers/base.py`, whose two methods
(`process_responses_api_call`, `create_conversation_id`) already mirrored OpenAI's
Responses API and Conversations API shape rather than describing what any provider
needs to do. Every verb here is generic on purpose — no method name or parameter
should name a specific vendor's API concept. A provider-specific capability (like
OpenAI's server-side Conversations API) is an extra method on that provider's own
adapter class, not part of this contract.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from app.ai.response import AIResponse


class AIProvider(ABC):

    @abstractmethod
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
        conversation_id: Optional[str] = None,
        is_chatbot: bool = False,
        agentid: Optional[str] = None,
        **provider_opts: Any,
    ) -> AIResponse:
        """Run one model call and return a normalized AIResponse."""
        raise NotImplementedError
