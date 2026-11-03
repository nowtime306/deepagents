"""Private agent configurations for the authenticated account."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.errors import fail
from app.model_catalog import supports_chat
from app.security import identity
from app.team_config import TeamMember

router = APIRouter(prefix="/api/v1/agents", tags=["智能体"])


class AgentInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    instructions: str = Field(default="", max_length=12000)
    model_id: str = Field(min_length=1, max_length=120)
    model_routing: bool = False
    enabled: bool = True
    workflow_version_ids: list[UUID] | None = Field(default=None, max_length=20)
    memory_enabled: bool | None = None
    web_enabled: bool = False
    web_mode: Literal["tavily", "openai_native"] | None = None
    knowledge_ids: list[UUID] | None = Field(default=None, max_length=100)
    skill_ids: list[UUID] | None = Field(default=None, max_length=100)
    mcp_ids: list[UUID] | None = Field(default=None, max_length=100)
    revision: int | None = Field(default=None, ge=1)
    team: list[TeamMember] | None = Field(default=None, max_length=6)


async def check_model(request: Request, body: AgentInput) -> None:
    config = await request.app.state.models.get(body.model_id)
    if not supports_chat(config.kind, config.protocol):
        fail(422, "MODEL_KIND_INVALID", "请选择可用的聊天模型")
    if (
        body.web_enabled
        and body.web_mode == "openai_native"
        and (config.provider != "openai" or config.protocol != "openai")
    ):
        fail(422, "WEB_MODE_UNSUPPORTED", "OpenAI 原生联网需要选择 OpenAI 服务商的聊天模型")


@router.get("")
async def listing(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT id,name,instructions,model_id,enabled,web_enabled,web_mode,revision,knowledge_ids,skill_ids,mcp_ids,"
        "memory_enabled,workflow_version_ids,team,model_routing "
        "FROM agents "
        "WHERE owner_id=%s ORDER BY created_at,id",
        (user["id"],),
    )
    return {"items": rows}


@router.post("", status_code=201)
async def create(body: AgentInput, request: Request) -> dict:
    user = await identity(request)
    body = body.model_copy(update={"web_mode": body.web_mode or "tavily"})
    await check_model(request, body)
    selection = {
        key: body.model_dump(mode="json")[key] or []
        for key in ("knowledge_ids", "skill_ids", "mcp_ids", "workflow_version_ids")
    }
    await request.app.state.capabilities.validate(user["id"], selection)
    await request.app.state.capabilities.freeze(user["id"], {**body.model_dump(mode="json"), **selection})
    identifier = uuid4()
    try:
        await request.app.state.db.execute(
            "INSERT INTO agents(id,owner_id,name,instructions,model_id,enabled,web_enabled,web_mode,"
            "knowledge_ids,skill_ids,mcp_ids,memory_enabled,workflow_version_ids,team,model_routing) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                user["id"],
                body.name,
                body.instructions,
                body.model_id,
                body.enabled,
                body.web_enabled,
                body.web_mode,
                Jsonb(selection["knowledge_ids"]),
                Jsonb(selection["skill_ids"]),
                Jsonb(selection["mcp_ids"]),
                bool(body.memory_enabled),
                Jsonb(selection["workflow_version_ids"]),
                Jsonb(body.model_dump(mode="json")["team"] or []),
                body.model_routing,
            ),
        )
    except UniqueViolation:
        fail(409, "AGENT_NAME_CONFLICT", "智能体名称已存在")
    return {"id": identifier}


@router.put("/{identifier}")
async def update(identifier: UUID, body: AgentInput, request: Request) -> dict:
    user = await identity(request)
    existing = await request.app.state.db.fetch(
        "SELECT knowledge_ids,skill_ids,mcp_ids,web_mode,memory_enabled,workflow_version_ids,team FROM "
        "agents WHERE id=%s AND owner_id=%s",
        (identifier, user["id"]),
    )
    if not existing:
        fail(404, "AGENT_NOT_FOUND", "智能体不存在或无权访问")
    body = body.model_copy(update={"web_mode": body.web_mode or existing[0]["web_mode"]})
    await check_model(request, body)
    payload = body.model_dump(mode="json")
    selection = {
        key: existing[0][key] if payload[key] is None else payload[key]
        for key in ("knowledge_ids", "skill_ids", "mcp_ids", "workflow_version_ids")
    }
    await request.app.state.capabilities.validate(user["id"], selection)
    team = existing[0]["team"] if body.team is None else payload["team"]
    await request.app.state.capabilities.freeze(
        user["id"],
        {
            **payload,
            **selection,
            "id": str(identifier),
            "team": team,
            "memory_enabled": existing[0]["memory_enabled"] if body.memory_enabled is None else body.memory_enabled,
        },
    )
    try:
        rows = await request.app.state.db.fetch(
            "UPDATE agents SET name=%s,instructions=%s,model_id=%s,enabled=%s,web_enabled=%s,web_mode=%s,"
            "knowledge_ids=%s,skill_ids=%s,mcp_ids=%s,memory_enabled=%s,workflow_version_ids=%s,"
            "team=%s,model_routing=%s,revision=revision+1 "
            "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id",
            (
                body.name,
                body.instructions,
                body.model_id,
                body.enabled,
                body.web_enabled,
                body.web_mode,
                Jsonb(selection["knowledge_ids"]),
                Jsonb(selection["skill_ids"]),
                Jsonb(selection["mcp_ids"]),
                existing[0]["memory_enabled"] if body.memory_enabled is None else body.memory_enabled,
                Jsonb(selection["workflow_version_ids"]),
                Jsonb(team),
                body.model_routing,
                identifier,
                user["id"],
                body.revision,
            ),
        )
    except UniqueViolation:
        fail(409, "AGENT_NAME_CONFLICT", "智能体名称已存在")
    if not rows:
        fail(409, "AGENT_CONFLICT", "智能体不存在或已被修改，请刷新后重试")
    return {"id": identifier}


@router.delete("/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM agents WHERE id=%s AND owner_id=%s "
        "AND NOT EXISTS (SELECT 1 FROM conversations WHERE agent_id=%s) RETURNING id",
        (identifier, user["id"], identifier),
    )
    if not rows:
        fail(409, "AGENT_IN_USE", "智能体不存在或已有对话，请先停用")
    return {"ok": True}
