
from enum import StrEnum

"""Cross-feature AI identifiers: intent-routing function names and billing
operation-type tags. Kept separate from `app.ai.registry` (model + pricing catalog)
and `app.ai.prompts.*` (per-feature instruction text) so each can change on its own.
"""

# Function/tool names the intent-detection model routes to. The argument schema for
# each one lives in `app.features.intent_detection.schemas.INTENT_TOOL_SCHEMAS`.
FUNCTION_GENERATE_SUMMARY = "generate_summary"
FUNCTION_UPGRADE_USER_CHAT = "upgrade_user_chat"
FUNCTION_GENERAL_QUERY = "general_query"
FUNCTION_CLARIFY_USER_QUERY = "clarify_user_query"
FUNCTION_DOCUMENT_INTELLIGENCE = "document_intellegence"
FUNCTION_INTENT_SEARCH = "intent_search"
FUNCTION_OUT_OF_SCOPE = "out_of_scope"

# Function/tool names for the Global Search "AI answer" feature's two internal
# model calls. Argument schemas live in
# `app.features.search_answer.schemas.CLASSIFY_TOOL_SCHEMAS` / `ANSWER_TOOL_SCHEMAS`.
FUNCTION_CONTEXTUAL_QUERY = "contextual_query"
FUNCTION_NOT_CONTEXTUAL_QUERY = "not_contextual_query"
FUNCTION_PROVIDE_SEARCH_ANSWER = "provide_search_answer"
FUNCTION_INSUFFICIENT_SEARCH_CONTEXT = "insufficient_search_context"

# Operation-type tags recorded on each billing row.
OPERATION_INTENT_DETECTION = "intent_detection"
OPERATION_CHAT_SUMMARY = "chat_summary"
OPERATION_UPGRADE_USER_CHAT = "upgrade_user_chat"
OPERATION_PROCESS_THREAD = "process_thread"
OPERATION_GENERAL_QUERY = "general_query"
OPERATION_INTENT_SEARCH = "intent_search"
OPERATION_SEARCH_ANSWER_CLASSIFY = "search_answer_classify"
OPERATION_SEARCH_ANSWER_SYNTHESIZE = "search_answer_synthesize"


class SummaryCategory(StrEnum):
    CHAT_SUMMARY = "chat_summary"
    THREAD_SUMMARY = "thread_summary"
    GENERATE_REPLY = "generate_reply"
