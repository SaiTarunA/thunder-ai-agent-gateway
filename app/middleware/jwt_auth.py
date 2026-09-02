import logging
import os
import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.constants import PUBLIC_PATHS

logger = logging.getLogger(__name__)

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM")
JWT_LEEWAY = os.getenv("JWT_LEEWAY")

class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization") or request.headers.get(
            "authorization"
        )
        if not auth_header:
            return JSONResponse(
                status_code=401, content={"detail": "Missing Authorization header"}
            )

        try:
            payload = jwt.decode(
                auth_header,
                JWT_SECRET_KEY,
                algorithms=[JWT_ALGORITHM],
                leeway=JWT_LEEWAY,
            )

            request.state.user = payload  # accessible later via request.state.user
        except jwt.ExpiredSignatureError:
            logger.error(f"Token has expired", exc_info=True)
            return JSONResponse(
                status_code=401, content={"detail": "Token has expired"}
            )
        except jwt.InvalidTokenError:
            logger.error(f"Invalid token", exc_info=True)
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
        except jwt.ImmatureSignatureError:
            logger.error("Token is not yet valid (iat)", exc_info=True)
            return JSONResponse(
                status_code=401, content={"detail": "Token is not yet valid"}
            )

        return await call_next(request)










"""
import logging
from starlette.types import ASGIApp, Scope, Receive, Send
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from fastapi import status

from app.core import constants
from app.core.jwt import decode_access_token

logger = logging.getLogger(__name__)


class JWTAuthMiddleware:
    
    Pure ASGI middleware validating bearer-token JWT headers.

    Bypass rules:
    - Heartbeat path → immediate 200 OK
    - Public paths → pass through
    - Requests with Agentid or X-Authtoken headers → delegated to AuthMiddleware
    

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path

        if path == constants.HEART_BEAT_PATH:
            response = JSONResponse(
                content={"message": "ok"},
                status_code=status.HTTP_200_OK,
            )
            await response(scope, receive, send)
            return

        # Bypass excluded paths or legacy-auth requests
        if (
            path in constants.PUBLIC_PATHS
            or any(path.startswith(p) for p in constants.PUBLIC_PATHS)
            or (request.headers.get("Agentid") or request.headers.get("X-Authtoken"))
        ):
            await self.app(scope, receive, send)
            return

        try:

            auth_header = request.headers.get("Authorization")

            if auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[7:]
                payload = decode_access_token(token)
                if not payload:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired token",
                    )

                agentid = payload.get("sub")
                if not agentid:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token missing subject (sub)",
                    )

                request.state.agentid = agentid
                request.state.sitename = agentid.split("@")[1] if "@" in agentid else agentid
                request.state.is_callback_required = False

                logger.info(f"JWT authenticated successfully | agentid={agentid}")
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Not authenticated. Missing Token.",
                )

            await self.app(scope, receive, send)

        except HTTPException as exc:
            logger.warning(
                "JWT Authentication failed | method=%s path=%s status=%s detail=%s",
                request.method,
                path,
                exc.status_code,
                exc.detail,
            )
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
            await response(scope, receive, send)

        except Exception as e:
            logger.exception(
                "Unhandled JWT authentication middleware error | method=%s path=%s",
                request.method,
                path,
            )
            response = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Internal Server Error during JWT Authentication: {str(e)}"},
            )
            await response(scope, receive, send)

"""


