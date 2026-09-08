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

from pydantic import BaseModel, Field

from app.ai import constants as ai_constants
from app.features.chat_summary.schemas import SummaryNLPExtractedData


class LunaRequest(BaseModel):
    agentid: str = Field(..., description="The ID/Email of the agent making the request")
    siteid: str | int = Field(..., description="The ID of the site making the request")
    sitename: str = Field(..., description="The name of the site making the request")
    user_name: Optional[str] = Field(None, description="Name of the user")
    archiveid: Optional[str] = Field(None, description="The archive ID of the user")
    sid: Optional[str] = Field(None, description="The chat ID of the conversation")
    smsgid: Optional[str] = Field(None, description="The message ID of the chat")
    user_query: str = Field(..., description="The user query")
    previous_response_id: Optional[str] = Field(None, description="The previous response ID")
    conversation_id: Optional[str] = Field(None, description="The conversation ID")


class UpgradeUserChatArgs(BaseModel):
    """No parameters — the model calls this tool with an empty argument object."""


class ReplyToThreadArgs(BaseModel):
    """No parameters — the model calls this tool with an empty argument object."""


class GeneralQueryArgs(BaseModel):
    message: str = Field(..., description="The response for the user requested general query.")


class ClarifyUserQueryArgs(BaseModel):
    message: str = Field(
        ..., description="One concise clarification question in the same language as the user."
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
    ] = Field(..., description="Primary document operation.")
    focus: Optional[str] = Field(
        None, description="Section, topic, page, fields, or criteria on which the operation should focus."
    )
    output_format: Optional[str] = Field(
        None, description="Requested output format, structure, tone, or length, or null."
    )


class OutOfScopeArgs(BaseModel):
    message: str = Field(..., description="A concise and polite explanation in the same language as the user.")


WEB_SEARCH_TOOL = {
    "type": "web_search",
}


INTENT_TOOL_SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    ai_constants.FUNCTION_GENERATE_SUMMARY: (
        SummaryNLPExtractedData,
        "Route requests to summarize messages from a direct chat, group chat, selected chat messages, or a chat topic. Also covers refinement or resummarization of a previous chat summary. Do not use for documents, calls, web pages, message rewriting, or reply generation.",
    ),
    ai_constants.FUNCTION_UPGRADE_USER_CHAT: (
        UpgradeUserChatArgs,
        "Route an existing user-written draft for grammar correction, clarity improvement, tone adjustment, length adjustment, or Markdown reformatting. Also acts as the fallback for a coherent standalone outgoing message when no clearer intent applies. Do not use for new content generation or replies that depend on another person's message.",
    ),
    ai_constants.FUNCTION_REPLY_TO_THREAD: (
        ReplyToThreadArgs,
        "Route requests to compose a new reply based on a selected message, selected thread, quoted message, or current message context. An existing thread is not required; this also supports creating the first reply to a top-level message. Do not use when the user already supplied a reply draft and only wants it improved you won't get messages in this stage so please consider if user want to reply to message in a thread or chat please trigger this function.",
    ),
    ai_constants.FUNCTION_GENERAL_QUERY: (
        GeneralQueryArgs,
        "Route answerable general requests, including factual and current-information questions, explanations, coding, debugging, how-to guidance, calculations, translation, summarization of directly pasted non-chat text, content generation, advice, recommendations, and normal conversation. The downstream pipeline, not the intent router, produces the answer and performs any required external lookup.",
    ),
    ai_constants.FUNCTION_CLARIFY_USER_QUERY: (
        ClarifyUserQueryArgs,
        "Call only when essential information, target context, or a single primary intent cannot be determined. Ask one concise question that collects all essential missing information. Do not call merely because a request is broad or requires external information.",
    ),
    ai_constants.FUNCTION_DOCUMENT_INTELLIGENCE: (
        DocumentIntelligenceArgs,
        "Route requests whose answers depend on selected or attached documents or supported files, including summarization, question answering, extraction, comparison, explanation, translation, and content analysis. Do not use when no relevant document is available or when the user asks to mutate the actual stored file.",
    ),
    ai_constants.FUNCTION_OUT_OF_SCOPE: (
        OutOfScopeArgs,
        "Call when the user requests an unsupported application operation, inaccessible private-data action, unsupported content source, unavailable feature, or policy-restricted operation. Do not use for answerable general questions or requests that only need clarification.",
    ),
}

