from fastapi.responses import JSONResponse
import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response, status

from app.modules.auth.jwt_manager import jwt_manager, REFRESH_TOKEN_EXPIRE_DAYS
from app.db.mysql.repositories.cloud_repo import cloud_db_handler
from app.modules.auth.schemas import GenerateTokensRequest, TokenResponse

logger = logging.getLogger(__name__)

auth_router = APIRouter()


@auth_router.post(
    "/generate_tokens",
    response_model=TokenResponse,
    summary="Generate or refresh AI service JWT access and refresh tokens",
)
async def generate_tokens(
    request: Request,
    response: Response,
    payload: Optional[GenerateTokensRequest] = None,
    cookie_refresh_token: Optional[str] = Cookie(None, alias="refresh_token"),
    access_token: Optional[str] = Header(None, alias="X-Authtoken"),
    agentid: Optional[str] = Header(None, alias="Agentid"),
):
    """
    Creates or refreshes AI session tokens:
    1. If a valid refresh token is present (cookie or payload), rotate refresh token and return new token pair.
    2. If refresh token is expired or missing, fallback to verifying user session via cloud_db_handler using authkey & username.
    """
    payload_obj = payload or GenerateTokensRequest()

    # Step 1: Check for Refresh Token in cookie or payload
    refresh_token = cookie_refresh_token or payload_obj.refresh_token

    if refresh_token:
        new_access_token, new_refresh_token, err = jwt_manager.verify_and_rotate_refresh_token(refresh_token)
        if new_access_token and new_refresh_token:
            logger.info("Successfully refreshed AI session tokens via refresh token rotation.")
            # Set HttpOnly + Secure cookie
            response.set_cookie(
                key="refresh_token",
                value=new_refresh_token,
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
                path="/ai_auth",
            )
            return JSONResponse(
                content={
                    "access_token": new_access_token,
                    "refresh_token": new_refresh_token,
                },
                status_code=status.HTTP_200_OK,
            )
        else:
            logger.warning(f"Refresh token rotation failed: {err}. Falling back to DB session validation.")

    # Step 2: Fallback to DB session verification via authkey & username
    authkey = payload_obj.authkey or payload_obj.x_authtoken or access_token
    username = payload_obj.username or agentid

    if not authkey or not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required authentication details (authkey/x-authtoken or username/agentid) or valid refresh token",
        )

    # Validate against cloud_db_handler
    user_session_records = await cloud_db_handler.fetch_query_for_authorization(
        authkey=authkey,
        username=username,
    )

    if not user_session_records:
        logger.warning(f"DB authorization check failed for username: {username}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authorization credentials or user session non-existent/expired",
        )

    # Generate initial AI session tokens
    access_token = jwt_manager.create_access_token(user_id=username)
    new_refresh_token = jwt_manager.create_refresh_token(username=username, authkey=authkey)

    # Set HttpOnly + Secure cookie
    response.set_cookie(
        key="refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
        path="/ai_auth",
    )

    logger.info(f"Successfully generated new AI session tokens for user: {username}")
    return JSONResponse(
        content={
            "access_token": access_token,
            "refresh_token": new_refresh_token,
        },
        status_code=status.HTTP_200_OK,
    )
