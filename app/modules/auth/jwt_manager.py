import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional, Tuple
from dotenv import load_dotenv

import jwt

load_dotenv()
logger = logging.getLogger(__name__)

# Constants & Configurations
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ISSUER = os.getenv("JWT_ISSUER", "streams-ai-agent-gateway")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "ai-api")
JWT_LEEWAY = int(os.getenv("JWT_LEEWAY", "10"))


def _format_pem_key(key: str) -> str:
    """Ensures literal escaped newlines '\\n' in environment variables are converted to actual newlines."""
    if not key:
        return ""
    return key.replace("\\n", "\n").strip()


class JWTManager:
    def __init__(self):
        self.private_key_pem: str = _format_pem_key(os.getenv("JWT_PRIVATE_KEY", ""))
        self.public_key_pem: str = _format_pem_key(os.getenv("JWT_PUBLIC_KEY", ""))

        if not self.private_key_pem or not self.public_key_pem:
            logger.warning(
                "JWT_PRIVATE_KEY or JWT_PUBLIC_KEY environment variable is missing. "
                "Ensure RS256 RSA keys are set in .env file."
            )

    def create_access_token(self, user_id: str, extra_claims: Optional[dict] = None) -> str:
        """
        Creates a short-lived RS256 JWT access token.
        Claims: sub, type, iss, aud, iat, exp
        """
        now = int(time.time())
        exp = now + (ACCESS_TOKEN_EXPIRE_MINUTES * 60)

        payload = {
            "sub": str(user_id),
            "type": "access",
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": exp,
        }

        if extra_claims:
            payload.update(extra_claims)

        encoded_jwt = jwt.encode(
            payload,
            self.private_key_pem,
            algorithm="RS256"
        )
        return encoded_jwt

    def decode_access_token(self, token: str) -> Optional[dict]:
        """
        Decodes and verifies an RS256 JWT access token.
        Checks signature using public key, expiration, issuer, audience, and type.
        """
        payload = jwt.decode(
            token,
            self.public_key_pem,
            algorithms=["RS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            leeway=JWT_LEEWAY,
        )
        
        if payload.get("type") != "access":
            raise jwt.InvalidTokenError("Invalid token type")
            
        return payload

    def create_refresh_token(self, username: str, authkey: Optional[str] = None) -> str:
        """
        Generates a stateless, cryptographically signed refresh token (<payload_b64>.<hmac>).
        Stateless & works across all server instances without DB interaction or process memory.
        """
        now = int(time.time())
        expires_at = now + (REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600)
        nonce = secrets.token_hex(16)

        data = {
            "sub": username,
            "authkey": authkey or "",
            "exp": expires_at,
            "iat": now,
            "nonce": nonce,
        }
        
        json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(json_bytes).decode("utf-8").rstrip("=")

        # Compute HMAC SHA256 signature
        signature = hmac.new(
            JWT_SECRET_KEY.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return f"{payload_b64}.{signature}"

    def verify_and_rotate_refresh_token(self, old_refresh_token: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Validates an existing HMAC signed refresh token statelessly across multiple boxes:
        - Verifies signature using shared JWT_SECRET_KEY
        - Checks expiration
        - Performs rotation: creates a new refresh token and access JWT
        Returns: (new_access_token, new_refresh_token, error_message)
        """
        try:
            parts = old_refresh_token.split(".")
            if len(parts) != 2:
                return None, None, "Invalid refresh token format"

            payload_b64, signature = parts[0], parts[1]

            # Verify HMAC SHA256 signature
            expected_sig = hmac.new(
                JWT_SECRET_KEY.encode("utf-8"),
                payload_b64.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_sig):
                return None, None, "Invalid refresh token signature"

            # Decode payload
            padding = "=" * (-len(payload_b64) % 4)
            json_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
            data = json.loads(json_bytes.decode("utf-8"))

            now = int(time.time())
            if data.get("exp", 0) < now:
                return None, None, "Refresh token has expired"

            username = data.get("sub")
            authkey = data.get("authkey") or None

            if not username:
                return None, None, "Invalid refresh token payload"

            # Rotation: Issue new access token and new stateless refresh token
            new_access_token = self.create_access_token(user_id=username)
            new_refresh_token = self.create_refresh_token(username=username, authkey=authkey)

            logger.info(f"Successfully statelessly rotated refresh token for user: {username}")
            return new_access_token, new_refresh_token, None

        except Exception as e:
            logger.warning(f"Failed to verify refresh token: {str(e)}")
            return None, None, f"Invalid refresh token: {str(e)}"


# Singleton instance
jwt_manager = JWTManager()
