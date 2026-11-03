"""Protocol capabilities based on explicit configuration and verified official endpoints."""

from urllib.parse import urlsplit

from app.errors import fail


def require_tools(config) -> None:
    """Require an explicit capability declaration only when binding actual tools."""
    if config.supports_tools is None:
        fail(422, "MODEL_TOOLS_UNVERIFIED", "模型工具调用能力尚未验证，请管理员验证并声明后使用工具")
    if not config.supports_tools:
        fail(422, "MODEL_TOOLS_UNSUPPORTED", "模型不支持工具调用，请选择支持工具的模型")


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


def require_images(config) -> None:
    """Validate image input against the actual execution configuration."""
    if not image_input(config):
        code = "MODEL_IMAGES_UNVERIFIED" if config.supports_images is None else "MODEL_IMAGES_UNSUPPORTED"
        fail(422, code, "模型图片输入能力尚未确认或不支持，请管理员验证后选择支持图片的模型")
