from typing import Optional

from pydantic import BaseModel, Field

from app.ai import ai_constants
from app.features.search.domain.enums import ChannelType
from app.features.search.domain.models import CommonRequest


class ContextualQueryArgs(BaseModel):
    """No parameters - call this when the query needs a search-grounded answer, not just a list of results."""


class NotContextualQueryArgs(BaseModel):
    """No parameters - call this when the query is a simple lookup (a name, a term, an exact phrase) with nothing to synthesize."""


CLASSIFY_TOOL_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    ai_constants.FUNCTION_CONTEXTUAL_QUERY: (
        ContextualQueryArgs,
        "The query asks something answerable in prose from matching messages - a question, or a request for a decision, status, or reason.",
    ),
    ai_constants.FUNCTION_NOT_CONTEXTUAL_QUERY: (
        NotContextualQueryArgs,
        "The query is a bare name, keyword, phrase, filename, ID, or URL fragment - a list of matches is the correct result, not prose.",
    ),
}


class CitationRef(BaseModel):
    message_id: int = Field(
        ...,
        description="The message_id of a search result used to support the answer",
    )
    snippet: str = Field(
        ...,
        description="The exact portion of that message's text that supports the answer",
    )


class ProvideAnswerArgs(BaseModel):
    answer: str = Field(
        ...,
        description="The complete answer to the user's query, grounded only in the provided search results",
    )
    citations: list[CitationRef] = Field(
        ...,
        description="Every message used to support the answer; at least one entry",
    )


class InsufficientContextArgs(BaseModel):
    """No parameters - call this when the provided search results don't contain enough information to answer confidently."""


ANSWER_TOOL_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    ai_constants.FUNCTION_PROVIDE_SEARCH_ANSWER: (
        ProvideAnswerArgs,
        "The provided search results support answering the query - write a cited answer.",
    ),
    ai_constants.FUNCTION_INSUFFICIENT_SEARCH_CONTEXT: (
        InsufficientContextArgs,
        "The provided search results do not contain enough information to answer confidently.",
    ),
}


class SearchAnswerRequest(CommonRequest):
    """Body for the Global Search AI-answer endpoint. Deliberately narrower
    than SearchContext: no retrieval_methods (forced to hybrid server-side,
    not caller-controlled), no cursor/sort (one-shot synthesis, not a
    paginated list), no content_types (v1 scope is messages only, same as
    Global Search / AI Search per the earlier retrieval-method decision)."""

    query: str = Field(..., description="The user's search box query")
    channel_types: Optional[list[ChannelType]] = Field(
        default_factory=lambda: [ChannelType.DM, ChannelType.PRIVATE_CHANNEL],
        description="Channel types to search within",
    )
    before: Optional[float] = Field(
        None,
        description="UNIX timestamp filter. If present, filters for results before this date.",
    )
    after: Optional[float] = Field(
        None,
        description="UNIX timestamp filter. If present, filters for results after this date.",
    )
    limit: Optional[int] = Field(
        10,
        description="Max messages to retrieve for grounding the answer.",
    )
    modifiers: Optional[str] = Field(
        None,
        description="A string containing only modifiers in the format of modifier:value. Search results returned will match the modifier value. For now modifiers only affect term clauses. Not Used as of now",
    )


class SearchAnswerResponse(BaseModel):
    has_answer: bool
    answer: Optional[str] = None
    citations: list[CitationRef] = []
    reason: Optional[str] = Field(
        None,
        description='One of "not_contextual", "no_results", "insufficient_context", "error" when has_answer is false.',
    )
