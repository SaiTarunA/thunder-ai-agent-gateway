from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.middleware.jwt_auth import JWTAuthMiddleware

logger = logging.getLogger(__name__)


class MiddlewareManager:

    def __init__(self, server: FastAPI):
        self.server = server

    def add_middlewares(self):
        try:
            # Adding CORS middleware
            self.server.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

            # Adding authentication middlewares
            self.server.add_middleware(JWTAuthMiddleware)

        except Exception:
            logger.exception("Failed to register middlewares")
            raise
