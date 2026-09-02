from enum import Enum

class ChatRequestType(str, Enum):
    """Enum for chat request types."""
    DRAFT_MESSAGE = "draft_message"
    THREAD_REPLY = "thread_reply"
    FREE_TEXT = "free_text"


class IntentType(str, Enum):
    """Enum for intent types."""
    DRAFT_MESSAGE = "draft_message"
    THREAD_REPLY = "thread_reply"
    THREAD_SUMMARIZATION = "thread_summarization"
    CHAT_SUMMARIZATION = "chat_summarization"

class DraftMessageToneType(str, Enum):
    """Enum for draft message tone types."""
    FRIENDLY = "friendly"
    NEUTRAL = "neutral"
    PROFESSIONAL = "professional"