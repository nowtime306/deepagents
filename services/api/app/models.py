"""Database-backed model settings with encrypted, write-only credentials."""

import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.config import ModelConfig
from app.errors import fail
from app.model_catalog import ModelKind, ModelProtocol, ModelProvider, probe_hint, supports_chat
from app.model_compatibility import capability
from app.storage import Database


class ModelInput(BaseModel):
    """Configuration edited in the administrator's model dialog."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2000)
    api_key: SecretStr | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False
    enabled: bool = True
    is_default: bool = False
    max_output_tokens: int | None = Field(default=None, ge=1, le=1048576)
    temperature: float | None = Field(default=None, ge=0, le=2)
    kind: ModelKind = "chat"
    provider: ModelProvider = "custom"
    protocol: ModelProtocol = "openai"
    supports_images: bool | None = None
    supports_tools: bool | None = None
    revision: int | None = Field(default=None, ge=1)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Permit explicit HTTP(S) model endpoints, including local inference servers."""
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            msg = "请填写不含账号、查询参数或片段的 HTTP(S) 模型服务地址"
            raise ValueError(msg)
        _ = url.port
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_default(self) -> "ModelInput":
        """A default selection must remain enabled and credential intent must be explicit."""
        if self.is_default and not self.enabled:
            msg = "默认模型必须启用"
            raise ValueError(msg)
        if self.is_default and not supports_chat(self.kind, self.protocol):
            msg = "只有聊天模型可以设为默认对话模型"
            raise ValueError(msg)
        if self.clear_api_key and self.api_key and self.api_key.get_secret_value():
            msg = "不能同时清除和设置密钥"
            raise ValueError(msg)
        return self


class CredentialVault:
    """Keep the platform master key outside database records and response payloads."""

    def __init__(self, path: Path, *, create: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and create:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key())
            except FileExistsError:
                pass
        if not path.exists():
            msg = "模型凭证加密主密钥缺失，请恢复数据库对应的 model.key 备份"
            raise RuntimeError(msg)
        self.cipher = Fernet(path.read_bytes())

    def encrypt(self, secret: str) -> str:
        """Encrypt a provider key before it reaches PostgreSQL."""
        return self.cipher.encrypt(secret.encode()).decode() if secret else ""

    def decrypt(self, ciphertext: str) -> SecretStr:
        """Decrypt only for a server-side invocation, never for a configuration read."""
        try:
            return SecretStr(self.cipher.decrypt(ciphertext.encode()).decode() if ciphertext else "")
        except InvalidToken:
            fail(503, "MODEL_KEY_UNAVAILABLE", "模型凭证无法解密，请恢复对应的主密钥备份")


class ModelRegistry:
    """Resolve current model records and preserve credentials during ordinary edits."""

    def __init__(self, database: Database, vault: CredentialVault) -> None:
        self.database, self.vault = database, vault

    async def list(self, *, enabled_only: bool = False) -> list[dict]:
        """Return safe metadata without ever returning encrypted or plaintext keys."""
        restriction = "WHERE enabled" if enabled_only else ""
        rows = await self.database.fetch(
            "SELECT id,name,model,base_url,max_output_tokens,temperature,enabled,is_default,"
            "kind,provider,protocol,supports_images,connection_status,checked_at,revision,"
            "(api_key_ciphertext<>'') AS api_key_configured "
            f"FROM model_configs {restriction} ORDER BY is_default DESC,created_at,id"
        )
        for row in rows:
            row["probe_hint"] = probe_hint(row["kind"], row["protocol"])
            row["compatibility"] = capability(row["kind"], row["protocol"], row["provider"])
        declarations = await self.database.fetch("SELECT model_id,supports_tools FROM model_capability_declarations")
        tools = {row["model_id"]: row["supports_tools"] for row in declarations}
        for row in rows:
            row["supports_tools"] = tools.get(row["id"])
        return rows

    async def get(self, identifier: str, *, enabled_only: bool = True) -> ModelConfig:
        """Freeze a model's database configuration and key for a new invocation."""
        rows = await self.database.fetch("SELECT * FROM model_configs WHERE id=%s", (identifier,))
        if not rows or (enabled_only and not rows[0]["enabled"]):
            fail(422, "MODEL_NOT_FOUND", "模型不存在或已停用，请重新选择")
        declarations = await self.database.fetch(
            "SELECT supports_tools FROM model_capability_declarations WHERE model_id=%s", (identifier,)
        )
        return self.from_row({**rows[0], "supports_tools": declarations[0]["supports_tools"] if declarations else None})

    def from_row(self, row: dict) -> ModelConfig:
        """Build a model snapshot from a row already locked by the caller."""
        return ModelConfig(
            id=row["id"],
            revision=row["revision"],
            name=row["name"],
            model=row["model"],
            base_url=row["base_url"],
            api_key=self.vault.decrypt(row["api_key_ciphertext"]),
            max_output_tokens=row["max_output_tokens"],
            temperature=row["temperature"],
            kind=row["kind"],
            provider=row["provider"],
            protocol=row["protocol"],
            supports_images=row["supports_images"],
            supports_tools=row.get("supports_tools"),
        )

    async def save(self, body: ModelInput, identifier: str | None = None) -> str:
        """Apply a full edit with optimistic revision checking and one atomic default."""
        async with self.database.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(710015)")
            current = None
            if identifier:
                cursor = await connection.execute("SELECT * FROM model_configs WHERE id=%s FOR UPDATE", (identifier,))
                current = await cursor.fetchone()
                if not current:
                    fail(404, "NOT_FOUND", "模型不存在")
                if body.revision != current["revision"]:
                    fail(409, "MODEL_REVISION_CONFLICT", "模型配置已更新，请刷新后重新编辑")
            identifier = identifier or str(uuid4())
            encrypted = current["api_key_ciphertext"] if current else ""
            if body.clear_api_key:
                encrypted = ""
            elif body.api_key and body.api_key.get_secret_value():
                encrypted = self.vault.encrypt(body.api_key.get_secret_value())
            if body.is_default:
                await connection.execute(
                    "UPDATE model_configs SET is_default=false,revision=revision+1 WHERE is_default AND id<>%s",
                    (identifier,),
                )
            await connection.execute(
                "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,max_output_tokens,temperature,"
                "enabled,is_default,kind,provider,protocol,supports_images) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,model=excluded.model,base_url=excluded.base_url,"
                "api_key_ciphertext=excluded.api_key_ciphertext,max_output_tokens=excluded.max_output_tokens,"
                "temperature=excluded.temperature,enabled=excluded.enabled,is_default=excluded.is_default,"
                "kind=excluded.kind,provider=excluded.provider,protocol=excluded.protocol,"
                "supports_images=excluded.supports_images,"
                "connection_status='not_tested',checked_at=NULL,revision=model_configs.revision+1,updated_at=now()",
                (
                    identifier,
                    body.name,
                    body.model,
                    body.base_url,
                    encrypted,
                    body.max_output_tokens,
                    body.temperature,
                    body.enabled,
                    body.is_default,
                    body.kind,
                    body.provider,
                    body.protocol,
                    body.supports_images
                    if "supports_images" in body.model_fields_set or not current
                    else current["supports_images"],
                ),
            )
            if "supports_tools" in body.model_fields_set:
                await connection.execute(
                    "INSERT INTO model_capability_declarations(model_id,supports_tools) VALUES(%s,%s) "
                    "ON CONFLICT(model_id) DO UPDATE SET supports_tools=excluded.supports_tools",
                    (identifier, body.supports_tools),
                )
        return identifier
