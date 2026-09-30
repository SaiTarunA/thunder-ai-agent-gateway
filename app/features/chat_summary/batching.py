"""Token-aware, time-aligned splitting of chat conversations for batched summarization.

When a chat history is too large for one model call it is split into consecutive
batches. Each batch is summarized on its own (map step) and the results are merged
into one final summary (reduce step) by the handler.

Splitting rules, in priority order:

1. Every batch stays at or below ``target_tokens``.
2. Batches are kept roughly equal in size. Each cut is chosen for the *remaining*
   tokens, so one uneven batch does not skew the ones after it.
3. Within a tolerance around the ideal size, a cut is snapped to the strongest
   natural time boundary: month change, then week change, then day change, then an
   idle gap in the conversation, then any message boundary. So a 3-month history
   splits by month when the months are similar in volume, and a heavy single month
   splits by week or day.
4. A message is never cut in half or truncated, preserving complete conversation context.

This module is pure (no I/O, no model calls); the token counter is injected.
"""

import json
import logging
import math
from datetime import datetime
from typing import Callable, Optional

from app.features.chat_summary.schemas import (
    DEFAULT_BATCHING_SETTINGS,
    EXCLUDED_PAYLOAD_KEYS,
    TokenLimits,
    ConversationBatch,
    TIMESTAMP_FORMATS,
    STRENGTH_MONTH,
    STRENGTH_WEEK,
    STRENGTH_DAY,
    STRENGTH_IDLE_GAP,
    STRENGTH_NONE,
    IDLE_GAP_SECONDS,
    BALANCE_TOLERANCE
)

logger = logging.getLogger(__name__)

TokenCounter = Callable[[str], int]


class ChatSummaryBatcher:

    @staticmethod
    def parse_timestamp(value) -> Optional[datetime]:
        """Parses a message timestamp to a naive datetime; returns None when it cannot be parsed."""
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        try:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        except ValueError:
            pass
        for fmt in TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def boundary_strength(previous: Optional[datetime], current: Optional[datetime]) -> int:
        """How natural a cut between two consecutive messages is."""
        if previous is None or current is None:
            return STRENGTH_NONE
        if (previous.year, previous.month) != (current.year, current.month):
            return STRENGTH_MONTH
        if previous.isocalendar()[:2] != current.isocalendar()[:2]:
            return STRENGTH_WEEK
        if previous.date() != current.date():
            return STRENGTH_DAY
        if (current - previous).total_seconds() >= IDLE_GAP_SECONDS:
            return STRENGTH_IDLE_GAP
        return STRENGTH_NONE

    def split_into_batches(
        self,
        conversations: list[dict],
        target_tokens: int,
        count_tokens: TokenCounter,
        balance_tolerance: float = BALANCE_TOLERANCE,
    ) -> list[ConversationBatch]:
        """Splits ``conversations`` into consecutive, roughly equal, time-aligned batches.

        ``conversations`` are dicts shaped like ``{"timestamp": "YYYY-MM-DD HH:MM:SS", "user": ..., "message": ...}``.
        Messages are kept completely intact without truncation to avoid misleading summaries.
        """
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if not conversations:
            return []

        convs = list(conversations)

        # Keep chronological order. Only re-sort when every timestamp is parseable, so a bad
        # value can never scramble the order the database returned.
        stamps = [self.parse_timestamp(c.get("timestamp")) for c in convs]
        if all(s is not None for s in stamps):
            order = sorted(range(len(convs)), key=lambda i: stamps[i])
            convs = [convs[i] for i in order]
            stamps = [stamps[i] for i in order]

        logger.info(f"Splitting {len(convs)} conversations into batches with target tokens {target_tokens} and timestamps {stamps}")

        tokens: list[int] = [count_tokens(json.dumps(conv, indent=4)) for conv in convs]
        total_messages = len(convs)
        cumulative = [0]
        for t in tokens:
            cumulative.append(cumulative[-1] + t)

        # strength[i] is the quality of a cut placed *before* message i.
        strength = [STRENGTH_NONE] * (total_messages + 1)
        for i in range(1, total_messages):
            strength[i] = self.boundary_strength(stamps[i - 1], stamps[i])

        logger.info(f"Strength of cuts: {strength}")
        logger.info(f"Cumulative tokens: {cumulative}")

        batches: list[ConversationBatch] = []
        start = 0
        while start < total_messages:
            remaining = cumulative[total_messages] - cumulative[start]
            if remaining <= target_tokens:
                end = total_messages
            else:
                batches_left = math.ceil(remaining / target_tokens)
                ideal = remaining / batches_left
                low = ideal * (1 - balance_tolerance)
                high = min(float(target_tokens), ideal * (1 + balance_tolerance))

                feasible: list[tuple[int, int]] = []  # (cut index, batch tokens)
                for cut in range(start + 1, total_messages):
                    batch_tokens = cumulative[cut] - cumulative[start]
                    if batch_tokens > target_tokens:
                        break
                    feasible.append((cut, batch_tokens))

                def best_cut(candidates: list[tuple[int, int]]) -> Optional[int]:
                    best_key = None
                    best_index = None
                    for cut, batch_tokens in candidates:
                        key = (strength[cut], -abs(batch_tokens - ideal))
                        if best_key is None or key > best_key:
                            best_key, best_index = key, cut
                    return best_index

                end = best_cut([c for c in feasible if low <= c[1] <= high])
                if end is None:
                    # Nothing inside the window (a very large message, or a very bursty
                    # history): accept any cut that keeps the batch at least half the ideal size.
                    end = best_cut([c for c in feasible if c[1] >= ideal * 0.5])
                if end is None:
                    end = feasible[-1][0] if feasible else start + 1

            chunk = convs[start:end]
            batches.append(
                ConversationBatch(
                    conversations=chunk,
                    tokens=cumulative[end] - cumulative[start],
                    start=str(chunk[0].get("timestamp") or "") or None,
                    end=str(chunk[-1].get("timestamp") or "") or None,
                )
            )
            start = end

        logger.info(
            f"split {total_messages} messages ({cumulative[-1]} tokens) into {len(batches)} batches "
            f"(target {target_tokens})"
        )
        return batches

    @staticmethod
    def calculate_token_limits(
        model_info: dict,
        instructions: str,
        max_output_tokens: int,
        count_tokens: TokenCounter,
        batching_settings: Optional[dict] = None,
    ) -> TokenLimits:
        """Calculates single-pass and batched token limits for a prompt with `instructions`."""
        settings = {**DEFAULT_BATCHING_SETTINGS, **(batching_settings or {})}
        instruction_tokens = count_tokens(instructions)
        usable = (
            int(model_info["max_input_tokens"] * (1 - settings["context_safety_margin"]))
            - instruction_tokens
            - int(max_output_tokens)
        )
        single_pass = max(1, min(usable, settings["single_pass_max_input_tokens"]))
        batch_target = max(1, min(settings["batch_target_input_tokens"], single_pass))
        return TokenLimits(single_pass=single_pass, batch_target=batch_target)

    @staticmethod
    def strip_payload_segments(
        segments: list[dict], excluded_keys: frozenset = EXCLUDED_PAYLOAD_KEYS
    ) -> list[dict]:
        """Strips internal bookkeeping keys before sending segments to the model."""
        return [{k: v for k, v in seg.items() if k not in excluded_keys} for seg in segments]

    @staticmethod
    def group_segments_by_token_limit(
        segments: list[dict], sizes: list[int], token_limit: int
    ) -> list[list[dict]]:
        """Bin-packs adjacent segments so each group stays below the token limit."""
        groups: list[list[dict]] = []
        current: list[dict] = []
        current_tokens = 0
        for seg, size in zip(segments, sizes):
            if current and current_tokens + size > token_limit:
                groups.append(current)
                current, current_tokens = [], 0
            current.append(seg)
            current_tokens += size
        if current:
            groups.append(current)
        return groups


chat_summary_batcher = ChatSummaryBatcher()

