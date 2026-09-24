from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field, model_validator
from typing import Any, Optional
from app.features.search.domain.enums import (
    ContentType,
    RetrievalStatus,
    SortType,
    SortDirectionType,
    ChannelType,
    RetrievalMethodType,
)


class CommonRequest(BaseModel):
    agentid: str = Field(
        ..., description="The ID/Email of the agent making the request"
    )
    siteid: str | int = Field(..., description="The ID of the site making the request")
    sitename: str = Field(..., description="The name of the site making the request")
    user_name: Optional[str] = Field(None, description="Name of the user")
    archiveid: Optional[str] = Field(None, description="The archive ID of the user")
    x_auth_token: Optional[str] = Field(
        None, description="The x-auth token of the user"
    )
    authkey: Optional[str] = Field(None, description="The authkey of the user")
    timezone: Optional[str] = Field(None, description="The timezone of the user")


class SearchContext(BaseModel):
    query: str = Field(
        ...,
        description="User prompt or search query",
    )
    content_types: Optional[list[ContentType]] = Field(
        default_factory=lambda: [ContentType.MESSAGES],
        description=f"Content types to include, a comma-separated list of any combination of {', '.join([ct.value for ct in ContentType])}",
    )
    channel_types: Optional[list[ChannelType]] = Field(
        default_factory=lambda: [ChannelType.DM, ChannelType.PRIVATE_CHANNEL],
        description=f"Mix and match channel types by providing a comma-separated list of any combination of {', '.join([ct.value for ct in ChannelType])}",
    )
    before: Optional[float] = Field(
        None,
        description="UNIX timestamp filter. If present, filters for results before this date.",
    )
    after: Optional[float] = Field(
        None,
        description="UNIX timestamp filter. If present, filters for results after this date.",
    )
    include_context_messages: Optional[bool] = Field(
        False,
        description="Whether to include context messages surrounding the main message result. Defaults to false if unspecified.",
    )
    cursor: Optional[str] = Field(
        None,
        description="The cursor returned by the API. Leave this blank for the first request and use this to get the next page of results.",
    )
    limit: Optional[int] = Field(
        20,
        description="Number of results to return, up to a max of 20. Defaults to 20.",
    )
    sort: Optional[SortType] = Field(
        SortType.SCORE,
        description="The field to sort the results by. Defaults to score. Can be one of: score, timestamp",
    )
    sort_direction: Optional[SortDirectionType] = Field(
        SortDirectionType.DESC,
        description="The direction to sort the results by. Defaults to desc.",
    )
    modifiers: Optional[str] = Field(
        None,
        description="A string containing only modifiers in the format of modifier:value. Search results returned will match the modifier value. For now modifiers only affect term clauses. Not Used as of now",
    )
    retrieval_methods: Optional[list[RetrievalMethodType]] = Field(
        default_factory=lambda: [RetrievalMethodType.LEXICAL],
        description=f"Retrieval methods to include, a comma-separated list of any combination of {', '.join([st.value for st in RetrievalMethodType])}",
    )

    @model_validator(mode="after")
    def check_limit(self):
        """Validates that the limit does not exceed the maximum page limit."""
        from app.features.search.config import SearchConfig

        if self.limit is not None and self.limit > SearchConfig.max_page_limit:
            raise ValueError(
                f"Limit cannot exceed {SearchConfig.max_page_limit}"
            )

        return self


class SearchContextRequest(CommonRequest, SearchContext):
    pass


# --------------------- query processing ---------------------


@dataclass(frozen=True)
class ProcessedQuery:
    raw_query: str

    normalized_query: str

    lexical_query: str

    embedding_query: str


# --------------------- retrieval planning ---------------------


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    methods: tuple[RetrievalMethodType, ...]
    limit: int
    pagination_depth: int


# --------------------- authorization ---------------------


@dataclass(frozen=True, slots=True)
class SearchAccess:
    site_id: int
    allowed_sids: frozenset[int] | None = None
    contributor_thread_root_ids: frozenset[int] | None = None


# --------------------- filter resolver ---------------------


@dataclass(frozen=True, slots=True)
class SearchModifiers:
    author_ids: frozenset[int] | None = None
    sids: frozenset[int] | None = None


@dataclass(frozen=True, slots=True)
class SearchFilters:
    access: SearchAccess

    channel_types: tuple[ChannelType, ...] = ()
    after: datetime | None = None
    before: datetime | None = None

    modifiers: SearchModifiers | None = None


# --------------------- retrieval planner ---------------------


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    query_vector: list[float] | None
    filters: SearchFilters
    methods: tuple[RetrievalMethodType, ...]
    content_type: ContentType
    limit: int
    pagination_depth: int
    sort: SortType
    sort_direction: SortDirectionType
    search_after: tuple[Any, ...] | None = None

    @property
    def is_lexical(self) -> bool:
        return self.methods == (RetrievalMethodType.LEXICAL,)

    @property
    def is_semantic(self) -> bool:
        return self.methods == (RetrievalMethodType.SEMANTIC,)

    @property
    def is_hybrid(self) -> bool:
        return (
            RetrievalMethodType.LEXICAL in self.methods
            and RetrievalMethodType.SEMANTIC in self.methods
        )

    def __post_init__(self) -> None:
        if not self.methods:
            raise ValueError("At least one retrieval method is required")

        if any(
            method
            not in (
                RetrievalMethodType.LEXICAL,
                RetrievalMethodType.SEMANTIC,
            )
            for method in self.methods
        ):
            raise ValueError(f"Unsupported retrieval methods: {self.methods}")


# --------------------- retriever ---------------------


@dataclass(frozen=True, slots=True)
class MessagesData:
    message_id: int
    sid: int
    text: str

    author_archive_id: int
    author_name: str | None = None
    author_username: str | None = None

    conversation_name: str | None = None
    conversation_type: ChannelType | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    thread_root_id: int | None = None
    parent_message_id: int | None = None

    is_thread_reply: bool = False
    is_edited: bool = False
    is_pinned: bool = False


@dataclass(frozen=True, slots=True)
class FilesData:
    pass


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    document_id: str
    content_type: ContentType
    rank: int
    data: MessagesData | FilesData | None = None
    raw_score: float | None = None
    sort_values: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    document_id: str
    content_type: ContentType
    data: MessagesData | FilesData | None = None
    score: float | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    methods: tuple[RetrievalMethodType, ...]
    status: RetrievalStatus
    candidates: list[RetrievalCandidate]
    latency_ms: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SearchCursor:
    query_hash: str
    sort: SortType
    sort_direction: SortDirectionType
    pagination_depth: int
    search_after: dict[
        ContentType,
        tuple[Any, ...] | None,
    ]
