from fastapi import status, HTTPException
import logging
import json
import tiktoken
from app.core import constants

logger = logging.getLogger(__name__)

class Utils:

    _instance = None


    def extract_user_name(self, user):
        try:
            name = user
            if "[V]" in name:
                name = name.split("[V]")[-1]
            if "_" in name:
                name = name.split("_")[-1]
            return name
        except Exception as e:
            logger.error(f"Error :: {e}")
            return user
    
    async def validate_token_limits(self, content, request_data):
        try:
            tokenizer = tiktoken.encoding_for_model(request_data["model_info"].get("model_name"))
            no_of_tokens = len(tokenizer.encode(json.dumps(content, indent=4)))
            logger.info(f"Token Count :: {no_of_tokens}, agentid :: {request_data.get('agentid')}")

            if no_of_tokens < constants.DEFAULT_OPENAI_MIN_OUTPUT_TOKENS or no_of_tokens > request_data["model_info"].get("max_input_tokens"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Insufficient Text found" if no_of_tokens < constants.DEFAULT_OPENAI_MIN_OUTPUT_TOKENS else "Too much Text found"
                )
        except Exception as e:
            logger.error(f"Error :: {e}, agentid :: {request_data.get('agentid')}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )

utils = Utils()
