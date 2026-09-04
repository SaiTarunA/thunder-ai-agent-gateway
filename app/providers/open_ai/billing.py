import json
import logging

from app.db.mysql.repositories.streams_repo import streams_db_handler
from app.providers.open_ai import constants as openai_constants

logger = logging.getLogger(__name__)


class OpenAIBilling:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OpenAIBilling, cls).__new__(cls)
        return cls._instance

    async def generate_billing_for_tokens(
        self,
        response,
        request_data
    ):
        try:

            # Responses API usage
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            # Cached input tokens (if available)
            cached_input_tokens = 0
            if hasattr(response.usage, "input_tokens_details") and response.usage.input_tokens_details:
                cached_input_tokens = getattr(response.usage.input_tokens_details, "cached_tokens", 0)

            # Ensure cached tokens don't exceed total input tokens
            non_cached_input_tokens = input_tokens - cached_input_tokens

            model_info = request_data["model_info"]

            input_price = model_info["input_token_price"]
            output_price = model_info["output_token_price"]

            # Default to input_price if cached price is not found anywhere
            cached_input_price = input_price
            model_name = getattr(response, "model", "").lower()
            logger.info(f"model_name :: {model_name}")
            if model_info.get("cached_input_token_price", None):
                cached_input_price = model_info.get("cached_input_token_price")
            elif openai_constants.MODEL_GPT_4O_MINI.get("model_name") in model_name:
                cached_input_price = openai_constants.MODEL_GPT_4O_MINI.get("cached_input_token_price")
            elif openai_constants.MODEL_GPT_4_1_MINI.get("model_name") in model_name:
                cached_input_price = openai_constants.MODEL_GPT_4_1_MINI.get("cached_input_token_price")
            elif openai_constants.MODEL_GPT_4_1.get("model_name") in model_name:
                cached_input_price = openai_constants.MODEL_GPT_4_1.get("cached_input_token_price")


            # Billing
            billing_prompt = (non_cached_input_tokens / 1000000) * input_price

            billing_cached_prompt = (cached_input_tokens / 1000000) * cached_input_price

            billing_completion = (output_tokens / 1000000) * output_price

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
                "model_provider": openai_constants.MODEL_OPENAI_PROVIDER,
                "ai_operation_type": request_data["operation_type"],
                "bill_amount": bill_amount,
                "extra_data": extra_data,
            }

            db_res = await streams_db_handler.insert_openai_billing_data(
                billing_data,
                request_data.get("agentid"),
            )

            logger.info(
                f"db_res :: {db_res}, agentid :: {request_data.get('agentid')}"
            )

            return db_res

        except Exception as err:
            logger.exception(f"Error :: {err}")
            return None
