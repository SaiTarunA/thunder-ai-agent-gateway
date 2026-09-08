"""The single entry point every feature handler uses to talk to an AI model.

Resolves which provider to call and records billing for every call, so a feature
handler never imports `openai`, a provider SDK response type, or `app.ai.providers.*`
directly — it just calls `model_router.generate(request_data)` and gets back an
`AIResponse`. Adding a second provider means constructing `ModelRouter(some_other_provider)`
here (or picking one per-request via `app.ai.registry`); it never touches `app/features/`.
"""

import logging
from typing import Optional

from app.ai.providers.base import AIProvider
from app.ai.providers.openai.client import openai_provider
from app.ai.response import AIResponse
from app.billing.service import billing_service

logger = logging.getLogger(__name__)


class ModelRouter:

    def __init__(self, provider: Optional[AIProvider] = None):
        self._provider = provider or openai_provider

    async def generate(self, request_data: dict) -> AIResponse:
        try:
            response = await self._provider.generate(
                model=request_data["model_info"]["model_name"],
                instructions=request_data["instructions"],
                input=request_data["user_query"],
                max_output_tokens=request_data["max_output_tokens"],
                temperature=request_data["temperature"],
                tools=request_data.get("tools"),
                tool_choice=request_data.get("tool_choice"),
                parallel_tool_calls=request_data.get("parallel_tool_calls"),
                previous_response_id=request_data.get("previous_response_id"),
                conversation_id=request_data.get("conversation_id"),
                agentid=request_data.get("agentid"),
            )
        except Exception:
            logger.exception(
                "Error, agentid=%s", request_data.get("agentid")
            )
            raise

        await billing_service.record_usage(response, request_data)
        return response

model_router = ModelRouter()
