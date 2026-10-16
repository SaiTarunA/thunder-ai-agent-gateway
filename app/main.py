from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from app.ai import registry
from app.ai.router import model_router
from app.ai.tokenizer import count_tokens_for
from app.core.configs.app_config import ROUTES_V1
from app.core.log import start_logging, stop_logging
from app.db.mysql.connection.db_pool import DBPool
from app.features.document_intelligence.extraction.backends import office_text
from app.features.gdrive_search.connection import cache_redis, close_google_clients
from app.features.search.module import search_module
from app.middleware.manager import MiddlewareManager

logger = logging.getLogger(__name__)


def warm_up() -> None:
    """Load what a fresh worker would otherwise load during its first
    request, about two seconds of it. Nothing here is required: a step that
    fails is logged and its work happens on first use, as before."""
    steps = {
        "model client": model_router.warm_up,
        # Routing counts the tokens of every request.
        "tokenizer": lambda: count_tokens_for("", registry.MODEL_GPT_4_1_MINI),
        "document readers": office_text.preload,
    }

    for name, step in steps.items():
        try:
            step()
        except Exception as e:
            logger.warning(f"Startup warm-up step failed, left to first use :: {name}, {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_logging()

    try:
        await DBPool().init_db_pool()
        await search_module.initialize()

        # Drive search depends on the local cache Redis, but nothing else
        # does, so an unreachable one is reported rather than fatal.
        if not await cache_redis.ping():
            logger.warning("Google Drive search's Redis is unreachable")

        warm_up()

        yield

    finally:
        await close_google_clients()
        await cache_redis.close()
        await search_module.close()
        await DBPool().close_all()
        stop_logging()


# FastAPI server
app = FastAPI(lifespan=lifespan)

# Middlewares
MiddlewareManager(app).add_middlewares()

# Routes
for prefix, router in ROUTES_V1.items():
    logger.info(f"adding routes for prefix: {prefix}")
    app.include_router(router, prefix=f"/api/v1/{prefix}", tags=[prefix])


@app.get("/health")
async def health_check():
    return {"status": "ok"}


"""
Local startup command:
uv run fastapi dev -e app.main:app --host 0.0.0.0 --port 5006

Production startup command:
nohup uv run gunicorn -c gunicorn_config.py app.main:app 2>&1 &
"""
