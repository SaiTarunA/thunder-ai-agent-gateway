"""OpenAI model catalog: names, context limits, and per-token pricing used for billing.

Split out of the old 41.5KB `providers/open_ai/constants.py`, which mixed this catalog
together with per-feature prompt text and hand-written tool-calling JSON Schema. Kept
separate from `app.ai.prompts.*` (instruction text) and `app.features.intent_detection`
(tool argument schemas) so pricing can change without touching either.
"""

MODEL_OPENAI_PROVIDER = "OpenAI"

# GPT-4o-mini model, used for Luna summary.
MODEL_GPT_4O_MINI = {
    "model_id": 1,
    "model_name": "gpt-4o-mini",
    "max_input_tokens": 128000,  # 128k is the gpt-4o max token limit
    "max_output_tokens": 16384,
    "input_token_price": 0.150,
    "cached_input_token_price": 0.075,
    "output_token_price": 0.600,
}

# GPT-4.1 model, used for SMS campaign.
MODEL_GPT_4_1 = {
    "model_id": 5,
    "model_name": "gpt-4.1",
    "max_input_tokens": 1000000,
    "max_output_tokens": 32768,
    "input_token_price": 2.00,
    "cached_input_token_price": 0.50,
    "output_token_price": 8.00,
}

# GPT-4.1-mini model, used for Luna summary.
MODEL_GPT_4_1_MINI = {
    "model_id": 6,
    "model_name": "gpt-4.1-mini",
    "max_input_tokens": 1048576,  # 1M token context window
    "max_output_tokens": 32768,
    "input_token_price": 0.400,
    "cached_input_token_price": 0.100,
    "output_token_price": 1.600,
}

# GPT-4o model details.
MODEL_GPT_4O = {
    "model_id": 2,
    "model_name": "gpt-4o",
    "max_input_tokens": 128000,
    "max_output_tokens": 4096,
    "input_token_price": 2.50,
    "output_token_price": 10.00,
}

# TTS-1 model details.
MODEL_GPT_TTS_1 = {
    "model_id": 3,
    "model_name": "tts-1",
    "max_characters": 4096,
    "bill_per_1m_char": 15,
    "supported_response_formats": ["mp3", "opus", "aac", "flac", "wav", "pcm"],
}

# Whisper-1 model details.
MODEL_GPT_WHISPER_1 = {
    "model_id": 4,
    "model_name": "whisper-1",
    "bill_for_1min": 0.006,
    "max_size": (25 * 1024 * 1024),  # 25mb
    "supported_audio_formats": ["mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm"],
    "supported_response_formats": ["json", "text", "srt", "verbose_json", "vtt"],
}

DEFAULT_OPENAI_MAX_OUTPUT_TOKENS = 500
DEFAULT_OPENAI_MIN_OUTPUT_TOKENS = 30
