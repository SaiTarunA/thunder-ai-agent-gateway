import json
import re
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


META_RESPONSE_PATTERNS = [
    r"\bi\s+(?:will\s+)?process(?:ing)?\s+(?:your\s+)?request\b",
    r"\bprocess(?:ed|ing)?\s+(?:your\s+)?request\s+(?:by|via|using|with)\s+web\s*search\b",
    r"\b(?:i\s+will|let\s+me|i'll)\s+(?:search|check|find|look\s+(?:\w+\s+)?up)\b",
    r"\b(?:i\s+will|let\s+me|i'll)\s+(?:search|look|check|find)\s+(?:for|up|into|out)\b",
    r"\bsearching\s+(?:the\s+web|for|online)\b",
    r"\bweb\s*search\s+(?:in\s+progress|tool|call)\b",
    r"^i\s+(?:am\s+)?processing\b",
]

EXPLICIT_UPGRADE_PATTERNS = [
    r"\b(?:make\s+this|make\s+it)\s+(?:more\s+)?(?:professional|formal|polite|casual|concise|shorter|better|clear|friendly)\b",
    r"\b(?:rephrase|rewrite|proofread|paraphrase|polish)\b",
    r"\b(?:fix|correct|check)\s+(?:the\s+)?(?:grammar|spelling|typos?)\b",
    r"\b(?:improve|format|upgrade)\s+(?:this|my)?\s*(?:draft|message|email|text|chat)?\b",
    r"\bconvert\s+(?:this\s+)?to\s+markdown\b",
    r"\bchange\s+(?:the\s+)?tone\b",
]

QUESTION_START_PATTERNS = [
    r"^\s*(?:what|who|where|when|why|how|which|whose|whom)\b",
    r"^\s*(?:what's|whats|whos|who's|wheres|where's|whens|when's|whys|why's|hows|how's)\b",
    r"^\s*(?:tell\s+me|explain|describe|define|list|show\s+me|give\s+me|help\s+me\s+understand|help\s+me\s+with)\b",
    r"^\s*(?:diff[e|a]?r[e|a]?nce\s+between|meaning\s+of|weather\s+in|weather\s+today|capital\s+of|score\s+of|price\s+of|population\s+of)\b",
    r"^\s*(?:is|are|am|was|were|do|does|did|will|would|should|could)\b",
    r"^\s*can\s+(?:you|u)\s+(?:tell|explain|describe|clarify|answer|help|provide|give|show|list|share|elaborate)\b",
]

INQUIRY_PHRASES = [
    r"\bdiff[e|a]?r[e|a]?nce\s+between\b",
    r"\bmeaning\s+of\b",
    r"\bcapital\s+of\b",
    r"\bweather\s+in\b",
]


class Utils:

    _instance = None

    @staticmethod
    def safe_parse_json(value: Any) -> Any:
        try:
            if value is None:
                return None
            if isinstance(value, (dict, list)):
                return value
            if not isinstance(value, str):
                return None

            trimmed = value.strip()
            if not trimmed:
                return None
            if not (trimmed.startswith("{") or trimmed.startswith("[")):
                return None

            return json.loads(trimmed)

        except Exception:
            try:
                # Fix invalid raw control characters inside JSON strings
                def _replace_ctrl(match):
                    char = match.group(0)
                    if char == "\n":
                        return "\\n"
                    elif char == "\r":
                        return "\\r"
                    elif char == "\t":
                        return "\\t"
                    elif char == "\b":
                        return "\\b"
                    elif char == "\f":
                        return "\\f"
                    return ""

                sanitized = re.sub(r"[\x00-\x1f]", _replace_ctrl, value)
                return json.loads(sanitized)
            except Exception:
                return None

    def extract_user_name(self, user):
        try:
            name = user
            if "[V]" in name:
                name = name.split("[V]")[-1]
            if "_" in name:
                name = name.split("_")[-1]
            return name
        except Exception as e:
            logger.error(f"Error :: {e}")
            return user


    def is_meta_response(self, text: Optional[str]) -> bool:
        """Check if the text is a placeholder or meta-statement rather than a real answer."""
        if not text or not text.strip():
            return True
        cleaned = text.strip().lower()

        for pattern in META_RESPONSE_PATTERNS:
            if re.search(pattern, cleaned):
                return True

        words = cleaned.split()
        if len(words) <= 15:
            has_search_term = any(w in cleaned for w in ["search", "websearch", "web_search", "lookup"])
            has_process_term = any(w in cleaned for w in ["process", "processing", "tool", "request"])
            if has_search_term and has_process_term:
                return True

        return False


    def is_inquiry_or_question(self, query: Optional[str]) -> bool:
        """Determine if a user query is an inquiry/question seeking an answer,
        rather than a draft message meant to be upgraded/polished.
        """
        if not query or not query.strip():
            return False
        cleaned = query.strip()
        lower = cleaned.lower()

        # If the user explicitly asks to edit, rewrite, or polish, it's an upgrade
        for pattern in EXPLICIT_UPGRADE_PATTERNS:
            if re.search(pattern, lower):
                return False

        # Check if query ends with a question mark
        if cleaned.endswith("?"):
            return True

        # Check if query starts with question words
        for pattern in QUESTION_START_PATTERNS:
            if re.search(pattern, lower):
                return True

        # Check for general inquiry phrases in short queries
        for pattern in INQUIRY_PHRASES:
            if re.search(pattern, lower):
                return True

        return False

utils = Utils()
