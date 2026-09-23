from fastapi import APIRouter, status
import logging

from app.features.search.domain.models import SearchContextRequest
from app.features.search.module import search_module

logger = logging.getLogger(__name__)

search_router = APIRouter()


@search_router.get("/info")
async def info():
    try:
        return {"status": status.HTTP_200_OK, "msg": "Search API is running"}
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }


@search_router.post("/context")
async def context(request: SearchContextRequest):
    try:
        return await search_module.search(request)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }