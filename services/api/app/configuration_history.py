"""Configuration audit context and owner-scoped access to the existing audit log."""
from contextvars import ContextVar
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.errors import fail

_actor = ContextVar("configuration_actor", default="system:application")
_change = ContextVar("configuration_change", default=None)


def begin_context(*, source: str = "system:application"):
    """Start a server-generated correlation scope; never trust request actor headers."""
    return _actor.set(source), _change.set(str(uuid4()))


def end_context(tokens):
    _actor.reset(tokens[0])
    _change.reset(tokens[1])


def set_actor(identifier):
    """Accept only the identity resolved by server-side authentication."""
    _actor.set(str(UUID(str(identifier))))


async def bind_context(connection):
    change = _change.get() or str(uuid4())
    await connection.execute(
        "SELECT set_config('app.configuration_actor',%s,true),set_config('app.configuration_change',%s,true)",
        (_actor.get(), change),
    )


def safe_origin(value: str) -> str:
    """Expose only scheme, hostname and port; never userinfo, paths or query credentials."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        host = f"[{host}]" if ":" in host else host
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}" if parsed.scheme in {"http", "https"} else "地址已隐藏"
    except ValueError:
        return "地址已隐藏"


async def list_history(database, user: dict, resource_type: str, resource_id: str, *, limit: int = 100):
    """Filter ownership in SQL, including deleted resources; global configurations require admin."""
    if resource_type not in {"model", "mcp", "route", "knowledge", "memory", "repository", "notification"}:
        fail(404, "NOT_FOUND", "资源类型不存在")
    if resource_type in {"model", "mcp", "route"} and not user["is_admin"]:
        fail(403, "ADMIN_REQUIRED", "仅管理员可查看此配置修改记录")
    return await database.fetch(
        "SELECT id,resource_type,resource_id,resource_name,operation,before_revision,after_revision,"
        "created_at,actor_id,source,change_id,changes,categories,baseline FROM audit_events "
        "WHERE resource_type=%s AND resource_id=%s AND (owner_id=%s OR (owner_id IS NULL AND %s)) "
        "ORDER BY id DESC LIMIT %s",
        (resource_type, resource_id, user["id"], user["is_admin"], min(max(limit, 1), 100)),
    )


async def history_detail(database, user: dict, identifier: int):
    rows = await database.fetch(
        "SELECT resource_type,resource_id FROM audit_events WHERE id=%s "
        "AND (owner_id=%s OR (owner_id IS NULL AND %s)) AND resource_type IS NOT NULL",
        (identifier, user["id"], user["is_admin"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "修改记录不存在或无权查看")
    items = await list_history(database, user, rows[0]["resource_type"], rows[0]["resource_id"])
    matches = [row for row in items if row["id"] == identifier]
    if not matches:
        # Detail must also support records older than the list window.
        matches = await database.fetch(
            "SELECT id,resource_type,resource_id,resource_name,operation,before_revision,after_revision,"
            "created_at,actor_id,source,change_id,changes,categories,baseline FROM audit_events "
            "WHERE id=%s AND (owner_id=%s OR (owner_id IS NULL AND %s))",
            (identifier, user["id"], user["is_admin"]),
        )
    return matches[0]
