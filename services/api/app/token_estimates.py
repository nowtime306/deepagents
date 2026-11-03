"""Network-free rough estimates; never tokenize image Base64 or claim exact billing."""

import json
import math

IMAGE_RESERVE = 2048
DEFAULT_OUTPUT = 4096


def text_tokens(text: str, config=None) -> int:
    """Approximate CJK as one token/character and other text as four chars/token."""
    if config is not None:
        from app.local_tokenizer import encoder

        encoding = encoder(config)
        if encoding is not None:
            return len(encoding.encode_ordinary(text))
    cjk = sum("\u2e80" <= char <= "\u9fff" for char in text)
    return cjk + math.ceil((len(text) - cjk) / 4)


def content_tokens(content, config=None) -> int:
    if isinstance(content, str):
        return text_tokens(content, config)
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"image", "image_url", "input_image"}:
            total += IMAGE_RESERVE
        elif block.get("type") in {"text", "input_text", "output_text"}:
            total += text_tokens(str(block.get("text", "")), config)
    return total


def message_tokens(messages, config=None) -> int:
    total = 0
    for batch in messages:
        for message in batch:
            total += 8 + content_tokens(message.content, config)
            calls = getattr(message, "tool_calls", None)
            if calls:
                total += text_tokens(json.dumps(calls, ensure_ascii=False), config)
    return total


def output_reserve(config, mode: str = "ask") -> int:
    limit = config.max_output_tokens or DEFAULT_OUTPUT
    return min(limit, 8192) if mode == "work" else limit
