from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging

from app.core.configs.app_config import ROUTES_V1

from app.core.log import start_logging, stop_logging
from app.middleware.manager import MiddlewareManager
from app.db.mysql.connection.db_pool import DBPool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_logging()
    try:
        await DBPool().init_db_pool()
        yield
    finally:
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
