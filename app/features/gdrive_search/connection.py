"""A user's Google Drive connection: who is asking, the stored connection,
how Google tokens are encrypted at rest, the access token each request runs
with, and the Google clients.

The gateway keeps one long-lived Google refresh token per connected user,
encrypted, and derives short-lived access tokens from it as needed.
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, fields
from typing import Awaitable, Callable, Optional, TypeVar

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from dotenv import load_dotenv
from redis.asyncio import Redis

from app.features.gdrive_search.config import gdrive_config
from app.integrations.google_drive.client import DriveAuthError, GoogleDriveClient, GoogleOAuthClient

load_dotenv()

logger = logging.getLogger(__name__)

T = TypeVar("T")

# An access token is refreshed this long before Google says it expires,
# rather than discovered expired in the middle of a search.
_ACCESS_TOKEN_MARGIN_SECONDS = 60


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------

class DriveNotConnected(Exception):
    """The user has not connected a Google Drive."""


class DriveAuthRequired(Exception):
    """The stored Drive connection no longer works; the user has to reconnect."""


class DriveUnavailable(Exception):
    """Drive refused or never answered every search for a question."""


# ---------------------------------------------------------------------
# Local Redis
# ---------------------------------------------------------------------


class CacheRedisConnection:
    """The local Redis behind Google Drive search (CACHE_REDIS_* in .env),
    separate from the search module's Sentinel Redis (REDIS_*). db 1 by
    default.

    The client is built on first use, so importing this module never opens
    a connection and an unreachable Redis only affects the requests that
    need it.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
        return cls._instance

    @property
    def client(self) -> Redis:
        if self._client is None:
            timeout = float(os.environ.get("CACHE_REDIS_SOCKET_TIMEOUT_SECONDS", "5"))
            self._client = Redis(
                # Not "localhost": it resolves to ::1 first, and on Windows a
                # Redis listening on IPv4 only costs every new connection a
                # 2s failed attempt there before 127.0.0.1 is tried.
                host=os.environ.get("CACHE_REDIS_HOST", "127.0.0.1"),
                port=int(os.environ.get("CACHE_REDIS_PORT", "6379")),
                db=int(os.environ.get("CACHE_REDIS_DB", "1")),
                password=os.environ.get("CACHE_REDIS_PASSWORD") or None,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
                decode_responses=True,
            )
        return self._client

    async def ping(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception as e:
            logger.warning(f"Cache Redis is unreachable :: {e}")
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


cache_redis = CacheRedisConnection()



# ---------------------------------------------------------------------
# Redis keys
# In the local cache Redis (CACHE_REDIS_* in .env). Per-user keys use the gateway
# principal (principal_from_claims), never a value from a request body.
# ---------------------------------------------------------------------

def connection_key(principal: str) -> str:
    return f"gdrive:conn:{principal}"


def access_token_key(principal: str) -> str:
    return f"gdrive:access:{principal}"


def oauth_state_key(state: str) -> str:
    return f"gdrive:oauth:{state}"


# ---------------------------------------------------------------------
# Google clients
# ---------------------------------------------------------------------

drive_client = GoogleDriveClient(
    max_concurrency=gdrive_config.drive_api_concurrency,
    max_download_concurrency=gdrive_config.drive_download_concurrency,
    request_timeout_seconds=gdrive_config.drive_request_timeout_seconds,
    hedge_after_seconds=gdrive_config.drive_hedge_after_seconds,
    include_shared_drives=gdrive_config.include_shared_drives,
)

oauth_client = GoogleOAuthClient(
    client_id=gdrive_config.oauth_client_id,
    client_secret=os.environ.get("GDRIVE_OAUTH_CLIENT_SECRET", ""),
    redirect_uri=gdrive_config.oauth_redirect_uri,
)


async def close_google_clients() -> None:
    await drive_client.close()
    await oauth_client.close()


# ---------------------------------------------------------------------
# Who is asking
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class DriveUser:
    """The user a request searches the Drive of."""

    # What per-user state (the connection, caches) is keyed by.
    principal: str
    # The verified JWT subject.
    agentid: str


def principal_from_claims(claims: Optional[dict]) -> Optional[str]:
    """The gateway user, from verified JWT claims: ``sub``, qualified by
    ``siteid`` when the token carries one."""
    if not claims:
        return None
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        return None
    siteid = str(claims.get("siteid") or "").strip()
    return f"{siteid}:{sub}" if siteid else sub


def drive_user_from(claims: Optional[dict]) -> Optional[DriveUser]:
    """The DriveUser for a request with these verified JWT claims; None
    without an authenticated subject."""
    principal = principal_from_claims(claims)

    if principal is None:
        return None

    return DriveUser(principal=principal, agentid=str(claims["sub"]).strip())


# ---------------------------------------------------------------------
# Connections
# A connection is the one piece of Drive state that cannot be rebuilt: losing
# it means the user has to connect again. Everything else (access tokens,
# listings, file text) is cache. The store sits behind ConnectionStore so it
# can move to MySQL by adding an implementation and changing
# gdrive_config.connection_store.
# ---------------------------------------------------------------------

CONNECTED = "connected"
REAUTH_REQUIRED = "reauth_required"


@dataclass
class DriveConnection:
    google_user_id: str
    email: str
    # Encrypted with TokenCipher; never stored in the clear.
    refresh_token: str
    hosted_domain: str = ""
    granted_scopes: str = ""
    status: str = CONNECTED
    last_error: str = ""
    # ISO-8601, UTC.
    connected_at: str = ""


class ConnectionStore(ABC):

    @abstractmethod
    async def get(self, principal: str) -> Optional[DriveConnection]:
        raise NotImplementedError

    @abstractmethod
    async def save(self, principal: str, connection: DriveConnection) -> None:
        raise NotImplementedError

    @abstractmethod
    async def set_status(self, principal: str, status: str, error: str = "") -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, principal: str) -> None:
        raise NotImplementedError


class RedisConnectionStore(ConnectionStore):
    """One hash per principal, with no expiry."""

    _FIELDS = tuple(f.name for f in fields(DriveConnection))

    def __init__(self, redis_connection: CacheRedisConnection = cache_redis):
        self._redis = redis_connection

    async def get(self, principal: str) -> Optional[DriveConnection]:
        data = await self._redis.client.hgetall(connection_key(principal))
        if not data or not data.get("refresh_token"):
            return None
        return DriveConnection(**{name: data[name] for name in self._FIELDS if name in data})

    async def save(self, principal: str, connection: DriveConnection) -> None:
        key = connection_key(principal)
        async with self._redis.client.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping=asdict(connection))
            await pipe.execute()

    async def set_status(self, principal: str, status: str, error: str = "") -> None:
        key = connection_key(principal)
        if await self._redis.client.exists(key):
            await self._redis.client.hset(key, mapping={"status": status, "last_error": error[:300]})

    async def delete(self, principal: str) -> None:
        await self._redis.client.delete(connection_key(principal))


def build_connection_store(store_name: str) -> ConnectionStore:
    if store_name == "redis":
        return RedisConnectionStore()
    raise ValueError(f"Unknown Drive connection store :: {store_name}")


# ---------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------

class TokenCipher:
    """Encrypts Google tokens before they are stored.

    Keys come from GDRIVE_TOKEN_ENCRYPTION_KEYS: comma-separated Fernet keys,
    the first used to encrypt and every one to decrypt, so a key can be
    rotated by putting a new one first. Losing every key disconnects every
    user.
    """

    def __init__(self, raw_keys: str):
        keys = [key.strip() for key in (raw_keys or "").split(",") if key.strip()]
        try:
            self._fernet = MultiFernet([Fernet(key) for key in keys]) if keys else None
        except ValueError as e:
            # Logged rather than raised: a bad key must not stop the gateway
            # from starting, only Drive connections from working.
            logger.error(f"GDRIVE_TOKEN_ENCRYPTION_KEYS holds an invalid Fernet key :: {e}")
            self._fernet = None

    @property
    def is_configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise RuntimeError("GDRIVE_TOKEN_ENCRYPTION_KEYS is not set")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, stored: str) -> str:
        if self._fernet is None:
            raise RuntimeError("GDRIVE_TOKEN_ENCRYPTION_KEYS is not set")
        try:
            return self._fernet.decrypt(stored.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Stored Google token could not be decrypted with any configured key") from exc


token_cipher = TokenCipher(os.environ.get("GDRIVE_TOKEN_ENCRYPTION_KEYS", ""))
connection_store = build_connection_store(gdrive_config.connection_store)


class TokenSource(ABC):

    @abstractmethod
    async def access_token(self, user: DriveUser, *, refresh: bool = False) -> str:
        """A Google access token for the user's Drive. ``refresh`` asks for a
        new one, after Google rejected the last. Raises DriveNotConnected or
        DriveAuthRequired when there is no usable connection."""
        raise NotImplementedError

    @abstractmethod
    async def is_connected(self, user: DriveUser) -> bool:
        raise NotImplementedError

    async def rejected(self, user: DriveUser, reason: str) -> None:
        """Google refused even a freshly obtained token."""
        return None


class GatewayOAuthTokenSource(TokenSource):
    """Access tokens refreshed from the connection the gateway's sign-in
    stored, cached encrypted in Redis until shortly before they expire."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def access_token(self, user: DriveUser, *, refresh: bool = False) -> str:
        principal = user.principal

        if refresh:
            await self.forget_access_token(principal)
        else:
            cached = await cache_redis.client.get(access_token_key(principal))
            if cached:
                try:
                    return token_cipher.decrypt(cached)
                except ValueError:
                    await self.forget_access_token(principal)

        connection = await connection_store.get(principal)
        if connection is None:
            raise DriveNotConnected("Google Drive is not connected")
        if connection.status != CONNECTED:
            raise DriveAuthRequired(connection.last_error or "Google Drive needs to be reconnected")

        try:
            refresh_token = token_cipher.decrypt(connection.refresh_token)
        except ValueError as exc:
            await self.mark_reauth_required(principal, str(exc))
            raise DriveAuthRequired(str(exc)) from exc

        try:
            tokens = await oauth_client.refresh(refresh_token)
        except DriveAuthError as exc:
            await self.mark_reauth_required(principal, str(exc))
            raise DriveAuthRequired("Google rejected the stored credentials") from exc

        await self.store_access_token(principal, tokens["access_token"], int(tokens.get("expires_in") or 3600))
        return tokens["access_token"]

    async def is_connected(self, user: DriveUser) -> bool:
        connection = await connection_store.get(user.principal)
        return connection is not None and connection.status == CONNECTED

    async def rejected(self, user: DriveUser, reason: str) -> None:
        await self.mark_reauth_required(user.principal, reason)

    async def store_access_token(self, principal: str, token: str, expires_in: int) -> None:
        ttl = expires_in - _ACCESS_TOKEN_MARGIN_SECONDS
        if ttl > 0:
            await cache_redis.client.set(access_token_key(principal), token_cipher.encrypt(token), ex=ttl)

    async def forget_access_token(self, principal: str) -> None:
        await cache_redis.client.delete(access_token_key(principal))

    async def mark_reauth_required(self, principal: str, reason: str) -> None:
        """Without this every search would retry a dead credential."""
        logger.warning(f"Google Drive connection needs reauthorization :: principal={principal}, reason={reason}")
        await connection_store.set_status(principal, REAUTH_REQUIRED, reason)
        await self.forget_access_token(principal)


gateway_oauth_tokens = GatewayOAuthTokenSource()
token_source: TokenSource = gateway_oauth_tokens


# ---------------------------------------------------------------------
# One request's Drive
# ---------------------------------------------------------------------

class DriveAccess:
    """One user's Drive for the length of one request.

    ``call`` runs a Drive operation with the current access token. A token
    Google rejects mid-request is replaced once and the operation retried; a
    second rejection means the connection has to be renewed.
    """

    def __init__(self, user: DriveUser):
        self.user = user
        self._token: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def principal(self) -> str:
        return self.user.principal

    async def token(self) -> str:
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    self._token = await token_source.access_token(self.user)
        return self._token

    async def call(self, operation: Callable[[str], Awaitable[T]]) -> T:
        token = await self.token()
        try:
            return await operation(token)
        except DriveAuthError:
            async with self._lock:
                # Another operation may have replaced it already.
                if self._token == token:
                    self._token = await token_source.access_token(self.user, refresh=True)
                token = self._token

        try:
            return await operation(token)
        except DriveAuthError as exc:
            await token_source.rejected(self.user, str(exc))
            raise DriveAuthRequired("Google rejected the Drive access token") from exc


@dataclass
class DriveContext:
    """One user's Drive and billing identity, for the length of one request.

    ``billing`` holds the siteid, agentid and sitename every model call's
    billing row is recorded under; ``timezone`` is the user's, for resolving
    "yesterday" or "last week" in a question.
    """

    drive: DriveAccess
    billing: dict = field(default_factory=dict)
    timezone: str = "UTC"

    @property
    def principal(self) -> str:
        return self.drive.principal
