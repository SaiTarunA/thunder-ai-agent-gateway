from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.security.jwt_auth_middleware import JWTAuthMiddleware

logger = logging.getLogger(__name__)


class MiddlewareManager:

    def __init__(self, server: FastAPI):
        self.server = server

    def add_middlewares(self):
        try:
            # Adding CORS middleware
            self.server.add_middleware(
                CORSMiddleware,
                allow_origins=[
                    "http://localhost:5173",
                    "http://localhost:3000",
                    "http://127.0.0.1:5173",
                ],
                allow_origin_regex=r"https?://.*",
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

            # Adding authentication middlewares
            self.server.add_middleware(JWTAuthMiddleware)

        except Exception as e:
            logger.exception(f"Failed to register middlewares :: {str(e)}")
            raise
