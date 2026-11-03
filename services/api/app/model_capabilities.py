"""Protocol capabilities based on explicit configuration and verified official endpoints."""

from urllib.parse import urlsplit


def native_search_mode(config) -> str | None:
    if config.protocol != "openai" or config.kind != "chat":
        return None
    host = urlsplit(config.base_url).hostname
    if (
        host == "api.deepseek.com"
        and urlsplit(config.base_url).scheme == "https"
        and urlsplit(config.base_url).port in (None, 443)
    ):
        return "responses" if config.model == "deepseek-v4-pro" else "deepseek_search"
    if config.provider == "openai" or host == "api.openai.com":
        return "responses"
    return None


def image_input(config) -> bool:
    if config.supports_images is not None:
        return config.supports_images
    if config.kind == "vision" and config.protocol == "openai":
        return True
    return (
        config.protocol == "openai"
        and urlsplit(config.base_url).hostname == "api.deepseek.com"
        and config.model == "deepseek-flash"
    )
