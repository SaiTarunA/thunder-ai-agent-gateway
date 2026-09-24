"""Intent-detection request schema and per-intent tool argument schemas.

`INTENT_TOOL_SCHEMAS` is the single source of truth for intent detection's
function-calling tools: each entry's Pydantic model both generates that function's
OpenAI tool definition (via `app.ai.tools.pydantic_model_to_openai_tool`) and
validates whatever arguments the model returns for it (via
`app.ai.tools.parse_tool_call_arguments`) — this is what replaces LangChain's
`bind_tools()` here without adding LangChain: one model drives both sides, so they
can never drift apart the way the old hand-written ~200-line JSON Schema block in
`providers/open_ai/constants.py` could (and did — see `SummaryNLPExtractedData`'s
docstring for the concrete case it caused).

Every branch is now validated the same way. Previously only `generate_summary`'s
arguments were parsed into a Pydantic model before use; the other six functions had
their raw, unvalidated arguments dict passed straight through.
"""

from typing import Literal, Optional
from enum import StrEnum
from pydantic import BaseModel, Field

from app.ai import ai_constants
from app.features.chat_summary.schemas import SummaryNLPExtractedData
from app.features.search.domain.models import ContentType, ChannelType, SortType, SortDirectionType, RetrievalMethodType
from pydantic import model_validator


class LunaRequest(BaseModel):
    agentid: str = Field(..., description="The ID/Email of the agent making the request")
    siteid: str | int = Field(..., description="The ID of the site making the request")
    sitename: str = Field(..., description="The name of the site making the request")
    user_name: Optional[str] = Field(None, description="Name of the user")
    archiveid: Optional[str] = Field(None, description="The archive ID of the user")
    x_auth_token: Optional[str] = Field(None, description="The x-auth token of the user")
    authkey: Optional[str] = Field(None, description="The authkey of the user")
    sid: Optional[str] = Field(None, description="The chat ID of the conversation")
    smsgid: Optional[str] = Field(None, description="The message ID of the chat")
    user_query: str = Field(..., description="The user query")
    timezone: Optional[str] = Field(None, description="The timezone of the user")
    previous_response_id: Optional[str] = Field(None, description="The previous response ID")
    conversation_id: Optional[str] = Field(None, description="The conversation ID")


class UpgradeUserChatArgs(BaseModel):
    """No parameters — the model calls this tool with an empty argument object."""
 
class ProcessThreadArgs(BaseModel):
    category: Literal[
        ai_constants.ThreadCategory.SUMMARIZE,
        ai_constants.ThreadCategory.GENERATE_REPLY,
    ] = Field(
        ...,
        description=(
            "'generate_reply' to compose a reply to someone else's message or thread; "
            "'summarize' to summarize a thread (a parent message with its replies or comments)."
        ),
    )
 
 
class GeneralQueryArgs(BaseModel):
    message: str = Field(
        ...,
        description=(
            "The complete, direct, final answer shown to the user, in the user's language, honouring any requested "
            "length, format, and tone. Never a placeholder, status message, or meta-statement such as "
            "'Searching...' or 'i process your request by websearch tool'."
        ),
    )
 
 
class ClarifyUserQueryArgs(BaseModel):
    message: str = Field(
        ...,
        description=(
            "Exactly one concise question in the user's language that collects all missing information, "
            "offering concrete options when helpful (e.g. 'today, the last 7 days, the last 50 messages, or unread?')."
        ),
    )
 
 
class DocumentIntelligenceArgs(BaseModel):
    operation: Literal[
        "summarize",
        "question_answering",
        "extract",
        "compare",
        "analyze",
        "explain",
        "classify",
        "translate",
        "rewrite",
        "other",
    ] = Field(..., description="Primary operation on the document's content.")
    focus: Optional[str] = Field(
        None,
        description="Section, page, topic, fields, or criteria to concentrate on; null for the whole document.",
    )
    output_format: Optional[str] = Field(
        None,
        description="Requested format, structure, tone, length, or target language (for translate); null if none.",
    )
 
 
class OutOfScopeArgs(BaseModel):
    message: str = Field(
        ...,
        description=(
            "1-3 polite sentences in the user's language: what the user asked, that it isn't possible here, "
            "and the closest supported alternative (e.g. 'I can't send messages, but I can draft the reply for you.')."
        ),
    )
 

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
    before: Optional[str] = Field(
        None,
        description="Date filter as 'YYYY-MM-DD HH:MM:SS' string. If present, filters for results before this date.",
    )
    after: Optional[str] = Field(
        None,
        description="Date filter as 'YYYY-MM-DD HH:MM:SS' string. If present, filters for results after this date.",
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
 
WEB_SEARCH_TOOL = {
    "type": "web_search",
}
 
 
INTENT_TOOL_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    ai_constants.FUNCTION_GENERATE_SUMMARY: (
        SummaryNLPExtractedData,
        "Recap chat messages over a scope: a time range (start_date/end_date), the last N messages (message_count), "
        "unread messages (unread_messages), or selected messages; scopes can be combined. Also refines a previous chat "
        "summary (is_resummarization_request). Fill only the fields the user supplied; leave the rest null/false. "
        f"If no scope is given, call {ai_constants.FUNCTION_CLARIFY_USER_QUERY} instead. Not for threads, documents, "
        "pasted text, calls, or finding a specific message.",
    ),
    ai_constants.FUNCTION_UPGRADE_USER_CHAT: (
        UpgradeUserChatArgs,
        "Improve the user's own message draft (from user_text or the composer): polish, fix grammar, rephrase, change "
        "tone, shorten, expand, or format it for sending. Use for (a) an explicit edit instruction with a draft, or "
        "(b) bare text with no instruction that reads as a message to another person rather than a question or "
        "request to the assistant. Never for questions, even with typos or broken grammar. Call with empty arguments.",
    ),
    ai_constants.FUNCTION_PROCESS_THREAD: (
        ProcessThreadArgs,
        "Compose a reply to someone else's message or thread (category 'generate_reply'), or summarize a thread - a "
        "parent message with its replies or comments (category 'summarize'). The target message or thread is resolved "
        "downstream, so call this even when nothing is selected. If the user already wrote the reply and wants it "
        f"improved, use {ai_constants.FUNCTION_UPGRADE_USER_CHAT} instead.",
    ),
    ai_constants.FUNCTION_GENERAL_QUERY: (
        GeneralQueryArgs,
        "Answer anything directed at the assistant: knowledge, current events and live data (call web_search first), "
        "explanations, app how-to questions, coding, calculations, advice, comparisons, translation or summarization "
        "of pasted non-chat text, emails and other content written from scratch, greetings, and thanks - including "
        "questions with typos or broken grammar. 'message' must hold the complete final answer, never a status "
        "message or placeholder.",
    ),
    ai_constants.FUNCTION_CLARIFY_USER_QUERY: (
        ClarifyUserQueryArgs,
        "Ask one concise question when a feature's required input is missing (a chat summary with no time range, "
        "message count, or unread scope; a referenced document that is not attached; an edit request with no text; "
        "a search with nothing to look for), when a reference is ambiguous, when two independent intents are "
        "requested, or when the input is unintelligible. Not for requests that are broad, informal, contain typos, "
        "or need web search.",
    ),
    ai_constants.FUNCTION_DOCUMENT_INTELLIGENCE: (
        DocumentIntelligenceArgs,
        "Work with the content of a document or attachment available in context: summarize, answer questions from, "
        "extract, compare, analyze, explain, classify, translate, or rewrite it, including images, slides, and sheets. "
        f"Referenced but missing document -> {ai_constants.FUNCTION_CLARIFY_USER_QUERY}. Changing the stored file -> "
        f"{ai_constants.FUNCTION_OUT_OF_SCOPE}.",
    ),
    ai_constants.FUNCTION_INTENT_SEARCH: (
        SearchContext,
        "Find specific messages, facts, links, files, documents, channels, or people in the user's accessible "
        "conversations ('find', 'where did', 'when did', 'who said', 'what did X say about Y'). Fill every field: "
        "query, content_types, channel_types, after/before as date strings 'YYYY-MM-DD HH:MM:SS' in user timezone (after = earlier bound), "
        "include_context_messages, limit, sort, sort_direction, retrieval_methods; cursor and modifiers null. Not for "
        "recaps over a time range, general knowledge, or conversations outside the accessible context.",
    ),
    ai_constants.FUNCTION_OUT_OF_SCOPE: (
        OutOfScopeArgs,
        "The user wants something no function can do: send, schedule, forward, delete, edit, pin, or react to real "
        "messages; control calls; change settings; mutate files; summarize a call with no transcript; search outside "
        "the accessible context; or a policy-restricted action. Also used when a supported request is bundled with an "
        "unsupported action. 'message' says it can't be done here and offers the closest supported alternative. Not "
        "for how-to questions about the app.",
    ),
}
