from typing import Optional
from pydantic import BaseModel, Field


class GenerateTokensRequest(BaseModel):
    username: Optional[str] = Field(None, description="Username / agent ID")
    authkey: Optional[str] = Field(None, description="Authentication key")
    refresh_token: Optional[str] = Field(None, description="Refresh token for token rotation")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900
