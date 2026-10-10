"""LiveKit voice worker. Run: uv run python -m app.worker dev"""

from __future__ import annotations

import asyncio
import json
import logging
import time

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
    session = AgentSession()
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
    await done.wait()


if __name__ == "__main__":
    cli.run_app(server)
