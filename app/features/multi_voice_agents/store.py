"""Call state reducer. Event-driven: each event is routed to one handler, whose update is merged
into the call's state with the reducers from app.call_state.

State lives in process memory, keyed by call_id, so a reconnect to the same worker resumes the call.
"""

from __future__ import annotations

from typing import Any

from app.features.multi_voice_agents.agents import AGENTS, START_AGENT
from app.features.multi_voice_agents.briefing import build_briefing
from app.features.multi_voice_agents.state import REDUCERS, ActionRecord, CallState, HandoffRecord, Task, Turn

MAX_HANDOFFS = 12  # callers may switch between tasks several times in one call
MAX_PER_PAIR = 5  # handoffs between the same two agents (either direction)


def _substantive(text: str) -> bool:
    return len(text.split()) >= 4


def n_start(state: CallState) -> dict[str, Any]:
    ev = state["event"]
    out: dict[str, Any] = {"call_id": ev["call_id"]}
    if not state.get("active_agent"):
        out["active_agent"] = START_AGENT
    active = out.get("active_agent") or state["active_agent"]
    out["briefing"] = build_briefing({**state, **out}, active) if state.get("turns") else ""
    return out


def n_turn(state: CallState) -> dict[str, Any]:
    turn = Turn(**state["event"]["turn"])
    out: dict[str, Any] = {"turns": [turn]}
    if turn.role == "user" and not state.get("original_request") and _substantive(turn.text):
        out["original_request"] = turn.text  # set once, never overwritten
    return out


def n_action(state: CallState) -> dict[str, Any]:
    ev = state["event"]
    rec = ActionRecord(
        agent=state["active_agent"], tool=ev["tool"], args=ev.get("args", {}), result=ev["result"]
    )
    return {"actions": [rec]}


def n_task(state: CallState) -> dict[str, Any]:
    ev = state["event"]
    return {
        "tasks": [
            Task(id=ev["id"], title=ev["title"], owner=state["active_agent"], status=ev["status"])
        ]
    }


def n_fact(state: CallState) -> dict[str, Any]:
    ev = state["event"]
    return {"entities": {ev["name"]: ev["value"]}}


def n_handoff(state: CallState) -> dict[str, Any]:
    ev = state["event"]
    src, dst = state["active_agent"], ev["to"]
    history = state.get("handoffs", [])

    def refuse(why: str) -> dict[str, Any]:
        return {"handoff_result": {"approved": False, "reason": why}}

    if dst not in AGENTS or dst not in AGENTS[src].can_transfer_to:
        return refuse(f"{src} cannot transfer to {dst}. Keep helping the caller yourself.")
    if len(history) >= MAX_HANDOFFS:
        return refuse("Too many transfers on this call. Resolve it yourself or offer a callback.")
    pair = {src, dst}
    if sum(1 for h in history if {h.from_agent, h.to_agent} == pair) >= MAX_PER_PAIR:
        return refuse(f"The caller already bounced between {src} and {dst}. Resolve it yourself.")

    rec = HandoffRecord(
        from_agent=src,
        to_agent=dst,
        reason=ev["reason"],
        task_summary=ev.get("task_summary", ""),
        remaining_work=ev.get("remaining_work", ""),
    )
    tasks = [
        t.model_copy(update={"owner": dst}) if t.owner == src and t.status != "done" else t
        for t in state.get("tasks", [])
    ]
    new_state: CallState = {**state, "handoffs": [*history, rec], "tasks": tasks}
    briefing = build_briefing(new_state, dst)
    return {
        "handoffs": [rec],
        "tasks": tasks,
        "active_agent": dst,
        "briefing": briefing,
        "handoff_result": {"approved": True, "to": dst, "briefing": briefing},
    }


NODES = {
    "start": n_start,
    "turn": n_turn,
    "action": n_action,
    "task": n_task,
    "fact": n_fact,
    "handoff": n_handoff,
}


def _merge(state: CallState, update: dict[str, Any]) -> CallState:
    out: dict[str, Any] = dict(state)
    for key, value in update.items():
        reducer = REDUCERS.get(key)
        out[key] = reducer(state.get(key), value) if reducer else value
    return out  # type: ignore[return-value]


class CallStore:
    """In-memory state for every call on this worker, one entry per call_id."""

    def __init__(self) -> None:
        self._calls: dict[str, CallState] = {}

    def apply(self, call_id: str, event: dict[str, Any]) -> CallState:
        state = _merge(self._calls.get(call_id, {}), {"event": event})
        state = _merge(state, NODES[event["type"]](state))
        self._calls[call_id] = state
        return state
