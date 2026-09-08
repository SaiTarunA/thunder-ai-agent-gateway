from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SummaryNLPExtractedData(BaseModel):
    """Extracted parameters for a chat-summary request.

    This is also the argument schema for intent detection's `generate_summary` tool
    (see `app.features.intent_detection.schemas.INTENT_TOOL_SCHEMAS`) — one model
    drives both the OpenAI tool definition and the validation applied to whatever the
    model returns, so they can't drift apart the way the old hand-written JSON Schema
    block and this model had: `group_name` was present in that JSON Schema but missing
    from this model, so the model silently discarded any `group_name` the AI supplied.
    Added below. `response_text` was present here but unused by any tool schema or by
    this feature's own logic — removed as dead.
    """

    start_date: Optional[str] = Field(
        None, description="Calculated summary start timestamp in 'YYYY-MM-DD HH:MM:SS' format, or null."
    )
    end_date: Optional[str] = Field(
        None, description="Calculated summary end timestamp in 'YYYY-MM-DD HH:MM:SS' format, or null."
    )
    message_count: Optional[int] = Field(
        None, ge=1, description="Requested number of messages, such as 10 for 'last 10 messages'; otherwise null."
    )
    unread_messages: bool = Field(False, description="True when summarizing unread messages.")
    is_resummarization_request: bool = Field(
        False, description="True when refining, repeating, shortening, expanding, or recreating a previous summary."
    )
    context: Optional[str] = Field(
        None,
        description="Focus, exclusions, requested contents, accuracy constraints, formatting instructions, or other summary requirements.",
    )
    summary_type: Optional[Literal["brief", "short", "long"]] = Field(
        None, description="Requested summary-detail level."
    )
    tone: Optional[str] = Field(None, description="Requested summary tone, or null.")
    buddy_name: Optional[str] = Field(None, description="Named buddy, or null.")
    group_name: Optional[str] = Field(None, description="Named group, or null.")
    topic_name: Optional[str] = Field(None, description="Named topic or subject focus, or null.")


class StreamsUserChatData(BaseModel):
    message: str
    messagetime: datetime
    archiveid: int
    sid: int
    accountid: int
    username: str
    siteid: int
    firstname: str
    lastname: str


class StoredChatSummaryData(BaseModel):
    summary: str
    start_date: datetime
    end_date: datetime
    sid: int
    extra_data: dict | str | None = None
