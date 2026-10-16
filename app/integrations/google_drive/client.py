"""Async clients for Google Drive search: the parts of the Drive v3 REST API
that search uses, and Google OAuth 2.0 for the Drive connection (the sign-in
URL with PKCE, the code exchange, token refresh and revocation).

Both take tokens and settings from the caller and know nothing about users,
caches or configuration files; app.features.gdrive_search wires them up.
Shared Drives are included on every call that accepts them, since without
that most business content is invisible.
"""

import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------

class GoogleDriveError(Exception):
    """A Google Drive or Google OAuth call failed."""


class DriveAuthError(GoogleDriveError):
    """Google rejected the credentials: expired, revoked, or without the
    Drive permission. Retrying with the same token cannot succeed."""


class DriveRequestError(GoogleDriveError):
    """A Drive call failed for any other reason, after retries."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(f"Drive API {status} {reason}: {message}")
        self.status = status
        self.reason = reason


class FileTooLarge(GoogleDriveError):
    """A file exceeds the download or export ceiling."""


class OAuthExchangeError(GoogleDriveError):
    """Google refused to exchange an authorization code for tokens."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code

# ---------------------------------------------------------------------
# Drive v3
# ---------------------------------------------------------------------

DRIVE_API_URL = "https://www.googleapis.com/drive/v3"

# Error reasons Google reports with a 403. The rate-limit ones are worth
# retrying; the permission ones mean the token cannot do this at all.
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_AUTH_REASONS = {"authError", "insufficientPermissions"}

_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class GoogleDriveClient:

    def __init__(
        self,
        *,
        max_concurrency: int = 16,
        max_download_concurrency: int = 16,
        request_timeout_seconds: float = 30.0,
        download_timeout_seconds: float = 120.0,
        hedge_after_seconds: Optional[float] = None,
        include_shared_drives: bool = True,
        max_attempts: int = 3,
    ):
        # Downloads hold their slot while the body streams, so they get their
        # own: a search's queries never wait behind other files' downloads.
        self._slots = asyncio.Semaphore(max_concurrency)
        self._download_slots = asyncio.Semaphore(max_download_concurrency)
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
        self._download_timeout = aiohttp.ClientTimeout(total=download_timeout_seconds)
        self._hedge_after = hedge_after_seconds
        self._include_shared_drives = include_shared_drives
        self._max_attempts = max_attempts
        self._session: Optional[aiohttp.ClientSession] = None
        # Backup requests sent, for tracing.
        self.hedged = 0

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _shared_drive_params(self, *, listing: bool) -> dict:
        if not self._include_shared_drives:
            return {}
        params = {"supportsAllDrives": "true"}
        if listing:
            params.update(includeItemsFromAllDrives="true", corpora="allDrives")
        return params

    async def about_user(self, token: str) -> dict:
        """The account the token belongs to: permissionId and emailAddress."""
        body = await self._get_json(
            token,
            f"{DRIVE_API_URL}/about",
            {"fields": "user(permissionId,emailAddress,displayName)"},
        )
        return body.get("user") or {}

    async def list_files(
        self,
        token: str,
        *,
        query: str,
        fields: str,
        page_size: int,
        page_token: Optional[str] = None,
        order_by: Optional[str] = None,
    ) -> dict:
        params = {
            "q": query,
            "pageSize": str(page_size),
            "fields": fields,
            **self._shared_drive_params(listing=True),
        }
        if page_token:
            params["pageToken"] = page_token
        if order_by:
            params["orderBy"] = order_by
        return await self._get_json(token, f"{DRIVE_API_URL}/files", params)

    async def get_file(self, token: str, file_id: str, *, fields: str) -> dict:
        params = {"fields": fields, **self._shared_drive_params(listing=False)}
        return await self._get_json(token, f"{DRIVE_API_URL}/files/{file_id}", params)

    async def download(
        self,
        token: str,
        file_id: str,
        *,
        max_bytes: int,
        export_mime_type: Optional[str] = None,
    ) -> bytes:
        """A file's bytes; Google Docs, Sheets and Slides are exported to
        ``export_mime_type``. Stops reading past ``max_bytes``."""
        if export_mime_type:
            url = f"{DRIVE_API_URL}/files/{file_id}/export"
            params = {"mimeType": export_mime_type}
        else:
            url = f"{DRIVE_API_URL}/files/{file_id}"
            params = {"alt": "media", **self._shared_drive_params(listing=False)}

        return await self._get(
            token, url, params, lambda response: _read_capped(response, max_bytes), download=True
        )

    async def _get_json(self, token: str, url: str, params: dict) -> dict:
        return await self._get(token, url, params, lambda response: response.json(), download=False)

    async def _get(self, token: str, url: str, params: dict, read, *, download: bool):
        """A GET with retries; ``read`` turns a 200 response into the result."""
        slots = self._download_slots if download else self._slots
        timeout = self._download_timeout if download else self._timeout

        for attempt in range(self._max_attempts):
            try:
                response = await self._open(url, params, token, slots, timeout)

                try:
                    if response.status == 200:
                        return await read(response)
                    status, body = response.status, await response.text()
                finally:
                    response.release()
                    slots.release()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt + 1 >= self._max_attempts:
                    raise DriveRequestError(0, type(exc).__name__, str(exc)) from exc
                await asyncio.sleep(_backoff(attempt))
                continue

            if not _should_retry(status, body) or attempt + 1 >= self._max_attempts:
                _raise_for(status, body)
            await asyncio.sleep(_backoff(attempt))

        raise DriveRequestError(0, "unreachable", "retry loop exhausted")

    async def _open(
        self, url: str, params: dict, token: str, slots: asyncio.Semaphore, timeout: aiohttp.ClientTimeout
    ) -> aiohttp.ClientResponse:
        """The response once its headers arrive, holding a slot the caller
        releases with it.

        About 1.5% of Drive calls wait 3-30s for Google's first byte, at
        random: the same call asked again is fast. So a call with no headers
        after hedge_after_seconds is sent once more and the first to answer
        is used. Headers, not the body, so a download already streaming is
        never duplicated.
        """

        async def one() -> aiohttp.ClientResponse:
            await slots.acquire()
            try:
                return await self._get_session().get(url, params=params, headers=_auth(token), timeout=timeout)
            except BaseException:
                slots.release()
                raise

        def discard(task: asyncio.Task) -> None:
            # A request that answered after losing, or after its caller left.
            if not task.cancelled() and task.exception() is None:
                task.result().close()
                slots.release()

        tasks = [asyncio.ensure_future(one())]
        winner: Optional[asyncio.Task] = None

        try:
            if self._hedge_after is not None:
                done, _ = await asyncio.wait(tasks, timeout=self._hedge_after)

                if not done:
                    self.hedged += 1
                    tasks.append(asyncio.ensure_future(one()))

            pending, error = set(tasks), None

            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

                for task in done:
                    if task.exception() is None:
                        winner = task
                        return task.result()
                    error = task.exception()

            raise error
        finally:
            for task in tasks:
                if task is not winner:
                    task.cancel()
                    task.add_done_callback(discard)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _backoff(attempt: int) -> float:
    return 0.5 * (2 ** attempt)


async def _read_capped(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    declared = response.content_length
    if declared is not None and declared > max_bytes:
        raise FileTooLarge(f"{declared} bytes exceeds the limit of {max_bytes}")

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise FileTooLarge(f"Download exceeded {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _error_details(body: str) -> tuple[str, str]:
    """(reason, message) from Google's JSON error body, when it is one."""
    try:
        error = json.loads(body).get("error") or {}
    except (ValueError, AttributeError):
        return "", body[:200]
    if not isinstance(error, dict):
        return str(error), ""
    reasons = [e.get("reason", "") for e in error.get("errors") or [] if isinstance(e, dict)]
    return (reasons[0] if reasons else error.get("status", "")), error.get("message", "")


def _should_retry(status: int, body: str) -> bool:
    if status == 429 or status >= 500:
        return True
    return status == 403 and _error_details(body)[0] in _RATE_LIMIT_REASONS


def _raise_for(status: int, body: str):
    reason, message = _error_details(body)

    if status == 401 or (status == 403 and reason in _AUTH_REASONS):
        raise DriveAuthError(message or reason or f"HTTP {status}")
    if reason == "exportSizeLimitExceeded":
        raise FileTooLarge(message or reason)

    raise DriveRequestError(status, reason, message)

# ---------------------------------------------------------------------
# OAuth 2.0
# ---------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SCOPES = (
    DRIVE_READONLY_SCOPE,
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)


class GoogleOAuthClient:

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        timeout_seconds: float = 15.0,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._redirect_uri)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def authorization_url(self, *, state: str, code_challenge: str, force_consent: bool) -> str:
        """Where to send the browser to sign in.

        The account chooser is always shown, so a browser signed in to one
        Google account cannot silently connect the wrong Drive. Consent is
        forced only when asked for: it is the only way to make Google issue
        a new refresh token to an account that already granted access.
        """
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "consent select_account" if force_consent else "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict:
        """Trade the callback's code for tokens. Raises OAuthExchangeError."""
        status, body = await self._post_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
            }
        )
        if status != 200:
            raise OAuthExchangeError(body.get("error", f"http_{status}"), body.get("error_description", ""))
        return body

    async def refresh(self, refresh_token: str) -> dict:
        """A new access token. Raises DriveAuthError when Google refuses the
        refresh token (revoked, expired, or issued to another client)."""
        status, body = await self._post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }
        )
        if status == 200:
            return body
        if status in (400, 401):
            raise DriveAuthError(body.get("error_description") or body.get("error") or f"HTTP {status}")
        raise GoogleDriveError(f"Token refresh failed with HTTP {status}")

    async def revoke(self, token: str) -> bool:
        """End the grant at Google, not just this service's copy of it."""
        try:
            async with self._get_session().post(
                GOOGLE_REVOKE_URL, data={"token": token}, timeout=self._timeout
            ) as response:
                if response.status == 200:
                    return True
                logger.warning(f"Google refused to revoke access :: HTTP {response.status}")
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Could not revoke Google access :: {e}")
            return False

    async def _post_token(self, form: dict) -> tuple[int, dict]:
        # One retry for a network error or 5xx; anything else is an answer.
        for attempt in range(2):
            try:
                async with self._get_session().post(
                    GOOGLE_TOKEN_URL, data=form, timeout=self._timeout
                ) as response:
                    try:
                        body = await response.json(content_type=None)
                    except ValueError:
                        body = {}
                    if response.status < 500 or attempt == 1:
                        return response.status, body if isinstance(body, dict) else {}
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 1:
                    raise GoogleDriveError(f"Google token endpoint unreachable :: {e}") from e
            await asyncio.sleep(0.5)
        return 0, {}
