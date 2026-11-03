"""Immutable private Agent publication, with live revocation and pinned resource revisions."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.errors import fail
from app.model_catalog import supports_chat
from app.security import identity

router = APIRouter(prefix="/api/v1/agents", tags=["智能体版本"])
FIELDS = (
    "id,name,instructions,model_id,revision,knowledge_ids,skill_ids,mcp_ids,"
    "web_enabled,web_mode,memory_enabled,workflow_version_ids,team,model_routing"
)


async def selected(request: Request, owner: UUID, agent: UUID | None, version: UUID) -> dict:
    rows = await request.app.state.db.fetch(
        "SELECT v.snapshot,v.id,v.version FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
        "WHERE v.id=%s AND v.agent_id=%s AND v.owner_id=%s AND a.owner_id=%s AND a.enabled AND v.active",
        (version, agent, owner, owner),
    )
    if not rows:
        fail(403, "AGENT_VERSION_UNAVAILABLE", "智能体版本不存在、已下线或无权使用")
    row = rows[0]
    snapshot = {**row["snapshot"], "published_version_id": str(row["id"]), "published_version": row["version"]}
    config = await request.app.state.models.get(snapshot["model_id"])
    if config.revision != snapshot["model_revision"]:
        fail(409, "AGENT_MODEL_CHANGED", "已发布智能体的模型配置已变化，请发布新版本")
    return await request.app.state.capabilities.freeze(owner, snapshot)


class Publish(BaseModel):
    revision: int = Field(ge=1)


class State(BaseModel):
    active: bool


@router.get("/{identifier}/versions")
async def listing(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT v.id,v.version,v.revision,v.active,v.snapshot,v.created_at FROM agent_versions v "
        "JOIN agents a ON a.id=v.agent_id WHERE a.id=%s AND a.owner_id=%s ORDER BY v.version DESC",
        (identifier, user["id"]),
    )
    return {"items": rows}


@router.post("/{identifier}/publish", status_code=201)
async def publish(identifier: UUID, body: Publish, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        f"SELECT {FIELDS} FROM agents WHERE id=%s AND owner_id=%s AND enabled AND revision=%s",
        (identifier, user["id"], body.revision),
    )
    if not rows:
        fail(409, "AGENT_CONFLICT", "智能体已修改、停用或无权访问，请刷新后发布")
    snapshot = await request.app.state.capabilities.freeze(user["id"], jsonable_encoder(rows[0]))
    model = await request.app.state.models.get(snapshot["model_id"])
    if not supports_chat(model.kind, model.protocol):
        fail(422, "AGENT_MODEL_UNSUPPORTED", "智能体需要支持当前协议的聊天模型，请调整模型后再发布")
    snapshot["model_revision"] = model.revision
    from app.quality_gate import publication_guard

    async with request.app.state.db.pool.connection() as connection:
        current = await (
            await connection.execute(
                "SELECT revision FROM agents WHERE id=%s AND owner_id=%s AND enabled FOR UPDATE",
                (identifier, user["id"]),
            )
        ).fetchone()
        if not current or current["revision"] != body.revision:
            fail(409, "AGENT_CONFLICT", "智能体已修改，请重新发布")
        snapshot["quality_gate"] = await publication_guard(
            connection, user["id"], snapshot.get("knowledge_ids", []))
        row = await (
            await connection.execute(
                "INSERT INTO agent_versions(id,agent_id,owner_id,version,revision,snapshot) "
                "SELECT %s,%s,%s,coalesce(max(version),0)+1,%s,%s FROM agent_versions WHERE agent_id=%s "
                "RETURNING id,version,revision,active",
                (uuid4(), identifier, user["id"], body.revision, Jsonb(snapshot), identifier),
            )
        ).fetchone()
    return row


@router.put("/{identifier}/versions/{version}/state")
async def state(identifier: UUID, version: UUID, body: State, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "UPDATE agent_versions SET active=%s WHERE id=%s AND agent_id=%s AND owner_id=%s RETURNING id,active",
        (body.active, version, identifier, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "智能体版本不存在或无权访问")
    return rows[0]
