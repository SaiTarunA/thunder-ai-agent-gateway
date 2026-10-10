"""Shared conversation state. One per call; every agent reads and writes this."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    agent: str | None = None
    text: str
    interrupted: bool = False  # True: text is only what the caller actually heard


class HandoffRecord(BaseModel):
    from_agent: str
    to_agent: str
    reason: str
    task_summary: str = ""
    remaining_work: str = ""
    ts: str = Field(default_factory=_now)


class ActionRecord(BaseModel):
    agent: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    ts: str = Field(default_factory=_now)


class Task(BaseModel):
    id: str
    title: str
    owner: str
    status: Literal["open", "in_progress", "done"] = "open"


def upsert_tasks(left: list[Task] | None, right: list[Task] | None) -> list[Task]:
    by_id = {t.id: t for t in (left or [])}
    for t in right or []:
        by_id[t.id] = t
    return list(by_id.values())


def merge_dicts(left: dict[str, str] | None, right: dict[str, str] | None) -> dict[str, str]:
    return {**(left or {}), **(right or {})}


def append(left: list | None, right: list | None) -> list:
    return [*(left or []), *(right or [])]


class CallState(TypedDict, total=False):
    call_id: str
    event: dict[str, Any]  # the input event for this invocation (overwritten each time)
    original_request: str | None  # set once, never overwritten
    active_agent: str
    turns: Annotated[list[Turn], append]
    handoffs: Annotated[list[HandoffRecord], append]  # append-only
    actions: Annotated[list[ActionRecord], append]  # append-only
    tasks: Annotated[list[Task], upsert_tasks]
    entities: Annotated[dict[str, str], merge_dicts]
    briefing: str
    handoff_result: dict[str, Any]


# Fields merged with a reducer; every other field is overwritten by the latest update.
REDUCERS: dict[str, Callable[[Any, Any], Any]] = {
    "turns": append,
    "handoffs": append,
    "actions": append,
    "tasks": upsert_tasks,
    "entities": merge_dicts,
}
