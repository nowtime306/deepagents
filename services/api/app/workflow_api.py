"""Owner-scoped editable workflow DAGs, debug evidence and tested immutable versions."""

import asyncio
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import APIError, fail
from app.list_pagination import decode_cursor, page
from app.security import identity
from app.workflow_compiler import WorkflowNodeFailed
from app.workflow_definition import (
    Definition,
    Position,
    configuration_issues,
    default_definition,
    node_preview,
    validate,
    validate_draft,
)
from app.workflow_runtime import WorkflowRuntime, digest
from app.workflow_schema import validate_text

router = APIRouter(prefix="/api/v1/workflows", tags=["工作流"])


class WorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=12000)
    revision: int | None = Field(default=None, ge=1)
    definition: Definition | None = None
    layout: dict[str, Position] | None = None
    allow_incomplete: bool = False


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)
    revision: int | None = Field(default=None, ge=1)
    run_id: UUID | None = None


class NodeTestInput(TestInput):
    revision: int = Field(ge=1)
    variables: dict[str, Annotated[str, Field(max_length=16000)]] = Field(default_factory=dict, max_length=30)


class PublishInput(BaseModel):
    revision: int = Field(ge=1)


class VersionState(BaseModel):
    active: bool


def runtime(request: Request) -> WorkflowRuntime:
    return WorkflowRuntime(
        request.app.state.db,
        request.app.state.models,
        request.app.state.capabilities,
        request.app.state.runtime.model_factory,
    )


async def owned(request: Request, identifier: UUID, owner: UUID) -> dict:
    rows = await request.app.state.db.fetch("SELECT * FROM workflows WHERE id=%s AND owner_id=%s", (identifier, owner))
    if not rows:
        fail(404, "NOT_FOUND", "工作流不存在或无权访问")
    return rows[0]


async def validate_definition(
    request: Request, definition: Definition, owner: UUID, model_id: str, layout: dict | None = None
) -> dict:
    try:
        validate(definition)
        if layout and (set(layout) - {node.id for node in definition.nodes}):
            raise ValueError("布局引用了不存在的节点")
        return await runtime(request).pins(definition, owner, model_id, allow_work=True)
    except ValueError as error:
        fail(422, "WORKFLOW_INVALID", str(error))


async def validate_edit(request: Request, body: WorkflowInput, definition: Definition, owner: UUID) -> None:
    if not body.allow_incomplete:
        await validate_definition(request, definition, owner, body.model_id, body.layout)
        return
    try:
        validate_draft(definition)
        if body.layout and set(body.layout) - {node.id for node in definition.nodes}:
            raise ValueError("布局引用了不存在的节点")
        await runtime(request).model_pin(body.model_id, {"models": {}})
    except ValueError as error:
        fail(422, "WORKFLOW_INVALID", str(error))


class ValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: Definition


@router.post("/validate")
async def check_definition(body: ValidationInput, request: Request) -> dict:
    """Inspect configuration without accessing resources or creating execution evidence."""
    await identity(request)
    issues = configuration_issues(body.definition)
    return {"valid": not issues, "scope": "configuration", "issues": issues}


@router.get("/options")
async def options(request: Request) -> dict:
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT v.id,v.name || ' · 版本 ' || v.version::text AS name FROM workflow_versions v "
            "JOIN workflows w ON w.id=v.workflow_id WHERE w.owner_id=%s AND v.active "
            "AND COALESCE(v.definition->>'execution','readonly')='readonly' "
            "ORDER BY v.created_at DESC LIMIT 200",
            (user["id"],),
        )
    }


@router.get("")
async def listing(request: Request, before: str | None = None, q: str = "", identifier: UUID | None = None) -> dict:
    user = await identity(request)
    timestamp, cursor_id = decode_cursor(before, user["id"], "workflows")
    if len(q) > 200:
        fail(422, "INVALID_INPUT", "搜索词不能超过200字")
    rows = await request.app.state.db.fetch(
        "SELECT * FROM workflows WHERE owner_id=%s AND (strpos(lower(name),lower(%s))>0 "
        "OR strpos(lower(instructions),lower(%s))>0) AND (%s::uuid IS NULL OR id=%s) "
        "AND (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s::uuid)) "
        "ORDER BY created_at DESC,id DESC LIMIT 101",
        (user["id"], q, q, identifier, identifier, timestamp, timestamp, cursor_id),
    )
    for row in rows:
        row["definition"] = Definition.model_validate(row["definition"]).model_dump()
    return page(rows, 100, user["id"], "workflows")


@router.post("", status_code=201)
async def create(body: WorkflowInput, request: Request) -> dict:
    user = await identity(request)
    definition = body.definition or default_definition()
    await validate_edit(request, body, definition, user["id"])
    identifier = uuid4()
    try:
        await request.app.state.db.execute(
            "INSERT INTO workflows(id,owner_id,name,instructions,model_id,definition,layout) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                user["id"],
                body.name,
                body.instructions,
                body.model_id,
                Jsonb(definition.model_dump()),
                Jsonb({key: value.model_dump() for key, value in (body.layout or {}).items()}),
            ),
        )
    except UniqueViolation:
        fail(409, "WORKFLOW_NAME_CONFLICT", "工作流名称已存在")
    return {"id": identifier}


@router.put("/{identifier}")
async def update(identifier: UUID, body: WorkflowInput, request: Request) -> dict:
    user = await identity(request)
    current = await owned(request, identifier, user["id"])
    definition = body.definition or Definition.model_validate(current["definition"])
    await validate_edit(request, body, definition, user["id"])
    layout = (
        {key: value.model_dump() for key, value in body.layout.items()}
        if body.layout is not None
        else current["layout"]
    )
    unchanged = (
        definition.model_dump() == Definition.model_validate(current["definition"]).model_dump()
        and body.model_id == current["model_id"]
        and body.instructions == current["instructions"]
    )
    try:
        rows = await request.app.state.db.fetch(
            "UPDATE workflows SET name=%s,instructions=%s,model_id=%s,definition=%s,layout=%s,revision=revision+1,"
            "tested_revision=CASE WHEN %s AND tested_revision=revision THEN revision+1 ELSE NULL END,"
            "tested_pins=CASE WHEN %s THEN tested_pins ELSE NULL END "
            "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id",
            (
                body.name,
                body.instructions,
                body.model_id,
                Jsonb(definition.model_dump()),
                Jsonb(layout),
                unchanged,
                unchanged,
                identifier,
                user["id"],
                body.revision,
            ),
        )
    except UniqueViolation:
        fail(409, "WORKFLOW_NAME_CONFLICT", "工作流名称已存在")
    if not rows:
        fail(409, "WORKFLOW_CONFLICT", "工作流已修改，请刷新重试")
    return {"id": identifier}


@router.get("/{identifier}/runs")
async def runs(identifier: UUID, request: Request, before: str | None = None, run_id: UUID | None = None) -> dict:
    user = await identity(request)
    await owned(request, identifier, user["id"])
    scope = "workflow-runs:" + str(identifier)
    timestamp, cursor_id = decode_cursor(before, user["id"], scope)
    rows = await request.app.state.db.fetch(
        "SELECT f.*,wr.task_id,wr.status AS work_status,coalesce(wr.id,f.id) AS routing_run_id,"
        "EXISTS(SELECT 1 FROM model_route_selections s WHERE s.run_id=coalesce(wr.id,f.id) "
        "AND s.owner_id=%s) AS routed FROM workflow_runs f "
        "LEFT JOIN work_run_workflows wf ON wf.workflow_run_id=f.id "
        "LEFT JOIN work_queue_runs wr ON wr.id=wf.run_id "
        "WHERE f.workflow_id=%s AND (%s::uuid IS NULL OR f.id=%s) "
        "AND (%s::timestamptz IS NULL OR (f.created_at,f.id)<(%s,%s::uuid)) "
        "ORDER BY f.created_at DESC,f.id DESC LIMIT 51",
        (user["id"], identifier, run_id, run_id, timestamp, timestamp, cursor_id),
    )
    return page(rows, 50, user["id"], scope)


@router.get("/{identifier}/runs/{run_id}/events")
async def events(identifier: UUID, run_id: UUID, request: Request) -> dict:
    user = await identity(request)
    await owned(request, identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT 1 FROM workflow_runs WHERE id=%s AND workflow_id=%s", (run_id, identifier)
    )
    if not rows:
        fail(404, "NOT_FOUND", "运行不存在")
    return {
        "items": await request.app.state.db.fetch(
            "SELECT event_id,node_id,event,data,created_at FROM workflow_node_events WHERE run_id=%s ORDER BY event_id",
            (run_id,),
        )
    }


@router.get("/{identifier}/versions")
async def versions(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    await owned(request, identifier, user["id"])
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,version,draft_revision,model_id,model_revision,name,instructions,digest,active,"
            "created_at,definition,"
            "COALESCE(definition->'input_schema','{\"type\":\"string\"}'::jsonb) AS input_schema,"
            "COALESCE(definition->>'execution','readonly') AS execution "
            "FROM workflow_versions WHERE workflow_id=%s ORDER BY version DESC",
            (identifier,),
        )
    }


@router.post("/{identifier}/publish", status_code=201)
async def publish(identifier: UUID, body: PublishInput, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        workflow = await (
            await connection.execute(
                "SELECT * FROM workflows WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, user["id"])
            )
        ).fetchone()
        if not workflow:
            fail(404, "NOT_FOUND", "工作流不存在或无权访问")
        if workflow["revision"] != body.revision:
            fail(409, "WORKFLOW_CONFLICT", "草稿已更改，请刷新后再发布")
        definition = Definition.model_validate(workflow["definition"])
        pins = await validate_definition(request, definition, user["id"], workflow["model_id"])
        model_revision = pins["models"][workflow["model_id"]]
        existing = await (
            await connection.execute(
                "SELECT version,resource_pins FROM workflow_versions "
                "WHERE workflow_id=%s AND draft_revision=%s AND model_revision=%s",
                (identifier, workflow["revision"], model_revision),
            )
        ).fetchone()
        if existing and (not existing["resource_pins"] or existing["resource_pins"] == pins):
            return {"version": existing["version"]}
        if workflow["tested_revision"] != workflow["revision"] or workflow["tested_pins"] != pins:
            fail(409, "WORKFLOW_UNTESTED", "请先测试通过当前草稿及资源配置，再发布版本")
        if existing:
            fail(409, "WORKFLOW_RESOURCE_CHANGED", "资源已变化，请保存新修订后测试发布")
        number = (
            await (
                await connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 AS version FROM workflow_versions WHERE workflow_id=%s",
                    (identifier,),
                )
            ).fetchone()
        )["version"]
        checksum = digest(
            {
                "definition": definition.model_dump(),
                "instructions": workflow["instructions"],
                "model_id": workflow["model_id"],
                "pins": pins,
            }
        )
        await connection.execute(
            "INSERT INTO workflow_versions(id,workflow_id,version,draft_revision,model_id,model_revision,"
            "name,instructions,"
            "definition,resource_pins,digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                identifier,
                number,
                workflow["revision"],
                workflow["model_id"],
                model_revision,
                workflow["name"],
                workflow["instructions"],
                Jsonb(definition.model_dump()),
                Jsonb(pins),
                checksum,
            ),
        )
    return {"version": number}


@router.put("/{identifier}/versions/{version}/state")
async def version_state(identifier: UUID, version: int, body: VersionState, request: Request) -> dict:
    user = await identity(request)
    await owned(request, identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "UPDATE workflow_versions SET active=%s WHERE workflow_id=%s AND version=%s RETURNING id",
        (body.active, identifier, version),
    )
    if not rows:
        fail(404, "NOT_FOUND", "发布版本不存在")
    return {"active": body.active}


async def execute(
    request: Request, workflow: dict, owner: UUID, text: str, *,
    published: dict | None = None, node_id: str | None = None, run_identifier: UUID | None = None,
    variables: dict[str, str] | None = None,
) -> dict:
    source = published or workflow
    definition = Definition.model_validate(source["definition"])
    if node_id:
        try:
            definition = node_preview(definition, node_id, text=text, variables=variables)
        except ValueError as error:
            fail(422, "WORKFLOW_NODE_PREVIEW_INVALID", str(error))
    if definition.execution != "readonly":
        fail(422, "WORKFLOW_WORK_REQUIRED", "此流程包含持久审批或代码，请使用任务运行入口")
    validate_text(text, definition.input_schema)
    pins = await validate_definition(request, definition, owner, source["model_id"])
    if published and (
        not source["active"]
        or source["model_revision"] != pins["models"][source["model_id"]]
        or source["resource_pins"]
        and source["resource_pins"] != pins
    ):
        fail(409, "WORKFLOW_RESOURCE_CHANGED", "发布版本已下线或模型/资源配置已变化，请重新测试和发布")
    identifier = run_identifier or uuid4()
    revision = source["draft_revision"] if published else source["revision"]
    snapshot = {
        "name": source["name"],
        "instructions": source["instructions"],
        "model_id": source["model_id"],
        "revision": revision,
        "definition": definition.model_dump(),
        "resource_pins": pins,
        **({"published_version": source["version"], "model_revision": source["model_revision"]} if published else {}),
        **({"debug_node_id": node_id, "debug_variables": variables or {},
            "canvas_definition": source["definition"]} if node_id else {}),
    }
    async with request.app.state.db.pool.connection() as connection:
        locked = await (
            await connection.execute(
                "SELECT revision FROM workflows WHERE id=%s AND owner_id=%s FOR UPDATE", (workflow["id"], owner)
            )
        ).fetchone()
        if not locked or (not published and locked["revision"] != revision):
            fail(409, "WORKFLOW_CONFLICT", "草稿已改变，请刷新后测试")
        try:
            await connection.execute(
                "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'RUNNING',%s,%s)",
                (identifier, workflow["id"], text, Jsonb(snapshot)),
            )
        except UniqueViolation:
            fail(409, "WORKFLOW_RUN_EXISTS", "运行编号已使用，请查看原运行，不要自动重试")
    try:
        async with asyncio.timeout(90):
            output = await runtime(request).run(
                definition, owner, source["model_id"], source["instructions"], text, identifier, pins
            )
        async with request.app.state.db.pool.connection() as connection:
            await connection.execute(
                "UPDATE workflow_runs SET status='SUCCEEDED',output=%s,finished_at=now() WHERE id=%s",
                (output, identifier),
            )
            if not published and not node_id:
                await connection.execute(
                    "UPDATE workflows SET tested_revision=revision,tested_pins=%s WHERE id=%s AND revision=%s",
                    (Jsonb(pins), workflow["id"], revision),
                )
        return {"id": identifier, "status": "SUCCEEDED", "output": output}
    except BaseException as error:
        await request.app.state.db.execute(
            "UPDATE workflow_runs SET status='FAILED',error_node=%s,finished_at=now() WHERE id=%s",
            (error.node_id if isinstance(error, WorkflowNodeFailed) else None, identifier),
        )
        if not isinstance(error, Exception):
            raise
        if isinstance(error, APIError):
            raise
        fail(502, "WORKFLOW_RUN_FAILED", "工作流运行失败，请查看节点记录并检查模型或资源配置")


@router.post("/{identifier}/test-runs")
async def test_run(identifier: UUID, body: TestInput, request: Request) -> dict:
    user = await identity(request)
    workflow = await owned(request, identifier, user["id"])
    if body.revision is not None and body.revision != workflow["revision"]:
        fail(409, "WORKFLOW_CONFLICT", "草稿已改变，请刷新后测试")
    return await execute(request, workflow, user["id"], body.text, run_identifier=body.run_id)


@router.post("/{identifier}/nodes/{node_id}/test-runs")
async def test_node(identifier: UUID, node_id: str, body: NodeTestInput, request: Request) -> dict:
    user = await identity(request)
    workflow = await owned(request, identifier, user["id"])
    if body.revision != workflow["revision"]:
        fail(409, "WORKFLOW_CONFLICT", "草稿已改变，请刷新后测试")
    selected = next((node for node in workflow["definition"]["nodes"] if node["id"] == node_id), None)
    if selected and selected["kind"] in {"code", "approval"}:
        from app.workflow_tasks import TaskRunInput, start_task

        return await start_task(identifier, TaskRunInput(text=body.text, revision=body.revision), request,
                                node_id=node_id, run_identifier=body.run_id)
    return await execute(request, workflow, user["id"], body.text, node_id=node_id, run_identifier=body.run_id,
                         variables=body.variables)


@router.post("/{identifier}/versions/{version}/runs")
async def published_run(identifier: UUID, version: int, body: TestInput, request: Request) -> dict:
    user = await identity(request)
    workflow = await owned(request, identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT * FROM workflow_versions WHERE workflow_id=%s AND version=%s", (identifier, version)
    )
    if not rows:
        fail(404, "NOT_FOUND", "发布版本不存在")
    return await execute(request, workflow, user["id"], body.text, published=rows[0], run_identifier=body.run_id)


@router.delete("/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        row = await connection.execute(
            "SELECT 1 FROM workflows WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, user["id"])
        )
        if not await row.fetchone():
            fail(404, "NOT_FOUND", "工作流不存在或无权访问")
        active = await connection.execute(
            "SELECT 1 FROM workflow_runs WHERE workflow_id=%s AND status IN ('PENDING','RUNNING')", (identifier,)
        )
        if await active.fetchone():
            fail(409, "WORKFLOW_RUNNING", "工作流运行中，请稍后删除")
        await connection.execute(
            "UPDATE api_credentials SET revoked_at=COALESCE(revoked_at,now()) "
            "WHERE version_id IN (SELECT id FROM workflow_versions WHERE workflow_id=%s)",
            (identifier,),
        )
        await connection.execute("DELETE FROM workflows WHERE id=%s AND owner_id=%s", (identifier, user["id"]))
    return {"ok": True}
