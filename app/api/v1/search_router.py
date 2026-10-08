from fastapi import APIRouter, status
import logging

from app.api.v1.search_indexing_router import search_indexing_router
from app.features.search.domain.models import SearchContextRequest
from app.features.search.module import search_module
from app.features.search_answer.handler import search_answer_handler
from app.features.search_answer.schemas import SearchAnswerRequest, SearchAnswerResponse

logger = logging.getLogger(__name__)

search_router = APIRouter()
search_router.include_router(search_indexing_router, prefix="/index")


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


@search_router.post("/answer", response_model=SearchAnswerResponse)
async def answer(request: SearchAnswerRequest):
    # No try/except here - the handler already catches its own exceptions
    # and returns a graceful has_answer=False response rather than ever
    # raising, since the frontend contract shouldn't have to special-case
    # an error state for this feature.
    return await search_answer_handler.handle(request)