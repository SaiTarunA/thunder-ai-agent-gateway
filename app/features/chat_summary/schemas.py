from datetime import datetime, timezone
from typing import Literal, Optional
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

EXCLUDED_PAYLOAD_KEYS = frozenset({"start", "end", "extra_data", "condensed", "unavailable"})

# Keys of a conversation payload that carry the user's requested focus. Only these (not the
# style keys) are passed to the intermediate "working notes" calls of the batched path.
FOCUS_KEYS = ("context", "topic_name", "buddy_name")

DEFAULT_BATCHING_SETTINGS = {
    "single_pass_max_input_tokens": 500_000,
    "batch_target_input_tokens": 250_000,
    "context_safety_margin": 0.10,
    "max_parallel_batches": 4,
    "batch_max_output_tokens": 8000,
    "batch_temperature": 0.3,
    "batch_attempts": 2,
    "max_collapse_rounds": 8,
}

TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")

# Boundary strengths. Higher wins when several cut points sit inside the tolerance window.
STRENGTH_MONTH = 4
STRENGTH_WEEK = 3
STRENGTH_DAY = 2
STRENGTH_IDLE_GAP = 1
STRENGTH_NONE = 0

IDLE_GAP_SECONDS = 30 * 60

# How far (as a fraction of the ideal batch size) a cut may move to reach a stronger boundary.
BALANCE_TOLERANCE = 0.30

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
        None, ge=1, le=10000, description="Requested number of messages (maximum 10,000), such as 10 for 'last 10 messages'; otherwise null."
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

    @property
    def should_store_summary(self) -> bool:
        """Returns False if summary is specific to a topic, buddy, unread messages or message count."""
        return not bool(
            self.topic_name
            or self.buddy_name
            or self.unread_messages
            or self.message_count
        )


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
    id: Optional[int] = None
    archive_id: Optional[int] = None
    source_type: Optional[str] = None
    ref_id: Optional[int] = None
    site_id: Optional[int] = None
    sid: Optional[int] = None
    summary: str
    start_date: datetime
    end_date: datetime
    extra_data: dict | str | None = None

    @field_validator("start_date", "end_date", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        elif v:
            return v.astimezone(timezone.utc)
        return v

    @property
    def parsed_extra_data(self) -> dict:
        if isinstance(self.extra_data, dict):
            return self.extra_data
        if isinstance(self.extra_data, str):
            try:
                import json
                return json.loads(self.extra_data)
            except Exception:
                return {}
        return {}

@dataclass
class TokenLimits:
    """Input-token limits for one prompt (`instructions` fixed, `user_query` variable)."""
    single_pass: int  # a query up to this size is sent in one call
    batch_target: int  # each batch of a split query stays at or below this size


@dataclass
class ConversationBatch:
    conversations: list[dict]
    tokens: int
    start: Optional[str] = None  # timestamp string of the first message
    end: Optional[str] = None  # timestamp string of the last message

    @property
    def date_range(self) -> str:
        """Human-readable period label, in the same timezone as the message timestamps."""
        start_day = (self.start or "")[:10]
        end_day = (self.end or "")[:10]
        if not start_day and not end_day:
            return ""
        if start_day == end_day or not end_day:
            return start_day
        if not start_day:
            return end_day
        return f"{start_day} to {end_day}"

