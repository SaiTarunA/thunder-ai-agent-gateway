"""Settings read from .env. Model names and voices are never hard-coded elsewhere."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    realtime_model: str = "gpt-realtime-2.1"
    transcribe_model: str = "gpt-4o-mini-transcribe"
    transcribe_language: str = "en"  # ISO-639-1 hint for the transcription model; "" = auto-detect
    voice_knowledge: str = "cedar"
    voice_call: str = "ash"
    voice_sms: str = "marin"
    voice_chat: str = "shimmer"
    # Server-side noise handling for the caller's mic (works on self-hosted LiveKit).
    noise_cancellation: str = "rnnoise"  # "rnnoise" or "none"
    noise_gate_threshold: float = 0.25  # RNNoise speech probability below this is muted (0..1)
    noise_gate_hold_ms: int = 200  # keep the gate open this long after speech so word tails survive
    # OpenAI's own input noise reduction. Off by default: RNNoise above already cleans the audio and
    # stacking denoisers smears words. "near_field" (headsets), "far_field" (room mics) or "none".
    openai_noise_reduction: str = "none"
    # Turn detection. "server" ignores quiet or short sounds; "semantic" is OpenAI's smarter
    # end-of-turn model but has no loudness threshold.
    vad_mode: str = "server"
    vad_threshold: float = 0.55  # 0..1, higher needs louder speech to count (and to interrupt)
    vad_prefix_padding_ms: int = 300
    vad_silence_ms: int = 700  # silence that ends the caller's turn
    livekit_url: str = "ws://192.168.2.4:7880"
    livekit_api_key: str = "voiceagent"
    livekit_api_secret: SecretStr = SecretStr(
        "8efe26bc1fd92fe6c9a55f96ca9552ffa8d5d9dcace8a44b9cac945a685198b6"
    )
    worker_agent_name: str = "voice-worker"
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
