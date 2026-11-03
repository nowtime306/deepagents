"""Authorized configuration evidence through existing resource read permissions."""
from fastapi import APIRouter, Query, Request

from app.configuration_history import history_detail, list_history
from app.errors import fail
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["资源修改记录"])
PERMISSIONS = {
    "model": None, "mcp": None, "route": None,
    "knowledge": "knowledge.read", "memory": "memory.read",
    "repository": "repositories.read", "notification": "notifications.read",
}
OWNERSHIP = {
    "knowledge": "SELECT 1 FROM knowledge_bases WHERE id::text=%s AND owner_id=%s",
    "repository": "SELECT 1 FROM repositories WHERE id::text=%s AND owner_id=%s",
    "notification": "SELECT 1 FROM notification_channels WHERE id::text=%s AND owner_id=%s",
}


def authorize(user: dict, kind: str) -> None:
    """History never grants wider feature access than the original resource."""
    if kind not in PERMISSIONS:
        fail(404, "NOT_FOUND", "资源类型不存在")
    permission = PERMISSIONS[kind]
    if permission is None and not user["is_admin"]:
        fail(403, "ADMIN_REQUIRED", "仅管理员可查看此配置修改记录")
    if permission and not user["is_admin"] and permission not in user["permissions"]:
        fail(403, "PERMISSION_REQUIRED", "当前角色没有查看此资源修改记录的权限")


async def require_owner(database, user: dict, kind: str, identifier: str) -> None:
    """Keep deleted-resource evidence readable only by its original owner."""
    if kind == "memory":
        if identifier != str(user["id"]):
            fail(404, "NOT_FOUND", "资源不存在或无权查看")
    elif kind in OWNERSHIP:
        current = await database.fetch(OWNERSHIP[kind], (identifier, user["id"]))
        historical = await database.fetch(
            "SELECT 1 FROM audit_events WHERE resource_type=%s AND resource_id=%s AND owner_id=%s LIMIT 1",
            (kind, identifier, user["id"]),
        )
        if not current and not historical:
            fail(404, "NOT_FOUND", "资源不存在或无权查看")


async def attach_runs(database, owner, items):
    """Use one safe projection for history list and detail responses."""
    related = await database.fetch(
        "SELECT a.id,s.mode,s.run_id,s.phase,s.revision FROM audit_events a CROSS JOIN LATERAL "
        "(SELECT mode,run_id,phase,revision,resolved_at,snapshot_id FROM run_resource_snapshots "
        "WHERE owner_id=%s AND resource_kind=a.resource_type AND resource_id=a.resource_id "
        "AND revision=a.after_revision::text ORDER BY resolved_at DESC,snapshot_id DESC LIMIT 10) s "
        "WHERE a.id=ANY(%s) ORDER BY a.id DESC,s.resolved_at DESC,s.snapshot_id DESC",
        (owner, [item["id"] for item in items]),
    ) if items else []
    for item in items:
        item["related_runs"] = [{key: row[key] for key in ("mode", "run_id", "phase", "revision")}
                                for row in related if row["id"] == item["id"]]


@router.get("/resource-configurations/{kind}/{identifier}/changes")
async def changes(kind: str, identifier: str, request: Request, limit: int = Query(default=100, ge=1, le=100)) -> dict:
    user = await identity(request)
    authorize(user, kind)
    database = request.app.state.db
    await require_owner(database, user, kind, identifier)
    items = await list_history(database, user, kind, identifier, limit=limit)
    await attach_runs(database, user["id"], items)
    return {"items": items}


@router.get("/configuration-changes/{audit_id}")
async def detail(audit_id: int, request: Request) -> dict:
    user = await identity(request)
    row = await history_detail(request.app.state.db, user, audit_id)
    authorize(user, row["resource_type"])
    await attach_runs(request.app.state.db, user["id"], [row])
    return row
