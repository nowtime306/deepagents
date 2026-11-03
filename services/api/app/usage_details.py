"""Normalize numeric provider usage without persisting model or reasoning content."""


def count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def token_detail(message, group: str, key: str, raw_key: str | None, maximum: int) -> int | None:
    usage = message.usage_metadata
    detail = usage.get(group) or {}
    value = detail.get(key) if isinstance(detail, dict) else None
    if value is None and raw_key:
        metadata = getattr(message, "response_metadata", {}) or {}
        raw = metadata.get("token_usage") or {}
        value = raw.get(raw_key) if isinstance(raw, dict) else None
    parsed = count(value)
    return parsed if parsed is not None and parsed <= maximum else None


def reported_usage(response) -> dict | None:
    """Return complete totals, nullable details, or unknown for invalid/partial usage."""
    messages = [getattr(item, "message", None) for batch in response.generations for item in batch]
    if not messages or any(message is None or not message.usage_metadata for message in messages):
        return None
    usages = [message.usage_metadata for message in messages]
    for item in usages:
        if any(count(item.get(key)) is None for key in ("input_tokens", "output_tokens", "total_tokens")):
            return None
        if item["input_tokens"] + item["output_tokens"] != item["total_tokens"]:
            return None
    result = {key: sum(item[key] for item in usages) for key in ("input_tokens", "output_tokens", "total_tokens")}
    if any(count(value) is None for value in result.values()):
        return None
    for name, group, key, raw_key, limit in [
        ("cache_read_tokens", "input_token_details", "cache_read", "prompt_cache_hit_tokens", "input_tokens"),
        ("cache_creation_tokens", "input_token_details", "cache_creation", None, "input_tokens"),
        ("reasoning_tokens", "output_token_details", "reasoning", None, "output_tokens"),
    ]:
        values = [token_detail(message, group, key, raw_key, message.usage_metadata[limit]) for message in messages]
        result[name] = sum(values) if all(value is not None for value in values) else None
    if (result["cache_read_tokens"] or 0) + (result["cache_creation_tokens"] or 0) > result["input_tokens"]:
        result["cache_read_tokens"] = result["cache_creation_tokens"] = None
    return result
