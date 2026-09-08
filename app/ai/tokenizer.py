"""Provider-aware token counting.

Only an OpenAI (tiktoken) counter exists today (`app.ai.providers.openai.tokenizer`)
— this module is the seam a second provider's counter plugs into (keyed off
`model_info["model_provider"]`), instead of every call site hardcoding `tiktoken`
directly the way `core/utils.py` used to.

Bug fix vs. the old `Utils.validate_token_limits`: that method's own `raise
HTTPException(422, ...)` for an out-of-range token count was being caught by its own
surrounding `except Exception`, so it was always re-raised as a 500 instead of the
intended 422. Fixed here by re-raising `HTTPException` before the generic handler.
"""

import json
import logging

from fastapi import HTTPException, status

from app.ai.providers.openai.tokenizer import count_tokens as _openai_count_tokens
from app.ai.registry import DEFAULT_OPENAI_MIN_OUTPUT_TOKENS

logger = logging.getLogger(__name__)


def _count_tokens_for(content, model_info: dict) -> int:
    provider = model_info.get("model_provider") or "OpenAI"
    if provider == "OpenAI":
        return _openai_count_tokens(json.dumps(content, indent=4), model_info.get("model_name"))
    raise ValueError(f"No token counter registered for provider: {provider}")


async def validate_token_limits(content, request_data: dict) -> None:
    try:
        model_info = request_data["model_info"]
        no_of_tokens = _count_tokens_for(content, model_info)
        logger.info(f"Token Count :: {no_of_tokens}, agentid :: {request_data.get('agentid')}")

        if no_of_tokens < DEFAULT_OPENAI_MIN_OUTPUT_TOKENS or no_of_tokens > model_info.get("max_input_tokens"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Insufficient Text found" if no_of_tokens < DEFAULT_OPENAI_MIN_OUTPUT_TOKENS else "Too much Text found",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
