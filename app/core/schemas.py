from pydantic import BaseModel, Field
from typing import Literal, Optional, Union, Annotated
from app.core.enums import ChatRequestType, DraftMessageToneType

# ===================== Fields for specific request types =====================


class DraftMessageFields(BaseModel):
    tone: Optional[DraftMessageToneType] = Field(
        default=None,
        description="Tone of the message to be drafted.",
    )
    language: Optional[str] = Field(
        default=None, description="Language of the message to be drafted."
    )
    length: Optional[str] = Field(
        default=None, description="Length of the message to be drafted."
    )


class ThreadReplyFields(BaseModel):
    main_message: Optional[str] = Field(
        default=None, description="The main message that the user is replying to."
    )
    recent_thread_messages: Optional[list[str]] = Field(
        default=None,
        description="List of recent messages in the thread for context.",
    )
    thread_summary: Optional[str] = Field(
        default=None, description="Summary of the thread for context."
    )


# ===================== Request types =====================


class CommonChatRequestFields(BaseModel):
    # Common fields for all request types
    sid: str = Field(..., description="Unique ID for the chat. Be it DM/group chat.")
    smsgid: str = Field(
        ..., description="Unique ID for the message. Be it DM/group chat/thread."
    )
    user_text: str = Field(..., description="Text provided by the user.")
    commented_via: Optional[str] = Field(
        default=None,
        description="Thread main message ID. The message that the user is replying to.",
    )
    is_ai_followup: bool = Field(
        default=False,
        description="Whether the request is a follow-up to a previous AI message.",
    )


# ===================== Request variants =====================


class DraftMessageRequest(CommonChatRequestFields):
    request_type: Literal[ChatRequestType.DRAFT_MESSAGE]
    additional_data: DraftMessageFields = Field(default_factory=DraftMessageFields)


class ThreadReplyRequest(CommonChatRequestFields):
    request_type: Literal[ChatRequestType.THREAD_REPLY]
    additional_data: ThreadReplyFields = Field(default_factory=ThreadReplyFields)


# ===================== Request types =====================

ChatRequest = Annotated[
    Union[DraftMessageRequest, ThreadReplyRequest],
    Field(discriminator="request_type"),
]

# ===================== Data models for specific request types =====================


class DraftMessageData(CommonChatRequestFields, DraftMessageFields):
    pass


class ThreadReplyData(CommonChatRequestFields, ThreadReplyFields):
    pass
