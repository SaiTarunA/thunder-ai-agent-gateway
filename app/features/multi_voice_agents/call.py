"""Call logic for the LiveKit worker: one specialist Agent per persona, shared call state.

Each agent runs its own OpenAI Realtime model (its own voice). A handoff is a tool that returns
the next SpecialistAgent; the briefing from the shared state is appended to its instructions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.encoders import jsonable_encoder
from livekit.agents import Agent, RunContext, function_tool
from livekit.plugins import openai

from app.features.multi_voice_agents.agents import (
    AGENTS,
    AgentSpec,
    handoff_opening,
    instructions_for,
    serialize_agent,
    tool_schemas,
    voice_for,
)
from app.core.configs.agent_config import Settings
from app.features.multi_voice_agents.store import CallStore
from app.features.multi_voice_agents.tools import (
    CLIENT_TIMEOUT,
    CLIENT_TOOLS,
    ClientError,
    run_client_tool,
)

log = logging.getLogger("call")
PUBLISH_TIMEOUT = 3.0  # UI updates are best effort; never let them stall the call
TOOL_TIMEOUT = 8.0  # a local tool must always answer, or the realtime model waits on it forever


def _public(state: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in state.items() if k not in ("event", "briefing", "handoff_result")}


class CallContext:
    """Per-call helper: applies state events, and publishes updates to the browser."""

    def __init__(self, room: Any, store: CallStore, settings: Settings, call_id: str) -> None:
        self.room, self.store, self.settings, self.call_id = room, store, settings, call_id
        self.seam_t0: float | None = None
        self._bg: set[asyncio.Task] = set()
        self._pending: dict[str, asyncio.Future] = {}
        self.close_session: Callable[[], Awaitable[None]] | None = None

    async def publish(self, payload: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self.room.local_participant.publish_data(
                    json.dumps(payload), reliable=True, topic="msg"
                ),
                PUBLISH_TIMEOUT,
            )

    async def event(self, **event: Any) -> dict[str, Any]:
        event["call_id"] = self.call_id
        state = self.store.apply(self.call_id, event)  # synchronous, so events never interleave
        # fire and forget: callers (tools, handoffs) only need the state, not the UI round trip
        self.spawn(self.publish({"type": "state", "state": jsonable_encoder(_public(state))}))
        return state

    # ---- round trips to the caller's app -----------------------------------------------
    async def client_request(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Ask the browser app to do something and wait up to CLIENT_TIMEOUT for its answer."""
        request_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            await self.publish(
                {"type": "client_request", "request_id": request_id, "action": action, "params": params}
            )
            resp = await asyncio.wait_for(fut, CLIENT_TIMEOUT)
        except TimeoutError:
            raise ClientError(
                f"The app did not answer within {CLIENT_TIMEOUT:.0f} seconds."
            ) from None
        finally:
            self._pending.pop(request_id, None)
        if not resp.get("ok"):
            raise ClientError(str(resp.get("error") or "The app could not do that."))
        return resp.get("data") or {}

    def on_client_response(self, msg: dict[str, Any]) -> None:
        fut = self._pending.get(str(msg.get("request_id", "")))
        if fut and not fut.done():
            fut.set_result(msg)

    async def end_session(self, reason: str) -> None:
        await self.publish({"type": "end_session", "reason": reason})
        if self.close_session:
            with contextlib.suppress(Exception):
                await self.close_session()

    def spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # ---- transcript -> shared state ----------------------------------------------------
    def on_user_stream(self, text: str, is_final: bool) -> None:
        if not text:
            return
        self.spawn(self.publish({"type": "transcript_stream", "role": "user", "text": text, "is_final": is_final}))

    def on_agent_stream_delta(self, agent_name: str, delta: str) -> None:
        if not delta:
            return
        self.spawn(self.publish({"type": "transcript_stream", "role": "assistant", "agent": agent_name, "delta": delta}))

    def on_item(self, item: Any, agent_name: str) -> None:
        """Called for each finished chat message. Interrupted replies keep only what was heard."""
        if getattr(item, "type", "") != "message" or item.role not in ("user", "assistant"):
            return
        text = (item.text_content or "").strip()
        if not text:
            return
        interrupted = bool(getattr(item, "interrupted", False))
        msg = {"role": item.role, "text": text, "interrupted": interrupted}
        if item.role == "assistant":
            msg["agent"] = agent_name
        self.spawn(self.publish({"type": "transcript", **msg}))
        self.spawn(self.event(type="turn", turn=msg))

    # ---- tools -------------------------------------------------------------------------
    async def run_tool(self, agent: SpecialistAgent, name: str, args: dict, run: RunContext) -> Any:
        if name == "transfer_to_agent":
            return await self._transfer(agent, args, run)
        t0 = time.monotonic()
        if name in CLIENT_TOOLS:
            return await self._run_client_tool(name, args)
        try:
            return await asyncio.wait_for(self._run_tool(name, args), TOOL_TIMEOUT)
        except TimeoutError:
            log.error("tool %s timed out after %.0fs args=%s", name, TOOL_TIMEOUT, args)
            return "That didn't go through. Apologise briefly and try the same step once more."
        finally:
            log.info("tool %s took %.0f ms", name, (time.monotonic() - t0) * 1000)

    async def _run_tool(self, name: str, args: dict) -> Any:
        if name == "update_task":
            await self.event(type="task", **{k: args.get(k, "") for k in ("id", "title", "status")})
            return "Task saved."
        if name == "note_fact":
            await self.event(type="fact", name=args.get("name", ""), value=args.get("value", ""))
            return "Fact saved."
        return f"Unknown tool {name}."

    async def _run_client_tool(self, name: str, args: dict) -> str:
        t0 = time.monotonic()
        self.spawn(self.publish({"type": "tool_status", "tool": name, "status": "pending"}))
        ok = False
        try:
            outcome = await run_client_tool(self.call_id, name, args, self.client_request)
            ok, result = True, outcome.text
            if outcome.end_session:
                # The call is handed to the softphone: end the voice session right away, silently.
                self.spawn(self.end_session("call_started"))
        except ClientError as e:
            log.error("client tool %s failed: %s args=%s", name, e, args)
            result = (
                f"FAILED: {e} Nothing was done. Apologise briefly, tell the caller it did not "
                "go through, and offer to try again."
            )
        finally:
            log.info("client tool %s took %.0f ms", name, (time.monotonic() - t0) * 1000)
        self.spawn(
            self.publish({"type": "tool_status", "tool": name, "status": "done" if ok else "failed"})
        )
        await self.event(type="action", tool=name, args=args, result=result)
        return result

    async def _transfer(self, agent: SpecialistAgent, args: dict, run: RunContext) -> Any:
        to_agent = args.get("agent", "")
        if to_agent and to_agent in AGENTS:
            next_spec = AGENTS[to_agent]
            self.spawn(
                self.publish(
                    {
                        "type": "agent_transfer",
                        "status": "pending",
                        "from_agent": agent.spec.name,
                        "to_agent": to_agent,
                        "next_agent": serialize_agent(next_spec, self.settings),
                        "reason": args.get("reason", ""),
                    }
                )
            )
        await run.wait_for_playout()  # let the handover sentence finish before the voice changes
        state = await self.event(
            type="handoff",
            to=to_agent,
            reason=args.get("reason", ""),
            task_summary=args.get("task_summary", ""),
            remaining_work=args.get("remaining_work", ""),
        )
        result = state["handoff_result"]
        if not result["approved"]:
            return result["reason"]  # the current agent keeps the call
        dst = AGENTS[result["to"]]
        spoke_before = any(h.to_agent == dst.name for h in state["handoffs"][:-1]) or (
            state["handoffs"][0].from_agent == dst.name
        )
        opening = handoff_opening(dst, agent.spec, returning=spoke_before)
        self.seam_t0 = time.monotonic()
        # Bare agent, no text: a text result makes livekit ask the *old* model for a tool reply,
        # so the previous agent would speak again after the handoff.
        return SpecialistAgent(self, dst, result["briefing"], opening)


def _turn_detection(s: Settings, spec: AgentSpec) -> Any:
    """Server VAD ignores quiet/short sounds (loudness threshold); semantic VAD has no such knob."""
    td = openai.realtime.realtime_model.TurnDetection
    if s.vad_mode == "semantic":
        return td(
            type="semantic_vad",
            eagerness=spec.eagerness,
            create_response=True,
            interrupt_response=True,
        )
    return td(
        type="server_vad",
        threshold=s.vad_threshold,
        prefix_padding_ms=s.vad_prefix_padding_ms,
        silence_duration_ms=s.vad_silence_ms,
        create_response=True,
        interrupt_response=True,
    )


class SpecialistAgent(Agent):
    def __init__(self, call: CallContext, spec: AgentSpec, briefing: str, opening: str) -> None:
        s = call.settings
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else None
        llm = openai.realtime.RealtimeModel(
            model=s.realtime_model,
            voice=voice_for(spec, s),
            api_key=key,
            input_audio_transcription=openai.realtime.realtime_model.AudioTranscription(
                model=s.transcribe_model,
                **({"language": s.transcribe_language} if s.transcribe_language else {}),
            ),
            input_audio_noise_reduction=(
                s.openai_noise_reduction
                if s.openai_noise_reduction in ("near_field", "far_field")
                else None
            ),
            turn_detection=_turn_detection(s, spec),
        )
        orig_session = llm.session
        def _hooked_session(*args: Any, **kwargs: Any) -> Any:
            sess = orig_session(*args, **kwargs)
            @sess.on("openai_server_event_received")
            def _on_openai_ev(ev: Any) -> None:
                t = ev.get("type", "") if isinstance(ev, dict) else getattr(ev, "type", "")
                if t in (
                    "response.output_audio_transcript.delta",
                    "response.audio_transcript.delta",
                    "response.output_text.delta",
                    "response.text.delta",
                ):
                    delta = ev.get("delta", "") if isinstance(ev, dict) else getattr(ev, "delta", "")
                    if delta:
                        call.on_agent_stream_delta(spec.name, delta)
            return sess
        llm.session = _hooked_session

        super().__init__(
            instructions=instructions_for(spec, briefing),
            llm=llm,
            tools=[self._tool(schema) for schema in tool_schemas(spec)],
        )
        self.call, self.spec, self.opening = call, spec, opening

    def _tool(self, schema: dict) -> Any:
        name = schema["name"]

        async def handler(raw_arguments: dict[str, Any], context: RunContext) -> Any:
            return await self.call.run_tool(self, name, raw_arguments, context)

        raw = {k: schema[k] for k in ("name", "description", "parameters")}
        return function_tool(handler, raw_schema=raw)

    async def on_enter(self) -> None:
        agent_data = serialize_agent(self.spec, self.call.settings)
        next_candidates = [
            serialize_agent(AGENTS[t], self.call.settings)
            for t in self.spec.can_transfer_to
            if t in AGENTS
        ]
        self.call.spawn(
            self.call.publish(
                {
                    "type": "agent",
                    **agent_data,
                    "next_candidates": next_candidates,
                }
            )
        )
        self.session.generate_reply(instructions=self.opening)
