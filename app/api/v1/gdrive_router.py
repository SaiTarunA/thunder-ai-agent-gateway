from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse
import logging

from app.features.gdrive_search.connection import drive_user_from, principal_from_claims
from app.features.gdrive_search.connection_handler import drive_connection_handler
from app.features.gdrive_search.handler import drive_search_handler
from app.features.gdrive_search.schemas import DriveSearchRequest

logger = logging.getLogger(__name__)

gdrive_router = APIRouter()

_UNAUTHENTICATED = {
    "status": status.HTTP_401_UNAUTHORIZED,
    "error": "No authenticated user on this request",
    "msg": "Failed",
}


def _claims(request: Request) -> dict:
    """The verified JWT claims JWTAuthMiddleware put on the request."""
    return getattr(request.state, "auth_claims", None) or {}


@gdrive_router.post("/search")
async def search(request: Request, body: DriveSearchRequest):
    """Search the caller's Drive directly, without Luna's intent routing."""
    try:
        claims = _claims(request)
        user = drive_user_from(claims)
        if user is None:
            return _UNAUTHENTICATED

        request_data = {
            # Billing rows need a site: without one in the body, the token's.
            "siteid": body.siteid if body.siteid is not None else claims.get("siteid", ""),
            "sitename": body.sitename or "",
            "agentid": body.agentid or user.agentid,
            "timezone": body.timezone,
        }
        return await drive_search_handler.search(user, body, request_data)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }


@gdrive_router.post("/connect")
async def connect(request: Request):
    """Start connecting a Google Drive: returns the Google sign-in URL."""
    try:
        principal = principal_from_claims(_claims(request))
        if not principal:
            return _UNAUTHENTICATED
        return await drive_connection_handler.start_connect(principal)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }


@gdrive_router.get("/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Google's redirect after sign-in. Public (see PUBLIC_PATHS): the
    single-use state, not a JWT, identifies who started the sign-in."""
    redirect_url = await drive_connection_handler.complete_connect(code=code, state=state, error=error)
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)


@gdrive_router.get("/status")
async def connection_status(request: Request):
    try:
        principal = principal_from_claims(_claims(request))
        if not principal:
            return _UNAUTHENTICATED
        return await drive_connection_handler.connection_status(principal)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }


@gdrive_router.delete("/connection")
async def disconnect(request: Request):
    try:
        principal = principal_from_claims(_claims(request))
        if not principal:
            return _UNAUTHENTICATED
        return await drive_connection_handler.disconnect(principal)
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }
