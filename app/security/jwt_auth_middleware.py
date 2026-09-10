import logging
import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.constants import PUBLIC_PATHS, HEART_BEAT_PATH
from app.security.jwt_manager import jwt_manager

logger = logging.getLogger(__name__)


class JWTAuthMiddleware(BaseHTTPMiddleware):
    def _unauthorized_response(self, request: Request, content: dict) -> JSONResponse:
        origin = request.headers.get("origin")
        headers = {}
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
        return JSONResponse(
            status_code=401,
            content=content,
            headers=headers,
        )

    async def dispatch(self, request: Request, call_next):
        # Bypass CORS preflight requests (OPTIONS)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Bypass heartbeat and public paths (e.g. /api/v1/auth/generate_tokens)
        if (
            path == HEART_BEAT_PATH
            or path in PUBLIC_PATHS
            or any(path.startswith(p) for p in PUBLIC_PATHS)
        ):
            return await call_next(request)

        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")

        if not auth_header:
            return self._unauthorized_response(
                request,
                content={"detail": "Missing Authorization header", "code": "missing_token"},
            )

        token = auth_header
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        try:
            payload = jwt_manager.decode_access_token(token)

            agentid = payload.get("sub")
            if not agentid:
                return self._unauthorized_response(
                    request,
                    content={"detail": "Token missing subject claim (sub)", "code": "invalid_token"},
                )
        except jwt.ExpiredSignatureError:
            logger.warning(f"Access token expired for path {path}")
            return self._unauthorized_response(
                request,
                content={"detail": "Token has expired", "code": "token_expired"},
            )
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid access token for path {path}: {str(e)}")
            return self._unauthorized_response(
                request,
                content={"detail": f"Invalid token: {str(e)}", "code": "invalid_token"},
            )
        except Exception as e:
            logger.error(f"Unexpected authentication error: {str(e)}", exc_info=True)
            return self._unauthorized_response(
                request,
                content={"detail": "Authentication failed", "code": "auth_failed"},
            )

        return await call_next(request)
