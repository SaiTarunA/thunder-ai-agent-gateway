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
    previous_response_id: Optional[str] = Field(None, description="The previous response ID")
    conversation_id: Optional[str] = Field(None, description="The conversation ID")


class UpgradeUserChatArgs(BaseModel):
    """No parameters — the model calls this tool with an empty argument object."""


# Alias for backward compatibility
ThreadArgs = ai_constants.ThreadCategory


class ProcessThreadArgs(BaseModel):
    category: Literal[
        ai_constants.ThreadCategory.SUMMARIZE,
        ai_constants.ThreadCategory.GENERATE_REPLY,
    ] = Field(..., description="Category of the thread request: 'summarize' or 'generate_reply'")


class GeneralQueryArgs(BaseModel):
    message: str = Field(
        ...,
        description="The complete, direct, and substantive final answer to the user's query. Never a placeholder, status message, or meta-statement like 'i process your request by websearch tool'.",
    )


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
        "Route ONLY when the user explicitly asks to edit, rewrite, rephrase, polish, format, or adjust the tone of a message draft, or sends a standalone outgoing communication draft intended for a recipient (e.g. 'hey can u send the file'). DO NOT use if the user is asking a question or seeking information, even if their question contains grammatical errors, spelling mistakes, or typos.",
    ),
    ai_constants.FUNCTION_PROCESS_THREAD: (
        ProcessThreadArgs,
        f"Route to a response composed to someone else's message or summarise the entire thread, using a selected message, thread, quote, or current message. A thread is not required; this covers the first reply to a top-level message as well thread summary request. If the user already wrote the reply and wants it improved, use upgrade instead. A new standalone message that is not a reply goes to general query. and user wants to summarise the thread please trigger {ai_constants.FUNCTION_PROCESS_THREAD} with category as 'summarize' or generate reply use 'generate_reply'. Note: here you would not know which message or thread the user wants to reply to message please tigger this later we'll collect those deatils and generate reply in that context so if user want to reply to message in a thread or chat please trigger this function.",
    ),
    ai_constants.FUNCTION_GENERAL_QUERY: (
        GeneralQueryArgs,
        "Route all answerable questions, inquiries, knowledge requests, current events, coding, explanations, and conversation — including any question with grammatical errors or typos. You must provide the complete, direct, and final answer in the 'message' argument. For real-time or current topics, use web_search first and put the actual final answer in 'message'. Never return meta-responses, status messages, or placeholders.",
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

