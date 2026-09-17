import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from app.core.log import request_id_var

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that assigns a unique request ID (or reuses X-Request-ID header)
    for every incoming HTTP request, storing it in contextvars so all loggers
    automatically print req_id:<unique_id> for the duration of the request.
    """

    async def dispatch(self, request: Request, call_next):
        incoming_id = request.headers.get("X-Request-ID") or request.headers.get("X-Correlation-ID")
        req_id = incoming_id.strip() if incoming_id else uuid.uuid4().hex[:12]

        token = request_id_var.set(req_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            request_id_var.reset(token)
