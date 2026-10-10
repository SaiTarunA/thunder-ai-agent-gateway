from fastapi import APIRouter, status
import logging
from livekit import api
from app.core.configs.agent_config import get_settings
from app.features.multi_voice_agents.agents import get_all_agents, START_AGENT
import uuid

logger = logging.getLogger(__name__)

voice_agent_router = APIRouter()


@voice_agent_router.get("/agents")
async def get_agents():
    try:
        s = get_settings()
        return {
            "start_agent": START_AGENT,
            "agents": get_all_agents(s),
        }
    except Exception as e:
        logger.error(f"Error fetching voice agents :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }


@voice_agent_router.post("/token")
async def generate_room_token(request: dict):
    try:
        s = get_settings()
        call_id = (request or {}).get("call_id") if isinstance(request, dict) else None
        call_id = call_id or uuid.uuid4().hex[:12]
        jwt = (
            api.AccessToken(s.livekit_api_key, s.livekit_api_secret.get_secret_value())
            .with_identity(f"caller-{uuid.uuid4().hex[:6]}")
            .with_name("Caller")
            .with_grants(api.VideoGrants(room_join=True, room=f"call-{call_id}"))
            .with_room_config(
                api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name=s.worker_agent_name)])
            )
            .to_jwt()
        )
        return {
            "url": s.livekit_url,
            "token": jwt,
            "call_id": call_id,
            "start_agent": START_AGENT,
            "agents": get_all_agents(s),
        }
    except Exception as e:
        logger.error(f"Error :: {e}")
        return {
            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "error": str(e),
            "msg": "Failed",
        }
