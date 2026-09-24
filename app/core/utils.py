import json
import re
from datetime import datetime, date, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo
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

    def convert_to_utc(
        self,
        dt_value: Any,
        source_tz: Optional[str] = "UTC",
    ) -> Optional[datetime]:
        """Convert a datetime string or datetime object to a UTC-aware datetime.

        Args:
            dt_value: A datetime object, date string (e.g. 'YYYY-MM-DD HH:MM:SS' or ISO format), or None.
            source_tz: The timezone of the source datetime. Defaults to 'UTC' if None or empty.

        Returns:
            A timezone-aware datetime in UTC, or None if dt_value is None or empty.
        """
        if dt_value is None:
            return None

        if isinstance(dt_value, str):
            dt_str = dt_value.strip()
            if not dt_str:
                return None
            try:
                parsed_dt = datetime.fromisoformat(dt_str)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        parsed_dt = datetime.strptime(dt_str, fmt)
                        break
                    except ValueError:
                        pass
                else:
                    logger.error(f"Failed to parse datetime string: {dt_str}")
                    return None
        elif isinstance(dt_value, datetime):
            parsed_dt = dt_value
        elif isinstance(dt_value, date):
            parsed_dt = datetime.combine(dt_value, datetime.min.time())
        else:
            logger.error(f"Unsupported datetime type: {type(dt_value)}")
            return None

        if parsed_dt.tzinfo is not None:
            return parsed_dt.astimezone(timezone.utc)

        tz_name = (source_tz or "UTC").strip() if isinstance(source_tz, str) else "UTC"
        if not tz_name:
            tz_name = "UTC"

        try:
            tz = ZoneInfo(tz_name)
        except Exception as e:
            logger.warning(f"Invalid or unsupported timezone '{tz_name}', defaulting to UTC: {e}")
            tz = timezone.utc

        return parsed_dt.replace(tzinfo=tz).astimezone(timezone.utc)

    def convert_utc_to_timezone(
        self,
        dt_value: Any,
        target_tz: Optional[str] = "UTC",
        output_format: Optional[str] = "%Y-%m-%d %H:%M:%S",
    ) -> Any:
        """Convert a UTC datetime string or datetime object to the target timezone.

        Args:
            dt_value: A datetime object or UTC date string (e.g. 'YYYY-MM-DD HH:MM:SS').
            target_tz: Target timezone name (e.g. 'Asia/Kolkata'). Defaults to 'UTC' if None or empty.
            output_format: Optional strftime format. If None, returns the aware datetime object.

        Returns:
            Formatted datetime string (or datetime object if output_format is None),
            or dt_value unchanged if conversion fails or dt_value is None.
        """
        if dt_value is None:
            return None

        parsed_dt = None
        if isinstance(dt_value, str):
            dt_str = dt_value.strip()
            if not dt_str:
                return None
            try:
                parsed_dt = datetime.fromisoformat(dt_str)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        parsed_dt = datetime.strptime(dt_str, fmt)
                        break
                    except ValueError:
                        pass
                else:
                    logger.error(f"Failed to parse datetime string: {dt_str}")
                    return dt_value
        elif isinstance(dt_value, datetime):
            parsed_dt = dt_value
        elif isinstance(dt_value, date):
            parsed_dt = datetime.combine(dt_value, datetime.min.time())
        else:
            return dt_value

        # Ensure parsed_dt is UTC-aware
        if parsed_dt.tzinfo is None:
            utc_dt = parsed_dt.replace(tzinfo=timezone.utc)
        else:
            utc_dt = parsed_dt.astimezone(timezone.utc)

        # Resolve target timezone
        tz_name = (target_tz or "UTC").strip() if isinstance(target_tz, str) else "UTC"
        if not tz_name:
            tz_name = "UTC"

        try:
            target_zone = ZoneInfo(tz_name)
        except Exception as e:
            logger.warning(f"Invalid target timezone '{tz_name}', defaulting to UTC: {e}")
            target_zone = timezone.utc

        converted_dt = utc_dt.astimezone(target_zone)

        if output_format:
            return converted_dt.strftime(output_format)
        return converted_dt

    def convert_to_timestamp(
        self,
        dt_value: Any,
        source_tz: Optional[str] = "UTC",
    ) -> Optional[float]:
        """Convert a datetime string, datetime object, or numeric value into a Unix epoch timestamp float.

        If dt_value is a date string, it is interpreted in source_tz and converted to UTC epoch seconds.
        """
        if dt_value is None:
            return None
        if isinstance(dt_value, (int, float)):
            return float(dt_value)
        if isinstance(dt_value, str):
            trimmed = dt_value.strip()
            if not trimmed or trimmed.lower() in ("none", "null"):
                return None
            try:
                return float(trimmed)
            except ValueError:
                pass
            utc_dt = self.convert_to_utc(trimmed, source_tz=source_tz)
            if utc_dt:
                return float(utc_dt.timestamp())
        elif isinstance(dt_value, (datetime, date)):
            utc_dt = self.convert_to_utc(dt_value, source_tz=source_tz)
            if utc_dt:
                return float(utc_dt.timestamp())
        return None


utils = Utils()
