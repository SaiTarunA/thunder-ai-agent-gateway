"""LiveKit voice worker. Run: uv run python -m app.worker dev"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from livekit.agents import AgentServer, AgentSession, JobContext, cli, room_io

from app.features.multi_voice_agents.agents import AGENTS, first_opening, get_all_agents, START_AGENT
from app.features.multi_voice_agents.call import CallContext, SpecialistAgent
from app.core.configs.agent_config import get_settings
from app.features.multi_voice_agents.noise_filter import build_noise_filter
from app.features.multi_voice_agents.store import CallStore

log = logging.getLogger("worker")
settings = get_settings()
server = AgentServer(
    ws_url=settings.livekit_url,
    api_key=settings.livekit_api_key,
    api_secret=settings.livekit_api_secret.get_secret_value(),
)
store = CallStore()
_vad: Any = None


def _build_session() -> AgentSession:
    """AgentSession with LiveKit-managed turn-taking (free, local Silero VAD) when configured.

    OpenAI's server VAD reacts to any speech-like sound and interrupts at once. Here the model's
    own detection is off (see call._turn_detection); Silero decides when you speak, and the agent
    only stops talking after `interrupt_min_duration_ms` of continuous speech, resuming if it
    turns out to be a false trigger. If Silero cannot load, fall back to OpenAI's server VAD.
    """
    global _vad
    if settings.vad_mode != "livekit":
        return AgentSession()
    try:
        if _vad is None:
            from livekit.plugins import silero

            _vad = silero.VAD.load(
                activation_threshold=settings.vad_activation_threshold,
                min_speech_duration=settings.vad_min_speech_ms / 1000,
                min_silence_duration=0.4,
                prefix_padding_duration=settings.vad_prefix_padding_ms / 1000,
            )
    except Exception:
        log.exception("Silero VAD unavailable, falling back to OpenAI server VAD")
        settings.vad_mode = "server"
        return AgentSession()
    return AgentSession(
        vad=_vad,
        turn_handling={
            "turn_detection": "vad",
            "endpointing": {"min_delay": settings.vad_silence_ms / 1000, "max_delay": 3.0},
            "interruption": {
                "enabled": True,
                "mode": "vad",
                "min_duration": settings.interrupt_min_duration_ms / 1000,
                "resume_false_interruption": True,
                "false_interruption_timeout": settings.interrupt_false_timeout_s,
                "discard_audio_if_uninterruptible": True,
            },
            "preemptive_generation": {"enabled": False},
        },
    )


@server.rtc_session(agent_name=settings.worker_agent_name)
async def entrypoint(job: JobContext) -> None:
    await job.connect()
    call_id = job.room.name.removeprefix("call-")
    call = CallContext(job.room, store, settings, call_id)
    call.spawn(
        call.publish(
            {
                "type": "agent_registry",
                "start_agent": START_AGENT,
                "agents": get_all_agents(settings),
            }
        )
    )
    state = await call.event(type="start")  # resumes from memory if one exists
    resumed = bool(state.get("turns"))
    spec = AGENTS[state["active_agent"]]
    opening = (
        "The call was reconnected. Briefly welcome the caller back and continue the "
        "remaining work without re-asking anything in the briefing."
        if resumed
        else first_opening(spec)
    )
    agent = SpecialistAgent(call, spec, state.get("briefing", ""), opening)
    session = _build_session()
    done = asyncio.Event()

    @session.on("user_input_transcribed")
    def _user_transcript(ev) -> None:
        call.on_user_stream(getattr(ev, "transcript", ""), getattr(ev, "is_final", False))

    @session.on("conversation_item_added")
    def _item(ev) -> None:
        name = getattr(session.current_agent, "spec", None)
        call.on_item(ev.item, name.name if name else "agent")

    @session.on("agent_state_changed")
    def _state(ev) -> None:
        if ev.new_state == "speaking" and call.seam_t0 is not None:
            log.info(
                "handoff seam: %.0f ms to first audio", (time.monotonic() - call.seam_t0) * 1000
            )
            call.seam_t0 = None

    @session.on("close")
    def _close(_ev) -> None:
        done.set()

    call.close_session = session.aclose

    @job.room.on("data_received")
    def _client_response(packet) -> None:
        if getattr(packet, "topic", "") != "client_response":
            return
        try:
            msg = json.loads(bytes(packet.data).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("bad client_response payload")
            return
        if isinstance(msg, dict):
            call.on_client_response(msg)

    noise_filter = build_noise_filter(settings)
    log.info("noise filter: %s", "rnnoise" if noise_filter else "off")
    await session.start(
        agent=agent,
        room=job.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(noise_cancellation=noise_filter)
        ),
    )
    call.spawn(call.load_contact_names())  # listening hint; the call does not wait for it
    await done.wait()


if __name__ == "__main__":
    cli.run_app(server)
