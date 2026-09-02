from fastapi import APIRouter
import logging
from app.core.schemas import ChatRequest

logger = logging.getLogger(__name__)

luna_router = APIRouter()


@luna_router.post("/chat")
async def chat(request: ChatRequest):
    logger.info("Chat endpoint called")
    return {"status": "ok"}
