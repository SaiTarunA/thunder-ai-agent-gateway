from pydantic import BaseModel, Field


class MessageIndexEventRequest(BaseModel):
    """Body for both the upsert (create/edit) and delete message events.

    Identifiers only, by design - the handler always re-fetches current
    state from MySQL rather than trusting a payload snapshot, so delivery
    order across worker processes can't produce a stale result."""

    site_id: int = Field(..., description="Site ID the message belongs to")
    sid: int = Field(..., description="Stream ID the message belongs to")
    message_id: int = Field(..., description="Message ID (smsgid)")


class StreamIndexEventRequest(BaseModel):
    """Body for stream-level events: type changed (reindex) or deleted."""

    site_id: int = Field(..., description="Site ID the stream belongs to")
    sid: int = Field(..., description="Stream ID")


class IndexEventAccepted(BaseModel):
    accepted: bool = True
