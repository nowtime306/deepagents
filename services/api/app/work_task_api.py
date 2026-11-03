"""User-owned durable work-task drafts, before execution is enabled."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.list_pagination import decode_cursor, page
from app.security import identity
from app.work_input_api import storage

router = APIRouter(prefix="/api/v1/work-tasks", tags=["任务草稿"])


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=12000)
    revision: int | None = Field(default=None, ge=1)


@router.get("")
async def listing(request: Request, before: str | None = None, q: str = "") -> dict:
    user = await identity(request)
    timestamp, cursor_id = decode_cursor(before, user["id"], "work-tasks")
    if len(q) > 200:
        fail(422, "INVALID_INPUT", "搜索词不能超过200字")
    rows = await request.app.state.db.fetch(
        "SELECT t.id,t.title,t.goal,t.status,t.revision,t.created_at,t.updated_at,"
        "EXISTS(SELECT 1 FROM work_task_workflows f WHERE f.task_id=t.id) AS workflow_task FROM work_tasks t "
        "WHERE t.owner_id=%s AND (strpos(lower(t.title),lower(%s))>0 OR strpos(lower(t.goal),lower(%s))>0) "
        "AND (%s::timestamptz IS NULL OR (t.updated_at,t.id)<(%s,%s::uuid)) "
        "ORDER BY t.updated_at DESC,t.id DESC LIMIT 101",
        (user["id"], q, q, timestamp, timestamp, cursor_id),
    )
    return page(rows, 100, user["id"], "work-tasks", time_key="updated_at")


@router.post("", status_code=201)
async def create(body: TaskInput, request: Request) -> dict:
    user = await identity(request)
    identifier = uuid4()
    await request.app.state.db.execute(
        "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES (%s,%s,%s,%s)",
        (identifier, user["id"], body.title, body.goal),
    )
    return {"id": identifier}


@router.get("/{identifier}")
async def detail(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT t.id,t.title,t.goal,t.status,t.revision,t.created_at,t.updated_at,"
        "EXISTS(SELECT 1 FROM work_task_workflows f WHERE f.task_id=t.id) AS workflow_task "
        "FROM work_tasks t WHERE t.id=%s AND t.owner_id=%s",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "任务不存在或无权访问")
    return rows[0]


@router.put("/{identifier}")
async def update(identifier: UUID, body: TaskInput, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "UPDATE work_tasks SET title=%s,goal=%s,revision=revision+1,updated_at=now() "
        "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id",
        (body.title, body.goal, identifier, user["id"], body.revision),
    )
    if not rows:
        fail(409, "TASK_CONFLICT", "任务不存在或已被修改，请刷新后重试")
    return {"id": identifier}


@router.delete("/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT id FROM work_tasks WHERE id=%s AND owner_id=%s FOR UPDATE",
            (identifier, user["id"]),
        )
        if not await cursor.fetchone():
            fail(404, "NOT_FOUND", "任务不存在或无权访问")
        cursor = await connection.execute("SELECT 1 FROM work_queue_runs WHERE task_id=%s LIMIT 1", (identifier,))
        if await cursor.fetchone():
            fail(409, "TASK_HAS_RUNS", "任务已有运行记录，请保留任务以便追溯和下载成果")
        files = await (
            await connection.execute(
                "SELECT id FROM work_input_files WHERE task_id=%s AND owner_id=%s", (identifier, user["id"])
            )
        ).fetchall()
        await connection.execute("DELETE FROM work_tasks WHERE id=%s AND owner_id=%s", (identifier, user["id"]))
    for file in files:
        storage(request, file["id"]).unlink(missing_ok=True)
    return {"ok": True}
