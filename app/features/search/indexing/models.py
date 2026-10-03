from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class SearchMessage:
    """Backend-agnostic write-side document for a message.

    Each retriever backend's BulkWriter is responsible for serializing
    this into its own storage shape (OpenSearch document body, Redis
    hash fields, etc.) - this model carries no backend-specific logic.
    """

    # Tenant / workspace
    site_id: int

    # Conversation
    sid: int
    channel_type: str
    channel_name: Optional[str]

    # Message identity
    message_id: int
    parent_message_id: Optional[int]
    thread_root_id: int

    # Content
    text: str
    message_type: str

    # Author
    author_archive_id: int
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
    embedding: Optional[list[float]] = None
    embedding_version: Optional[str] = None
