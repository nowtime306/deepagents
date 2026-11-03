"""Run-scoped resource resolution with encrypted, credential-free recovery records."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from app.errors import fail


@dataclass
class ResourceRun:
    database: object
    owner: object
    mode: str
    run: object
    phase: int = 1
    values: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def cached_credentials(self) -> dict:
        """Process-only credentials, shared when a paused run resumes."""
        key = (str(self.owner), self.mode, str(self.run), self.phase)
        return _credentials.setdefault(key, {})


active_resources: ContextVar[ResourceRun | None] = ContextVar("active_resources", default=None)
# Credentials stay in process memory only. Paused runs reuse these while the process lives.
_credentials: dict[tuple, object] = {}


def current_scope():
    """Return the active root run scope, or None outside execution."""
    return active_resources.get()


def release_credentials(owner, mode, run_id):
    """Forget process-only credentials once a run reaches a terminal state."""
    prefix = (str(owner), mode, str(run_id))
    for key in list(_credentials):
        if key[:3] == prefix:
            del _credentials[key]


@asynccontextmanager
async def resource_run(database, owner, mode, run_id, *, phase=None):
    """Share a resource snapshot across members and nested workflows of one run."""
    existing = active_resources.get()
    if existing is not None:
        if str(existing.owner) != str(owner):
            fail(403, "RESOURCE_SCOPE_MISMATCH", "运行资源不属于当前账号")
        yield existing
        return
    if phase is None:
        rows = await database.fetch(
            "SELECT phase FROM run_resource_stages WHERE owner_id=%s AND mode=%s AND run_id=%s "
            "AND (phase=1 OR confirmed_by IS NOT NULL) ORDER BY phase DESC LIMIT 1", (owner, mode, run_id),
        )
        phase = rows[0]["phase"] if rows else 1
    await database.execute(
        "INSERT INTO run_resource_stages(owner_id,mode,run_id,phase) VALUES(%s,%s,%s,1) ON CONFLICT DO NOTHING",
        (owner, mode, run_id),
    )
    if phase != 1 and not await database.fetch(
        "SELECT 1 FROM run_resource_stages WHERE owner_id=%s AND mode=%s AND run_id=%s "
        "AND phase=%s AND confirmed_by IS NOT NULL", (owner, mode, run_id, phase),
    ):
        fail(409, "RESOURCE_STAGE_UNCONFIRMED", "恢复执行阶段尚未确认")
    state = ResourceRun(database, owner, mode, run_id, phase)
    token = active_resources.set(state)
    try:
        yield state
    finally:
        active_resources.reset(token)
        if mode in {"resource", "workflow"}:
            release_credentials(owner, mode, run_id)


async def freeze_resource(kind, identifier, revision, config, *, vault, snapshot=None, credential_scope=None):
    """Freeze noncredential execution fields; return the original fields on recovery.

    Callers must pass only credential-free config and a safe display snapshot.
    Live resource authorization remains the caller's responsibility.
    """
    state = active_resources.get()
    if state is None:
        return config
    key = (kind, str(identifier))
    async with state.lock:
        if key in state.values:
            return state.values[key]
        identity = (state.owner, state.mode, state.run, state.phase, kind, str(identifier))
        rows = await state.database.fetch(
            "SELECT * FROM run_resource_snapshots WHERE owner_id=%s AND mode=%s AND run_id=%s "
            "AND phase=%s AND resource_kind=%s AND resource_id=%s", identity,
        )
        if not rows:
            await state.database.execute(
                "INSERT INTO run_resource_snapshots(owner_id,mode,run_id,phase,resource_kind,resource_id,"
                "revision,snapshot,execution_config,credential_scope) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (*identity, str(revision), Jsonb(snapshot or {}), vault.encrypt(json.dumps(config)), credential_scope),
            )
            rows = await state.database.fetch(
                "SELECT * FROM run_resource_snapshots WHERE owner_id=%s AND mode=%s AND run_id=%s "
                "AND phase=%s AND resource_kind=%s AND resource_id=%s", identity,
            )
        row = rows[0]
        state.records[key] = row
        value = json.loads(vault.decrypt(row["execution_config"]).get_secret_value())
        state.values[key] = value
        return value


def credential_scope(config):
    """A rotated key can only be recovered for the same provider and destination."""
    value = [config.protocol, config.provider, config.base_url]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


async def freeze_model(config, vault, *, credential_marker=None):
    """Retain a model's first configuration and never persist its API key."""
    state = active_resources.get()
    if state is None:
        return config
    key = ("model", config.id)
    fields = config.model_dump(mode="json", exclude={"api_key", "route_selection_id"})
    # Free-form names and endpoints can contain credentials; public metadata is deliberately restricted.
    safe = {name: fields[name] for name in (
        "id", "revision", "model", "kind", "provider", "protocol", "max_output_tokens", "temperature",
        "supports_images", "supports_tools",
    )}
    from app.configuration_history import safe_origin

    safe["service_target"] = safe_origin(config.base_url)
    safe["service_target_digest"] = hashlib.sha256(config.base_url.encode()).hexdigest()
    if config.kind in {"embedding", "multimodal_embedding"}:
        from app.embedding_compatibility import fingerprint

        safe["vector_fingerprint"] = fingerprint(
            config, preprocessing="media-v1" if config.kind == "multimodal_embedding" else "text-v1",
        )
        safe["embedding_compatibility_key"] = config.embedding_compatibility_key
    if credential_marker:
        safe["credential_revision"] = credential_marker
    frozen = await freeze_resource(
        "model", config.id, config.revision, fields, vault=vault, snapshot=safe,
        credential_scope=credential_scope(config),
    )
    if key not in state.cached_credentials:
        if credential_scope(config.model_copy(update=frozen)) != credential_scope(config):
            fail(409, "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED", "恢复所需的模型服务目标或协议已变化，请确认恢复配置")
        state.cached_credentials[key] = config.api_key
        recorded = state.records[("model", config.id)]["snapshot"].get("credential_revision")
        if (credential_marker and recorded and recorded != credential_marker) or (
            not recorded and frozen["revision"] != config.revision
        ):
            await state.database.execute(
                "UPDATE run_resource_snapshots SET credential_changed=true WHERE owner_id=%s AND mode=%s "
                "AND run_id=%s AND phase=%s AND resource_kind='model' AND resource_id=%s",
                (state.owner, state.mode, state.run, state.phase, config.id),
            )
    return config.model_copy(update={**frozen, "api_key": state.cached_credentials[key]})


def resolved_model(identifier):
    """Return actual noncredential model fields for usage accounting, if resolved."""
    state = active_resources.get()
    return state.values.get(("model", str(identifier))) if state else None
