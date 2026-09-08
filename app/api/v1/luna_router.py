from fastapi import APIRouter, status
import logging

from app.features.intent_detection.schemas import LunaRequest
from app.features.intent_detection.handler import intent_detection_handler

logger = logging.getLogger(__name__)

luna_router = APIRouter()


@luna_router.post("/chat")
async def chat(request: LunaRequest):
    try:
        return await intent_detection_handler.detect_intent(request)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }
