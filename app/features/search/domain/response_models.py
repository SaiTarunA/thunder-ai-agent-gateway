from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.features.search.domain.enums import (
    ChannelType,
    ContentType,
    RetrievalStatus,
)


class MessageSearchResult(BaseModel):
    message_id: int
    sid: int
    text: str
    author_archive_id: int
    author_name: Optional[str] = None
    author_username: Optional[str] = None
    channel_name: Optional[str] = None
    channel_type: Optional[ChannelType] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    thread_root_id: Optional[int] = None
    parent_message_id: Optional[int] = None
    is_thread_reply: bool = False
    is_edited: bool = False
    is_pinned: bool = False


class SearchCandidateResponse(BaseModel):
    document_id: str
    score: Optional[float] = None
    data: Optional[MessageSearchResult] = None


class ContentTypeResultResponse(BaseModel):
    status: RetrievalStatus
    error: Optional[str] = None
    candidates: list[SearchCandidateResponse] = []


class SearchResponseMetadata(BaseModel):
    next_cursor: Optional[str] = None


class SearchResponse(BaseModel):
    ok: bool
    results: dict[ContentType, ContentTypeResultResponse]
    response_metadata: SearchResponseMetadata
