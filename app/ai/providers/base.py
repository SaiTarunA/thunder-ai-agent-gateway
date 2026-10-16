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
from typing import Any, Optional, Union

from app.ai.response import AIResponse


class AIProvider(ABC):

    @abstractmethod
    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        # Text, or a list of input items for files and images alongside it.
        input: Union[str, list[dict]],
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

    def warm_up(self) -> None:
        """Load now what the first call would otherwise load, before the
        server takes requests. Nothing by default."""
