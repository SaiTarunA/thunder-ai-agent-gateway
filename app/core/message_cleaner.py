"""
Message cleaning utility.

Uses:
  - ftfy       : fix mojibake / broken Unicode encoding
  - clean-text : remove emojis, normalize whitespace, fix unicode
  - emoji      : accurately detect/strip emoji code-points
  - html / re  : unescape HTML entities and strip HTML tags (stdlib)

Public API
----------
clean_message(text)            -> str   : normalize a single message string
filter_conversations(messages) -> list  : remove noise entries, return cleaned list
"""

import html as _html
import re
import logging

import ftfy
import emoji
from cleantext import clean as _ct_clean

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1 – Text Normalization
# ---------------------------------------------------------------------------

def clean_message(text: str) -> str:
    """
    Normalize a raw chat message string.

    Pipeline:
      1. ftfy       – fix mojibake / broken Unicode
      2. clean-text – strip emojis, fix unicode, normalize line breaks
      3. html       – unescape remaining HTML entities (&amp; → &)
      4. re         – strip remaining HTML/XML tags (<b>word</b> → word)
      5. re         – collapse extra whitespace

    Emojis are removed because they add no useful information for AI
    summarisation and reply generation.
    Returns an empty string if nothing meaningful remains.
    """
    if not text or not text.strip():
        return ""

    try:
        # 1. Fix broken encoding (café not cafÃ©)
        text = ftfy.fix_text(text)

        # 2. cleantext: emoji removal + unicode normalisation + line-break fix
        text = _ct_clean(
            text,
            fix_unicode=True,
            to_ascii=False,        # preserve accented chars (é, ü …)
            lower=False,           # preserve original casing
            no_line_breaks=True,   # flatten multi-line messages
            no_urls=False,         # keep URLs – may be relevant context
            no_emoji=True,         # remove emoji
            no_punct=False,        # keep punctuation for readability
        )

        # 3. Unescape HTML entities leftover after cleantext
        text = _html.unescape(text)

        # 4. Strip HTML/XML tags (e.g. <b>bold</b>)
        text = re.sub(r"<[^>]+>", " ", text)

        # 5. Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()

    except Exception as exc:
        logger.warning(f"clean_message failed, returning raw text. Error: {exc}")
        return text.strip()

    return text


# ---------------------------------------------------------------------------
# Stage 2 – Noise Detection
# ---------------------------------------------------------------------------

def _is_emoji_only(text: str) -> bool:
    """Return True if after stripping emojis nothing meaningful remains."""
    stripped = emoji.replace_emoji(text, replace="").strip()
    return not stripped


def is_noise(raw_text: str, cleaned_text: str) -> bool:
    """
    Return True if the message should be discarded before sending to AI.

    Checks (in order):
      1. Empty after cleaning
      2. Emoji-only original message
      3. Cleaned text is only digits / symbols / punctuation
      4. Cleaned text is fewer than 2 words (catches single filler words too)
    """
    # 1. Nothing left after cleaning
    if not cleaned_text:
        return True

    # 2. Original was emoji-only (cleaning removed everything meaningful)
    if _is_emoji_only(raw_text):
        return True

    # 3. Pure numbers / symbols / punctuation with no letters
    if not re.search(r"[a-zA-Z\u00C0-\u024F]", cleaned_text):
        return True

    # 4. Less than 2 words → single token (could be "ok", "yes", a lone number …)
    if len(cleaned_text.split()) < 2:
        return True

    return False


# ---------------------------------------------------------------------------
# Stage 3 – Conversation List Filter
# ---------------------------------------------------------------------------

def filter_conversations(conversations: list[dict]) -> list[dict]:
    """
    Given a list of conversation entry dicts:
        [{"timestamp": ..., "user": ..., "message": ...}, ...]

    Returns a new list where:
      - Each message is cleaned (HTML stripped, emojis removed, encoding fixed)
      - Noise entries (emoji-only, empty, pure numbers, single tokens) are dropped

    The original list is not mutated.
    """
    result: list[dict] = []
    dropped = 0

    for entry in conversations:
        raw = entry.get("message", "")
        cleaned = clean_message(raw)

        if is_noise(raw, cleaned):
            dropped += 1
            logger.debug(
                f"Dropped noise message from '{entry.get('user', '?')}': {raw!r}"
            )
            continue

        result.append({**entry, "message": cleaned})

    if dropped:
        logger.info(
            f"Filtered {dropped} noise message(s) out of {len(conversations)} total."
        )

    return result
