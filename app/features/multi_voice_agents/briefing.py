"""Template briefing handed to the next agent (no model call, so no added latency)."""

from __future__ import annotations

from app.features.multi_voice_agents.agents import AGENTS
from app.features.multi_voice_agents.state import CallState

RECENT_TURNS = 8


def _who(name: str | None) -> str:
    spec = AGENTS.get(name or "")
    return spec.person if spec else (name or "Agent")


def build_briefing(state: CallState, to_agent: str) -> str:
    lines: list[str] = []
    lines.append(f"CALL BRIEFING for {to_agent}.")
    lines.append(f"Caller's original request: {state.get('original_request') or 'not stated yet'}")

    handoffs = state.get("handoffs", [])
    path = [handoffs[0].from_agent, *[h.to_agent for h in handoffs]] if handoffs else []
    if not path:
        path = [state.get("active_agent", to_agent)]
    lines.append("Colleagues so far: " + " -> ".join(_who(a) for a in path))

    if handoffs:
        h = handoffs[-1]
        lines.append(f"Why you got the call (from {_who(h.from_agent)}): {h.reason}")
        if h.task_summary:
            lines.append(f"Already handled: {h.task_summary}")
        if h.remaining_work:
            lines.append(f"Remaining for you: {h.remaining_work}")

    actions = state.get("actions", [])
    if actions:
        lines.append("Actions already performed:")
        lines += [f"- {a.agent}: {a.tool} -> {a.result}" for a in actions]
    tasks = state.get("tasks", [])
    if tasks:
        lines.append("Tasks:")
        lines += [f"- [{t.status}] {t.title} (owner {t.owner})" for t in tasks]
    entities = state.get("entities", {})
    if entities:
        lines.append(
            "Known facts (do NOT ask again): " + "; ".join(f"{k}={v}" for k, v in entities.items())
        )

    turns = state.get("turns", [])[-RECENT_TURNS:]
    if turns:
        lines.append("Recent conversation:")
        for t in turns:
            who = "Caller" if t.role == "user" else _who(t.agent)
            suffix = " [caller interrupted; this is all they heard]" if t.interrupted else ""
            lines.append(f"{who}: {t.text}{suffix}")
    return "\n".join(lines)
