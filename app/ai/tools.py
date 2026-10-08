"""Manual, LangChain-free equivalent of `bind_tools()`.

Two things, both driven off a plain Pydantic model:

1. `pydantic_model_to_openai_tool` derives an OpenAI function-calling ("strict" mode)
   tool schema directly from the model, so the schema and the validation applied to
   whatever the model returns can never drift apart — no hand-written JSON Schema
   block to keep in sync by hand.
2. `parse_tool_call_arguments` validates a raw tool-call arguments payload against
   that same model before any handler touches it.

This intentionally does not depend on LangChain, langgraph, or any other framework —
it is ~30 lines of Pydantic + stdlib `json`.
"""

import json
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _strip_titles(schema: dict) -> None:
    """Pydantic adds a "title" to the schema and to every property; OpenAI's tool
    schema doesn't need it and it's just noise, so drop it recursively (including
    inside $defs, which Pydantic emits for nested/```Literal``` types, and inside
    `items`, which Pydantic emits for list-of-model fields)."""
    if not isinstance(schema, dict):
        return
    schema.pop("title", None)
    for value in schema.get("properties", {}).values():
        _strip_titles(value)
    for value in schema.get("$defs", {}).values():
        _strip_titles(value)
    items = schema.get("items")
    if isinstance(items, dict):
        _strip_titles(items)


def _apply_strict_object_rules(schema: dict) -> None:
    """OpenAI's strict function-calling mode requires `additionalProperties:
    false` and every property listed in `required` on EVERY object schema in
    the structure, not just the root - Pydantic nests a referenced model's own
    schema under `$defs` (for a nested BaseModel field) and under `items` (for
    a list-of-BaseModel field), so this has to recurse into both, not just
    into `properties`. A model whose only nested field is itself flat (no
    further nesting) never exercised this path, which is why it went unnoticed
    until the first tool schema with a list-of-model field."""
    if not isinstance(schema, dict):
        return

    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}).keys())

    for value in schema.get("properties", {}).values():
        _apply_strict_object_rules(value)

    items = schema.get("items")
    if isinstance(items, dict):
        _apply_strict_object_rules(items)

    for value in schema.get("$defs", {}).values():
        _apply_strict_object_rules(value)


def pydantic_model_to_openai_tool(
    model: Type[BaseModel],
    *,
    name: str,
    description: str,
    strict: bool = True,
) -> dict:
    """Build an OpenAI Responses-API function/tool definition from a Pydantic model.

    In `strict` mode (the default, matching this codebase's existing tool defs),
    OpenAI requires every property to be listed in `required` (optionality is
    expressed through nullable types, not omission) and `additionalProperties: false`
    at every object level, including nested models. Pydantic's own `required` list
    only includes fields without a default, so it's overridden here to include
    every property, at every level.
    """
    schema = model.model_json_schema()
    schema.setdefault("type", "object")
    schema["additionalProperties"] = False
    if strict:
        schema["required"] = list(schema.get("properties", {}).keys())
        _apply_strict_object_rules(schema)
    _strip_titles(schema)

    return {
        "type": "function",
        "name": name,
        "strict": strict,
        "description": description,
        "parameters": schema,
    }


def parse_tool_call_arguments(model: Type[T], raw_arguments) -> T:
    """Validate a tool call's arguments (a JSON string, a dict, or None for a
    no-argument tool) against `model`, returning a validated instance. Raises
    `pydantic.ValidationError` on a mismatch, same as every other Pydantic parse
    in this codebase."""
    if raw_arguments is None:
        data = {}
    elif isinstance(raw_arguments, str):
        data = json.loads(raw_arguments) if raw_arguments else {}
    else:
        data = raw_arguments
    return model.model_validate(data)
