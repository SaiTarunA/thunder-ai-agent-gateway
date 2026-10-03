"""
Background task functions for the live indexing pipeline - scheduled via
FastAPI's BackgroundTasks from app.api.v1.search_indexing_router, so they
run after the HTTP response is sent rather than blocking the request.

v1 scope: best-effort, no queue. If the worker process is recycled between
the response being sent and a task running here, that event is silently
missed - accepted for now, see the search feature's indexing-pipeline
design notes for the reasoning. Each function re-fetches current state
from MySQL rather than trusting its arguments as a snapshot, so even
out-of-order delivery across gunicorn's worker processes still converges
on the correct result.
"""
import logging

from app.features.search.module import search_module

logger = logging.getLogger(__name__)


async def upsert_message(site_id: int, sid: int, message_id: int) -> None:
    indexer = search_module.get_message_indexer()

    result = await indexer.index_message(
        site_id=site_id,
        sid=sid,
        message_id=message_id,
    )

    logger.info(
        "upsert_message :: site_id=%s sid=%s message_id=%s result=%s",
        site_id,
        sid,
        message_id,
        result,
    )


async def delete_message(site_id: int, sid: int, message_id: int) -> None:
    bulk_writer = search_module.get_bulk_writer()

    deleted = await bulk_writer.delete(message_id)

    logger.info(
        "delete_message :: site_id=%s sid=%s message_id=%s deleted=%s",
        site_id,
        sid,
        message_id,
        deleted,
    )


async def reindex_stream(site_id: int, sid: int) -> None:
    indexer = search_module.get_message_indexer()

    result = await indexer.index_messages(site_id=site_id, sids=[sid])

    logger.info(
        "reindex_stream :: site_id=%s sid=%s result=%s",
        site_id,
        sid,
        result,
    )


async def delete_stream(site_id: int, sid: int) -> None:
    bulk_writer = search_module.get_bulk_writer()

    deleted = await bulk_writer.delete_by_sid(site_id, sid)

    logger.info(
        "delete_stream :: site_id=%s sid=%s deleted=%s",
        site_id,
        sid,
        deleted,
    )
