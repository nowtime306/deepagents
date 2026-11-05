"""Server-only environment configuration."""

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.model_catalog import ModelKind, ModelProtocol, ModelProvider

DEFAULT_INTERNAL_API_URL = "http://127.0.0.1:8100"


def validate_internal_api_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            isinstance(value, str) and not any(ord(char) <= 32 for char in value)
            and parsed.scheme == "http" and ipaddress.ip_address(parsed.hostname or "").is_loopback
            and parsed.port is not None and 1024 <= parsed.port <= 65535
            and parsed.path in {"", "/"} and "@" not in parsed.netloc and "%" not in parsed.netloc
            and not parsed.query and not parsed.fragment and "?" not in value and "#" not in value
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("PLATFORM_INTERNAL_API_URL须为含显式端口的HTTP本机回环根地址（1024–65535）")
    return value.rstrip("/")


class ModelConfig(BaseModel):
    """A frozen runtime model configuration resolved from the database."""

    id: str
    revision: int = 1
    name: str
    model: str
    base_url: str
    api_key: SecretStr = SecretStr("")
    max_output_tokens: int | None = None
    temperature: float | None = None
    kind: ModelKind = "chat"
    provider: ModelProvider = "custom"
    protocol: ModelProtocol = "openai"
    supports_images: bool | None = None
    supports_tools: bool | None = None
    route_selection_id: UUID | None = None
    embedding_compatibility_key: str | None = None


@dataclass
class Settings:
    """Read configuration without exposing secrets to the client."""

    database_url: str = "postgresql://agent:agent@127.0.0.1:55432/owner_agent?connect_timeout=10"
    web_origin: str = "http://localhost:3100"
    cookie_secure: bool = False
    data_dir: Path = Path(__file__).parents[1] / ".local"
    run_timeout: int = 180
    work_enabled: bool = False
    ask_worker_enabled: bool = False
    automation_enabled: bool = False
    evaluation_enabled: bool = False
    media_worker_enabled: bool = False
    internal_api_url: str = DEFAULT_INTERNAL_API_URL
    evaluation_instance_id: str | None = None
    evaluation_platform_commit: str | None = None
    restricted_execution: bool = field(default=False, kw_only=True)
    session_cookie_name: str = field(default="owner_agent_session", kw_only=True)

    def __post_init__(self) -> None:
        self.internal_api_url = validate_internal_api_url(self.internal_api_url)
        if not isinstance(self.session_cookie_name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.session_cookie_name
        ):
            raise ValueError("SESSION_COOKIE_NAME must be 1-64 safe ASCII characters, starting with a letter or digit")
        if self.restricted_execution and any((self.work_enabled, self.automation_enabled,
                                             self.evaluation_enabled, self.media_worker_enabled)):
            raise ValueError("Restricted release cannot enable background execution workers")

    @classmethod
    def from_env(cls) -> "Settings":
        """Load the package-local environment and validate model definitions."""
        load_dotenv()
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            data_dir=Path(os.getenv("PLATFORM_DATA_DIR", str(cls.data_dir))),
            web_origin=os.getenv("WEB_ORIGIN", cls.web_origin),
            internal_api_url=os.getenv("PLATFORM_INTERNAL_API_URL", cls.internal_api_url),
            restricted_execution=os.getenv("RESTRICTED_EXECUTION", "false").lower() == "true",
            work_enabled=os.getenv("WORK_ENABLED", "false").lower() == "true",
            ask_worker_enabled=os.getenv("ASK_WORKER_ENABLED", "true").lower() == "true",
            automation_enabled=os.getenv("AUTOMATION_ENABLED", "true").lower() == "true",
            evaluation_enabled=os.getenv("EVALUATION_ENABLED", "true").lower() == "true",
            media_worker_enabled=os.getenv("MEDIA_WORKER_ENABLED", "true").lower() == "true",
            cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            session_cookie_name=os.getenv("SESSION_COOKIE_NAME", cls.session_cookie_name),
            evaluation_instance_id=os.getenv("EVALUATION_INSTANCE_ID"),
            evaluation_platform_commit=os.getenv("EVALUATION_PLATFORM_COMMIT"),
        )


class LoginInput(BaseModel):
    """Local account credentials."""

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class ConversationInput(BaseModel):
    """An independently owned Ask conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="新对话", min_length=1, max_length=120)
    agent_id: UUID | None = None
    agent_version_id: UUID | None = None
    project_id: UUID | None = None


class RunInput(BaseModel):
    """A new user message submitted to the selected model."""

    message: str = Field(min_length=1, max_length=32000)
    native_search: bool | None = None
    model_routing: bool = False
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)
    model_id: str = Field(min_length=1, max_length=120)
