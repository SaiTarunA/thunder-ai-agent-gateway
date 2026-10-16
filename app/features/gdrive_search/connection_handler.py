"""Connecting a Google Drive: the sign-in flow, connection status and
disconnect.

Google sends the browser back with a plain redirect that carries no
Authorization header, so the callback cannot check the gateway JWT. The
single-use state stored when sign-in starts holds the principal instead: it
proves who started the sign-in, and stops a Drive being attached to someone
else's account.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

import jwt
from fastapi import status

from app.features.gdrive_search.config import gdrive_config
from app.features.gdrive_search.connection import (
    CONNECTED,
    DriveConnection,
    DriveUser,
    cache_redis,
    connection_store,
    drive_client,
    gateway_oauth_tokens,
    oauth_client,
    oauth_state_key,
    token_cipher,
)
from app.features.gdrive_search.names import forget_names, warm_listing
from app.integrations.google_drive.client import DRIVE_READONLY_SCOPE, GoogleDriveError, OAuthExchangeError

logger = logging.getLogger(__name__)

# Revocations at Google, run after the disconnect response and referenced
# until done.
_revocations: set[asyncio.Task] = set()


class DriveConnectionHandler:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def start_connect(self, principal: str, *, force_consent: bool = False) -> dict:
        """Begin sign-in: the URL to send the browser to."""
        try:
            if not (oauth_client.is_configured and token_cipher.is_configured):
                logger.error(
                    "Google Drive connection is not configured: set gdrive_config.oauth_client_id "
                    "and GDRIVE_OAUTH_CLIENT_SECRET / GDRIVE_TOKEN_ENCRYPTION_KEYS in .env"
                )
                return {
                    "status": status.HTTP_503_SERVICE_UNAVAILABLE,
                    "error": "Google Drive connection is not configured",
                    "msg": "Failed",
                }

            state = secrets.token_urlsafe(32)
            code_verifier = secrets.token_urlsafe(64)
            code_challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
                .rstrip(b"=")
                .decode("ascii")
            )

            await cache_redis.client.set(
                oauth_state_key(state),
                json.dumps(
                    {"principal": principal, "code_verifier": code_verifier, "force_consent": force_consent}
                ),
                ex=gdrive_config.oauth_state_ttl_seconds,
            )

            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "authorization_url": oauth_client.authorization_url(
                    state=state, code_challenge=code_challenge, force_consent=force_consent
                ),
            }

        except Exception as e:
            logger.exception(f"Error : {e}, principal :: {principal}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def complete_connect(self, *, code: str, state: str, error: str) -> str:
        """Finish sign-in from Google's callback; the URL to send the browser
        to next (the frontend, or Google again when consent must be forced)."""
        try:
            raw = await cache_redis.client.getdel(oauth_state_key(state)) if state else None

            if not raw:
                # Expired, already used, or never issued by us.
                return _error_redirect("invalid_state")

            pending = json.loads(raw)
            principal = pending["principal"]

            if error or not code:
                # Cancelled on the consent screen, or refused by the account's
                # Workspace admin.
                return _error_redirect((error or "no_code")[:64])

            try:
                tokens = await oauth_client.exchange_code(code=code, code_verifier=pending["code_verifier"])
            except OAuthExchangeError as e:
                logger.warning(f"Google code exchange refused :: {e}, principal :: {principal}")
                return _error_redirect("token_exchange_failed")

            # Each permission on Google's consent screen can be unticked and
            # sign-in completes anyway; without Drive there is nothing to search.
            granted = (tokens.get("scope") or "").split()

            if granted and DRIVE_READONLY_SCOPE not in granted:
                return _error_redirect("drive_permission_missing")

            access_token = tokens["access_token"]
            google_user = await drive_client.about_user(access_token)
            google_user_id = google_user.get("permissionId", "")

            refresh_token = tokens.get("refresh_token")

            if refresh_token:
                stored_refresh_token = token_cipher.encrypt(refresh_token)
            else:
                # Google omits the refresh token when this account already
                # granted access and consent was not forced. Reuse the stored
                # one if it still works; otherwise force consent once.
                existing = await connection_store.get(principal)

                if existing and existing.google_user_id == google_user_id and await _refresh_works(existing):
                    stored_refresh_token = existing.refresh_token
                elif not pending.get("force_consent"):
                    started = await self.start_connect(principal, force_consent=True)
                    return started.get("authorization_url") or _error_redirect("no_refresh_token")
                else:
                    return _error_redirect("no_refresh_token")

            # A different Google account may have been connected before:
            # nothing cached from it may be served to this one.
            await forget_names(principal)
            await gateway_oauth_tokens.forget_access_token(principal)

            await connection_store.save(
                principal,
                DriveConnection(
                    google_user_id=google_user_id,
                    email=google_user.get("emailAddress", ""),
                    refresh_token=stored_refresh_token,
                    hosted_domain=_hosted_domain(tokens.get("id_token")),
                    granted_scopes=" ".join(granted),
                    connected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            await gateway_oauth_tokens.store_access_token(principal, access_token, int(tokens.get("expires_in") or 3600))

            # Built now rather than by the first search, which would wait on
            # it. Listing calls no model, so there is no agentid to bill.
            warm_listing(DriveUser(principal=principal, agentid=""))

            logger.info(f"Google Drive connected :: principal={principal}")
            return _connected_redirect()

        except GoogleDriveError as e:
            logger.warning(f"Google Drive sign-in failed :: {e}")
            return _error_redirect("google_unavailable")
        except Exception as e:
            logger.exception(f"Error : {e}")
            return _error_redirect("server_error")

    async def connection_status(self, principal: str) -> dict:
        try:
            connection = await connection_store.get(principal)

            if connection is None:
                return {
                    "status": status.HTTP_200_OK,
                    "msg": "Success",
                    "connected": False,
                    "connection_status": "not_connected",
                }

            return {
                "status": status.HTTP_200_OK,
                "msg": "Success",
                "connected": connection.status == CONNECTED,
                "connection_status": connection.status,
                "email": connection.email,
                **_account_notice(connection),
            }

        except Exception as e:
            logger.exception(f"Error : {e}, principal :: {principal}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }

    async def disconnect(self, principal: str) -> dict:
        """Disconnect and delete everything derived from the Drive. The grant
        is revoked at Google after the response, so the user is not left
        waiting on a network call; a failed revoke is only logged."""
        try:
            connection = await connection_store.get(principal)

            await forget_names(principal)
            await gateway_oauth_tokens.forget_access_token(principal)
            await connection_store.delete(principal)

            if connection is not None:
                task = asyncio.create_task(_revoke(connection))
                _revocations.add(task)
                task.add_done_callback(_revocations.discard)

            logger.info(f"Google Drive disconnected :: principal={principal}")
            return {"status": status.HTTP_200_OK, "msg": "Success", "connected": False}

        except Exception as e:
            logger.exception(f"Error : {e}, principal :: {principal}")
            return {
                "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "error": str(e),
                "msg": "Failed",
            }


# Shown to the user by the frontend; Google's own error codes (e.g.
# access_denied from the consent screen) fall back to the default.
_ERROR_MESSAGES = {
    "access_denied": "Google Drive access was not granted.",
    "invalid_state": "The sign-in link expired. Please connect Google Drive again.",
    "token_exchange_failed": "Google sign-in could not be completed. Please try again.",
    "drive_permission_missing": "Drive access was not allowed. Connect again and keep the Google Drive permission ticked.",
    "no_refresh_token": (
        "Google did not grant lasting access. Remove this app under your Google account's "
        "third-party access, then connect again."
    ),
    "google_unavailable": "Google could not be reached. Please try again.",
    "server_error": "Something went wrong while connecting Google Drive. Please try again.",
}


def _frontend_redirect(**params: str) -> str:
    """Where sign-in ends: gdrive_config.frontend_redirect_url, told the outcome."""
    url = gdrive_config.frontend_redirect_url
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _connected_redirect() -> str:
    return _frontend_redirect(connected="1")


def _error_redirect(code: str) -> str:
    return _frontend_redirect(error=code, message=_ERROR_MESSAGES.get(code, "Google sign-in did not complete."))


async def _refresh_works(connection: DriveConnection) -> bool:
    try:
        await oauth_client.refresh(token_cipher.decrypt(connection.refresh_token))
        return True
    except (GoogleDriveError, ValueError):
        return False


async def _revoke(connection: DriveConnection) -> None:
    try:
        await oauth_client.revoke(token_cipher.decrypt(connection.refresh_token))
    except ValueError:
        logger.warning("Stored Google token could not be decrypted for revocation")


def _hosted_domain(id_token: Optional[str]) -> str:
    """The Google Workspace domain managing the account, "" if none does.

    The ID token is read without checking its signature: it came straight
    from Google's token endpoint over TLS in this request, which OpenID
    Connect (section 3.1.3.7) accepts in place of the signature check.
    """
    if not id_token:
        return ""

    try:
        claims = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return ""

    return str(claims.get("hd", "")).lower()


def _account_notice(connection: DriveConnection) -> dict:
    """Whether to warn that the connected account looks personal: outside
    the company domains, or not managed by their Workspace. A personal
    Google account registered with a work address has the address but not
    the management. Judged when asked, so a change to the domains applies to
    accounts already connected."""
    domains = gdrive_config.company_email_domains
    email_domain = connection.email.rpartition("@")[2].strip().lower()

    personal = bool(domains) and (email_domain not in domains or connection.hosted_domain not in domains)

    return {"personal_account": personal, "company_domains": list(domains)}


drive_connection_handler = DriveConnectionHandler()
