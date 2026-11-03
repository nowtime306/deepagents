"""Private immutable publication previews, current selection and protected draft copies."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.security import identity
from app.workflow_api import validate_definition
from app.workflow_definition import Definition, Position, validate_draft
from app.workflow_schema import validate_schema

router = APIRouter(prefix="/api/v1/workflows", tags=["工作流版本管理"])


class DraftRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class ReleaseRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_revision: int = Field(ge=1)


class CopyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)


async def owned(connection, identifier, owner, *, locked=False) -> dict:
    suffix = " FOR UPDATE" if locked else ""
    row = await (await connection.execute(
        "SELECT * FROM workflows WHERE id=%s AND owner_id=%s" + suffix, (identifier, owner),
    )).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "工作流不存在或无权访问")
    return row


async def historical(connection, identifier, version) -> dict:
    row = await (await connection.execute(
        "SELECT * FROM workflow_versions WHERE workflow_id=%s AND version=%s", (identifier, version),
    )).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "历史发布版本不存在")
    return row


def editable(source: dict) -> Definition:
    """Allow resource repair but never reinterpret an unsupported historical schema."""
    definition = Definition.model_validate(source["definition"])
    validate_draft(definition)
    validate_schema(definition.input_schema)
    validate_schema(definition.output_schema)
    layout = source.get("layout") or {}
    if not isinstance(layout, dict):
        raise ValueError("历史布局不是节点坐标对象")
    if set(layout) - {node.id for node in definition.nodes}:
        raise ValueError("历史布局引用不存在的节点")
    for position in layout.values():
        Position.model_validate(position)
    return definition


def supported(source: dict) -> Definition:
    try:
        return editable(source)
    except (ValueError, TypeError):
        fail(422, "WORKFLOW_VERSION_SCHEMA_UNSUPPORTED", "当前编辑器不支持该历史结构或Schema，未修改原草稿")


def canvas_projection(source: dict) -> dict:
    try:
        return {"schema_supported": True, "canvas_definition": editable(source).model_dump()}
    except (ValueError, TypeError):
        return {"schema_supported": False, "canvas_definition": None}


def draft_response(source: dict) -> dict:
    return {**source, "definition": Definition.model_validate(source["definition"]).model_dump()}


async def insert_draft(connection, owner, source, name) -> UUID:
    identifier = uuid4()
    await connection.execute(
        "INSERT INTO workflows(id,owner_id,name,instructions,model_id,definition,layout) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (identifier, owner, name, source["instructions"], source["model_id"],
         Jsonb(source["definition"]), Jsonb(source.get("layout") or {})),
    )
    return identifier


@router.get("/{identifier}")
async def detail(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        return draft_response(await owned(connection, identifier, owner))


@router.get("/{identifier}/versions/{version}")
async def version_detail(identifier: UUID, version: int, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        workflow = await owned(connection, identifier, owner)
        source = await historical(connection, identifier, version)
    return {**source, **canvas_projection(source), "layout_known": bool(source["layout"]),
            "is_current": workflow["current_published_version"] == version,
            "current_published_version": workflow["current_published_version"],
            "release_revision": workflow["release_revision"]}


@router.post("/{identifier}/versions/{version}/set-current")
async def set_current(identifier: UUID, version: int, body: ReleaseRevision, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        workflow = await owned(connection, identifier, owner, locked=True)
        if workflow["release_revision"] != body.release_revision:
            fail(409, "WORKFLOW_RELEASE_CONFLICT", "当前发布选择已改变，请刷新后重试")
        source = await historical(connection, identifier, version)
        if not source["active"]:
            fail(409, "WORKFLOW_VERSION_INACTIVE", "该历史版本已下线，请先明确启用")
        definition = supported(source)
        pins = await validate_definition(request, definition, owner, source["model_id"], source["layout"])
        if (not source["digest"] or not source["resource_pins"] or source["resource_pins"] != pins
                or source["model_revision"] != pins["models"][source["model_id"]]):
            fail(409, "WORKFLOW_RESOURCE_CHANGED", "历史资源版本已变化或记录不完整，请恢复草稿修复后测试发布")
        row = await (await connection.execute(
            "UPDATE workflows SET current_published_version=%s,release_revision=release_revision+1 WHERE id=%s "
            "RETURNING current_published_version,release_revision", (version, identifier),
        )).fetchone()
    return row


@router.post("/{identifier}/versions/{version}/copy", status_code=201)
async def copy_version(identifier: UUID, version: int, body: CopyInput, request: Request):
    owner = (await identity(request))["id"]
    try:
        async with request.app.state.db.pool.connection() as connection:
            await owned(connection, identifier, owner, locked=True)
            source = await historical(connection, identifier, version)
            supported(source)
            copied = await insert_draft(connection, owner, source, body.name)
    except UniqueViolation:
        fail(409, "WORKFLOW_NAME_CONFLICT", "工作流名称已存在，请选择新名称")
    return {"id": copied, "revision": 1, "current_published_version": None, "release_revision": 1}


@router.post("/{identifier}/versions/{version}/restore")
async def restore_version(identifier: UUID, version: int, body: DraftRevision, request: Request):
    owner = (await identity(request))["id"]
    try:
        async with request.app.state.db.pool.connection() as connection:
            workflow = await owned(connection, identifier, owner, locked=True)
            if workflow["revision"] != body.revision:
                fail(409, "WORKFLOW_CONFLICT", "草稿已改变；未恢复或创建备份，请刷新后重试")
            source = await historical(connection, identifier, version)
            supported(source)
            backup_name = "恢复前草稿 · " + workflow["name"][:65] + " · " + uuid4().hex[:12]
            backup = await insert_draft(connection, owner, workflow, backup_name)
            row = await (await connection.execute(
                "UPDATE workflows SET name=%s,instructions=%s,model_id=%s,definition=%s,layout=%s,"
                "revision=revision+1,tested_revision=NULL,tested_pins=NULL WHERE id=%s RETURNING *",
                (source["name"], source["instructions"], source["model_id"], Jsonb(source["definition"]),
                 Jsonb(source["layout"]), identifier),
            )).fetchone()
    except UniqueViolation:
        fail(409, "WORKFLOW_NAME_CONFLICT", "历史名称已被其他工作流占用，未覆盖草稿或创建备份")
    return {"id": identifier, "revision": row["revision"], "draft_backup_id": backup,
            "draft_backup_name": backup_name, "workflow": draft_response(row)}
