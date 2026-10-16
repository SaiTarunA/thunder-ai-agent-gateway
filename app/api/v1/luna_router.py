from fastapi import APIRouter, Request, status
import logging

from app.features.intent_detection.schemas import LunaRequest
from app.features.intent_detection.handler import intent_detection_handler

logger = logging.getLogger(__name__)

luna_router = APIRouter()


@luna_router.post("/chat")
async def chat(request: LunaRequest, http_request: Request):
    try:
        # The verified JWT claims, set by JWTAuthMiddleware; never body fields.
        auth_claims = getattr(http_request.state, "auth_claims", None)
        return await intent_detection_handler.detect_intent(request, auth_claims=auth_claims)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }
