from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class SearchMessage:
    # Tenant / workspace
    site_id: int
    site_name: str

    # Conversation
    sid: int
    conversation_type: str
    conversation_name: Optional[str]

    # Message identity
    message_id: int
    parent_message_id: Optional[int]
    thread_root_id: int

    # Content
    text: str
    message_type: str

    # Author
    author_archive_id: int
    author_account_id: Optional[int]
    author_username: str
    author_name: Optional[str]

    # Timestamps
    created_at: datetime
    updated_at: datetime

    # Message state
    is_thread_reply: bool
    is_deleted: bool
    is_edited: bool
    is_pinned: bool

    # Semantic search
    embedding: Optional[list[float]]
    embedding_version: Optional[str] = None

    def to_opensearch(self) -> dict:
        return {
            "site_id": self.site_id,
            "site_name": self.site_name,

            "sid": self.sid,
            "conversation_type": self.conversation_type,
            "conversation_name": self.conversation_name,

            "message_id": self.message_id,
            "parent_message_id": self.parent_message_id,
            "thread_root_id": self.thread_root_id,

            "text": self.text,
            "message_type": self.message_type,

            "author_archive_id": self.author_archive_id,
            "author_account_id": self.author_account_id,
            "author_username": self.author_username,
            "author_name": self.author_name,

            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),

            "is_thread_reply": self.is_thread_reply,
            "is_deleted": self.is_deleted,
            "is_edited": self.is_edited,
            "is_pinned": self.is_pinned,

            "embedding": self.embedding,
            "embedding_version": self.embedding_version,
        }