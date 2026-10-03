import logging

from fastapi import APIRouter, BackgroundTasks, status

from app.features.search.indexing.api_models import (
    IndexEventAccepted,
    MessageIndexEventRequest,
    StreamIndexEventRequest,
)
from app.workers import indexing_worker

logger = logging.getLogger(__name__)

search_indexing_router = APIRouter()


@search_indexing_router.post(
    "/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IndexEventAccepted,
)
async def upsert_message(
    request: MessageIndexEventRequest,
    background_tasks: BackgroundTasks,
):
    """Message created or edited. Schedules a re-fetch-and-upsert; no-ops
    if the message isn't indexable (wrong msgtype, already deleted) by the
    time the task runs."""
    background_tasks.add_task(
        indexing_worker.upsert_message,
        site_id=request.site_id,
        sid=request.sid,
        message_id=request.message_id,
    )
    return IndexEventAccepted()


@search_indexing_router.post(
    "/messages/delete",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IndexEventAccepted,
)
async def delete_message(
    request: MessageIndexEventRequest,
    background_tasks: BackgroundTasks,
):
    """Message soft-deleted in MySQL. Schedules removal from the index."""
    background_tasks.add_task(
        indexing_worker.delete_message,
        site_id=request.site_id,
        sid=request.sid,
        message_id=request.message_id,
    )
    return IndexEventAccepted()


@search_indexing_router.post(
    "/streams/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IndexEventAccepted,
)
async def reindex_stream(
    request: StreamIndexEventRequest,
    background_tasks: BackgroundTasks,
):
    """Stream metadata changed (e.g. channel_type). Schedules a full
    reindex of every message in the stream so the denormalized
    channel_type on each document is refreshed."""
    background_tasks.add_task(
        indexing_worker.reindex_stream,
        site_id=request.site_id,
        sid=request.sid,
    )
    return IndexEventAccepted()


@search_indexing_router.post(
    "/streams/delete",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IndexEventAccepted,
)
async def delete_stream(
    request: StreamIndexEventRequest,
    background_tasks: BackgroundTasks,
):
    """Stream deleted. Schedules removal of every message in the stream
    from the index."""
    background_tasks.add_task(
        indexing_worker.delete_stream,
        site_id=request.site_id,
        sid=request.sid,
    )
    return IndexEventAccepted()
