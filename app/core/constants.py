import os

# ==================  URL path  ===================

HEART_BEAT_PATH = "/heart_beat"
PUBLIC_PATHS = {"/docs", "/redoc", "/openapi.json", "/api/v1/auth/generate_tokens", "/ai_auth/generate_tokens"}


DEFAULT_OPENAI_MIN_OUTPUT_TOKENS = 30

CACHE_FOLDER_PATH = "cache/"


OPERATION_INTENT_DETECTION = "intent_detection"
OPERATION_CHAT_SUMMARY = "chat_summary"
OPERATION_UPGRADE_USER_CHAT = "upgrade_user_chat"
OPERATION_REPLY_TO_THREAD = "reply_to_thread"
