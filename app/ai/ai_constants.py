
from enum import StrEnum

"""Cross-feature AI identifiers: intent-routing function names and billing
operation-type tags. Kept separate from `app.ai.registry` (model + pricing catalog)
and `app.ai.prompts.*` (per-feature instruction text) so each can change on its own.
"""

# Function/tool names the intent-detection model routes to. The argument schema for
# each one lives in `app.features.intent_detection.schemas.INTENT_TOOL_SCHEMAS`.
FUNCTION_GENERATE_SUMMARY = "generate_summary"
FUNCTION_UPGRADE_USER_CHAT = "upgrade_user_chat"
FUNCTION_PROCESS_THREAD = "process_thread"
FUNCTION_GENERAL_QUERY = "general_query"
FUNCTION_CLARIFY_USER_QUERY = "clarify_user_query"
FUNCTION_DOCUMENT_INTELLIGENCE = "document_intellegence"
FUNCTION_OUT_OF_SCOPE = "out_of_scope"

# Operation-type tags recorded on each billing row.
OPERATION_INTENT_DETECTION = "intent_detection"
OPERATION_CHAT_SUMMARY = "chat_summary"
OPERATION_UPGRADE_USER_CHAT = "upgrade_user_chat"
OPERATION_PROCESS_THREAD = "process_thread"
OPERATION_GENERAL_QUERY = "general_query"


class ThreadCategory(StrEnum):
    SUMMARIZE = "summarize"
    GENERATE_REPLY = "generate_reply"
