import logging
import os
import time
from typing import Any, Dict, Optional
from fastapi import status

from dotenv import load_dotenv
from openai import AsyncOpenAI

from app.providers.base import BaseAIProvider
from app.providers.open_ai.billing import OpenAIBilling

load_dotenv()

logger = logging.getLogger(__name__)


class OpenAIGateway(BaseAIProvider):
    _instance = None

    generic_client = AsyncOpenAI(
        api_key=os.environ["OPEN_AI_API_KEY"],
        timeout=30,
    )

    chatbot_client = AsyncOpenAI(
        api_key=os.environ["OPEN_AI_ASSISTANT_API_KEY"],
        timeout=30,
    )

    billing = OpenAIBilling()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OpenAIGateway, cls).__new__(cls)
        return cls._instance

    async def process_responses_api_call(
        self,
        request_data: Dict[str, Any],
        is_chatbot: bool = False,
    ):
        try:
            start = time.perf_counter()

            logger.info(
                "Preparing to call OpenAI Response API, agentid=%s",
                request_data.get("agentid"),
            )

            client = (
                self.chatbot_client
                if is_chatbot
                else self.generic_client
            )

            response = await client.responses.create(
                model=request_data.get("model_info").get("model_name"),
                input=request_data.get("user_query"),
                instructions=request_data.get("instructions"),
                tools=request_data.get("tools"),
                tool_choice=request_data.get("tool_choice"),
                max_output_tokens=int(request_data.get("max_output_tokens")),
                temperature=float(request_data.get("temperature")),
                # previous_response_id = request_data.get("previous_response_id") or None,
                conversation=request_data.get("conversation_id") or None,
            )

            duration = time.perf_counter() - start
            request_data["openai_api_call_duration"] = f"{duration:.2f}"

            logger.info(
                "OpenAI Response API completed in %.2f sec, agentid=%s",
                duration,
                request_data.get("agentid"),
            )

            await self.billing.generate_billing_for_tokens(
                response,
                request_data
            )

            return response

        except Exception as e:
            logger.exception(
                "[process_responses_api_call] Error, agentid=%s",
                request_data.get("agentid"),
            )
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def create_conversation_id(
        self,
        request_data: Dict[str, Any],
    ) -> Optional[Any]:
        try:
            logger.info(
                "Preparing to call OpenAI Conversation API, agentid=%s",
                request_data.get("agentid"),
            )

            start = time.perf_counter()

            conversation = await self.generic_client.conversations.create(
                metadata={"topic": request_data.get("agentid")}
            )

            duration = time.perf_counter() - start

            logger.info(
                "Conversation created in %.2f sec, agentid=%s",
                duration,
                request_data.get("agentid"),
            )

            return conversation

        except Exception:
            logger.exception(
                "[create_conversation_id] Error, agentid=%s",
                request_data.get("agentid"),
            )
            return None


openai_client = OpenAIGateway()
