"""OpenAI-specific token counting, behind `app.ai.tokenizer`'s provider dispatch."""

import tiktoken


def count_tokens(content: str, model_name: str) -> int:
    tokenizer = tiktoken.encoding_for_model(model_name)
    return len(tokenizer.encode(content))
