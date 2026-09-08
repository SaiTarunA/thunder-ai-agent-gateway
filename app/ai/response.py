"""Provider-neutral response shape.

Every provider adapter under `app.ai.providers.*` returns an `AIResponse`, never its
own SDK's raw response object. This is what lets every feature handler stop parsing
a specific vendor's output format directly (previously the OpenAI Responses API's
`response.output[].type == "message" / "function_call"` shape was hand-parsed in three
different handler files).
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolCall:
    """One function/tool call the model asked to make."""

    id: Optional[str]
    name: str
    arguments: str  # raw JSON string; validate/parse via app.ai.tools.parse_tool_call_arguments


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass
class AIResponse:
    """Normalized result of one `AIProvider.generate()` call."""

    text: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = field(default_factory=list)
    usage: Optional[Usage] = None
    model: Optional[str] = None
    provider: str = ""
    raw: Any = None  # original provider response, for provider-specific debugging only
