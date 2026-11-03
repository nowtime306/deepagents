"""Create explicit durable Work roots for owner-scoped fixed workflow execution."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.permissions import require_account
from app.security import identity
from app.work_network import domain, policy
from app.workflow_api import owned, validate_definition
from app.workflow_definition import Definition, node_preview
from app.workflow_schema import validate_text

router = APIRouter(prefix="/api/v1/workflows", tags=["工作流任务"])


class TaskRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)
    revision: int | None = Field(default=None, ge=1)
    version: int | None = Field(default=None, ge=1)
    network_domains: list[str] = Field(default_factory=list, max_length=10)
    repository_id: UUID | None = None


@router.post("/{identifier}/work-runs", status_code=202)
async def start(identifier: UUID, body: TaskRunInput, request: Request) -> dict:
    return await start_task(identifier, body, request)


async def start_task(
    identifier: UUID, body: TaskRunInput, request: Request, *,
    node_id: str | None = None, run_identifier: UUID | None = None,
) -> dict:
    user = await identity(request)
    for permission in ("workflows.execute", "work.read", "work.execute", "work.manage"):
        await require_account(request.app.state.db, user["id"], permission)
    if not request.app.state.settings.work_enabled or not await request.app.state.db.fetch(
        "SELECT 1 FROM work_worker_instances WHERE heartbeat_at>now()-interval '45 seconds' LIMIT 1"
    ):
        fail(503, "WORKER_OFFLINE", "请先启动任务后台")
    if (body.version is None) == (body.revision is None):
        fail(422, "WORKFLOW_SELECTION_INVALID", "请选择草稿修订或发布版本，不能同时选择")
    workflow = await owned(request, identifier, user["id"])
    source = workflow
    if body.version:
        rows = await request.app.state.db.fetch(
            "SELECT * FROM workflow_versions WHERE workflow_id=%s AND version=%s AND active", (identifier, body.version)
        )
        if not rows:
            fail(404, "NOT_FOUND", "发布版本不存在或已下线")
        source = rows[0]
    definition = Definition.model_validate(source["definition"])
    if node_id:
        try:
            definition = node_preview(definition, node_id, text=body.text)
        except ValueError as error:
            fail(422, "WORKFLOW_NODE_PREVIEW_INVALID", str(error))
    if definition.execution != "work":
        fail(422, "WORKFLOW_READONLY", "只读流程请使用原来的测试或版本运行入口")
    validate_text(body.text, definition.input_schema)
    pins = await validate_definition(request, definition, user["id"], source["model_id"])
    if body.version and source["resource_pins"] != pins:
        fail(409, "WORKFLOW_RESOURCE_CHANGED", "发布资源已变化，请重新测试和发布")
    try:
        domains = list(dict.fromkeys(domain(item) for item in body.network_domains))
        await policy(domains)
    except (ValueError, OSError):
        fail(422, "NETWORK_DOMAIN_INVALID", "任务网络域名无效或不是公网地址")
    from app.repository_runs import attach, freeze

    frozen_repository = await freeze(request, user["id"], body.repository_id)
    task_id, run_id, workflow_run = uuid4(), uuid4(), run_identifier or uuid4()
    revision = source["draft_revision"] if body.version else body.revision
    snapshot = {"name": source["name"], "instructions": source["instructions"], "model_id": source["model_id"],
                "revision": revision, "definition": definition.model_dump(), "resource_pins": pins,
                "parent_work_run": str(run_id),
                **({"debug_node_id": node_id, "canvas_definition": source["definition"]} if node_id else {}),
                **({"published_version": body.version} if body.version else {})}
    async with request.app.state.db.pool.connection() as connection:
        current = await (await connection.execute(
            "SELECT revision FROM workflows WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, user["id"])
        )).fetchone()
        if not current or (body.revision is not None and current["revision"] != body.revision):
            fail(409, "WORKFLOW_CONFLICT", "草稿已变化，请刷新后运行")
        if body.version and not await (await connection.execute(
            "SELECT 1 FROM workflow_versions WHERE id=%s AND active FOR SHARE", (source["id"],)
        )).fetchone():
            fail(409, "WORKFLOW_VERSION_UNAVAILABLE", "发布版本已下线")
        inserted = await (await connection.execute(
            "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'RUNNING',%s,%s) "
            "ON CONFLICT(id) DO NOTHING RETURNING id",
            (workflow_run, identifier, body.text, Jsonb(snapshot)),
        )).fetchone()
        if not inserted:
            fail(409, "WORKFLOW_RUN_EXISTS", "运行编号已使用，请查看原运行，不要自动重试")
        await connection.execute(
            "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,%s,%s)",
            (task_id, user["id"], source["name"] + " · 流程任务", "执行固定工作流：" + source["name"]),
        )
        await connection.execute("INSERT INTO work_task_workflows(task_id,workflow_id) VALUES(%s,%s)",
                                 (task_id, identifier))
        await connection.execute(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,model_revision,"
            "environment,step_limit,token_limit,agent_snapshot,instructions,network_domains,resource_budgeted) "
            "VALUES(%s,%s,%s,%s,%s,1,%s,%s,'coding',%s,%s,'{}',%s,%s,true)",
            (run_id, task_id, user["id"], source["name"] + " · 流程任务", "执行固定工作流：" + source["name"],
             source["model_id"], pins["models"][source["model_id"]], definition.model_calls, definition.token_limit,
             source["instructions"], domains),
        )
        await connection.execute(
            "INSERT INTO work_run_workflows(run_id,workflow_run_id,version_id) VALUES(%s,%s,%s)",
            (run_id, workflow_run, source["id"] if body.version else None),
        )
        await attach(connection, user["id"], run_id, frozen_repository)
    return {"run_id": run_id, "task_id": task_id, "workflow_run_id": workflow_run, "status": "PENDING"}


async def sync(connection, run: UUID, status: str, *, result: dict | None = None) -> None:
    """Called in the same transaction as the owning Work root's final transition."""
    row = await (await connection.execute(
        "SELECT w.workflow_run_id,r.workflow_id,r.snapshot FROM work_run_workflows w "
        "JOIN workflow_runs r ON r.id=w.workflow_run_id WHERE w.run_id=%s", (run,)
    )).fetchone()
    if not row:
        return
    output = (result or {}).get("text")
    await connection.execute(
        "UPDATE workflow_runs SET status=%s,output=%s,finished_at=now() WHERE id=%s "
        "AND status IN ('RUNNING','PENDING')", (status, output, row["workflow_run_id"]),
    )
    if (
        status == "SUCCEEDED"
        and not row["snapshot"].get("published_version")
        and not row["snapshot"].get("debug_node_id")
    ):
        await connection.execute(
            "UPDATE workflows SET tested_revision=revision,tested_pins=%s WHERE id=%s AND revision=%s",
            (Jsonb(row["snapshot"]["resource_pins"]), row["workflow_id"], row["snapshot"]["revision"]),
        )


async def reconcile(database) -> None:
    """Make API cancellation, expired leases and interrupted workers visible on the workflow."""
    async with database.pool.connection() as connection:
        rows = await (await connection.execute(
            "SELECT w.run_id,r.status,r.result FROM work_run_workflows w JOIN work_queue_runs r ON r.id=w.run_id "
            "JOIN workflow_runs f ON f.id=w.workflow_run_id WHERE f.status IN ('PENDING','RUNNING') "
            "AND r.status IN ('COMPLETED','FAILED','CANCELLED','NEEDS_REVIEW')"
        )).fetchall()
        for row in rows:
            await sync(connection, row["run_id"], {"COMPLETED": "SUCCEEDED"}.get(row["status"], row["status"]),
                       result=row["result"])
