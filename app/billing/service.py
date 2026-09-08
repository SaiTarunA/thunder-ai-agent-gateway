"""Provider-agnostic billing.

Moved out of `providers/open_ai/billing.py`. Consumes `AIResponse.usage` (the
normalized shape from `app.ai.response`) instead of reading a specific provider's raw
usage object directly, and tags each row with `response.provider` instead of a
hardcoded OpenAI constant — so a second provider's usage flows through the same
billing pipeline instead of needing a parallel one.

Simplification vs. the original: the old code fell back to pattern-matching
`response.model` against specific model-name constants when `cached_input_token_price`
was missing from `model_info`. Every model actually used by these AI operations already
carries that price in `app.ai.registry`, so that fallback chain was redundant — this
version falls back straight to `input_price`, same as the original's own last resort.
"""

import json
import logging

from app.ai.response import AIResponse
from app.billing.repository import billing_repository

logger = logging.getLogger(__name__)


class BillingService:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def record_usage(self, response: AIResponse, request_data: dict):
        try:
            if response.usage is None:
                logger.warning(
                    "No usage data on AIResponse, skipping billing, agentid=%s",
                    request_data.get("agentid"),
                )
                return None

            model_info = request_data["model_info"]

            input_price = model_info["input_token_price"]
            output_price = model_info["output_token_price"]
            cached_input_price = model_info.get("cached_input_token_price", input_price)

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cached_input_tokens = response.usage.cached_input_tokens
            non_cached_input_tokens = input_tokens - cached_input_tokens

            billing_prompt = (non_cached_input_tokens / 1_000_000) * input_price
            billing_cached_prompt = (cached_input_tokens / 1_000_000) * cached_input_price
            billing_completion = (output_tokens / 1_000_000) * output_price
            bill_amount = billing_prompt + billing_cached_prompt + billing_completion

            extra_data = json.dumps(
                {
                    "input_tokens": input_tokens,
                    "non_cached_input_tokens": non_cached_input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "input_token_price": input_price,
                    "cached_input_token_price": cached_input_price,
                    "output_token_price": output_price,
                    "billing_prompt": billing_prompt,
                    "billing_cached_prompt": billing_cached_prompt,
                    "billing_completion": billing_completion,
                    "bill_amount": bill_amount,
                    "max_input_tokens": model_info["max_input_tokens"],
                    "max_output_tokens": model_info["max_output_tokens"],
                },
                indent=2,
            )

            billing_data = {
                "siteid": request_data.get("siteid"),
                "agentid": request_data.get("agentid"),
                "sitename": request_data.get("sitename"),
                "model_id": model_info["model_id"],
                "modelname": model_info["model_name"],
                "model_provider": response.provider,
                "ai_operation_type": request_data["operation_type"],
                "bill_amount": bill_amount,
                "extra_data": extra_data,
            }

            db_res = await billing_repository.insert_billing_data(billing_data, request_data.get("agentid"))

            logger.info(f"db_res :: {db_res}, agentid :: {request_data.get('agentid')}")

            return db_res

        except Exception as err:
            logger.exception(f"Error :: {err}")
            return None


billing_service = BillingService()
