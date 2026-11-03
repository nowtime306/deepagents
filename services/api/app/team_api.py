"""Owner-scoped team configuration choices, progress and cancellation."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.errors import fail
from app.model_catalog import supports_chat
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["Agent 团队"])


@router.get("/agents/team-options")
async def options(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT v.id,v.agent_id,v.version,v.snapshot->>'name' AS name,v.snapshot->>'model_id' AS model_id,"
        "m.name AS model_name,m.kind,m.protocol FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
        "JOIN model_configs m ON m.id=v.snapshot->>'model_id' AND m.enabled "
        "AND m.revision=(v.snapshot->>'model_revision')::int WHERE v.owner_id=%s AND a.owner_id=%s "
        "AND v.active AND a.enabled AND coalesce(v.snapshot->'team','[]'::jsonb)='[]'::jsonb "
        "AND coalesce(v.snapshot->'workflow_version_ids','[]'::jsonb)='[]'::jsonb ORDER BY v.created_at DESC LIMIT 200",
        (user["id"], user["id"]),
    )
    return {"items": [row for row in rows if supports_chat(row["kind"], row["protocol"])]}


async def owned(request, run, mode):
    user = await identity(request)
    if mode == "ask":
        query = (
            "SELECT r.status FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE r.id=%s AND c.owner_id=%s"
        )
    else:
        query = "SELECT status FROM work_queue_runs WHERE id=%s AND owner_id=%s"
    rows = await request.app.state.db.fetch(query, (run, user["id"]))
    if not rows:
        fail(404, "RUN_NOT_FOUND", "运行不存在或无权访问")
    return user, rows[0]


async def listing(request, run, mode):
    user, parent = await owned(request, run, mode)
    column = "ask_run_id" if mode == "ask" else "work_run_id"
    rows = await request.app.state.db.fetch(
        "SELECT t.id,t.role_name,t.version_id,t.model_id,m.name AS model_name,t.status,t.cancel_requested,t.error_code,"
        "t.created_at,t.finished_at,"
        "count(u.call_id)::int AS calls,count(u.call_id) FILTER(WHERE u.status='KNOWN')::int AS reported_calls,"
        "sum(u.input_tokens) FILTER(WHERE u.status='KNOWN') AS input_tokens,"
        "sum(u.output_tokens) FILTER(WHERE u.status='KNOWN') AS output_tokens FROM team_calls t "
        "LEFT JOIN model_configs m ON m.id=t.model_id "
        f"LEFT JOIN model_usage u ON u.team_call_id=t.id WHERE t.{column}=%s AND t.owner_id=%s "
        "GROUP BY t.id,m.name ORDER BY t.created_at,t.id",
        (run, user["id"]),
    )
    for row in rows:
        if row["status"] == "RUNNING" and parent["status"] not in {"RUNNING", "WAITING_APPROVAL", "PAUSED"}:
            row["status"], row["error_code"] = "UNKNOWN", "TEAM_RESULT_UNKNOWN"
        for key in ("input_tokens", "output_tokens"):
            row[key] = int(row[key]) if row[key] is not None else None
    return {"items": rows}


async def cancel(request, run, identifier, mode):
    user, _ = await owned(request, run, mode)
    column = "ask_run_id" if mode == "ask" else "work_run_id"
    rows = await request.app.state.db.fetch(
        f"UPDATE team_calls SET cancel_requested=true WHERE id=%s AND {column}=%s AND owner_id=%s "
        "AND status='RUNNING' AND NOT cancel_requested RETURNING id",
        (identifier, run, user["id"]),
    )
    if not rows:
        fail(409, "TEAM_CALL_NOT_ACTIVE", "成员任务已结束、取消或无权访问")
    return {"ok": True, "message": "已请求取消，将在下一模型或工具边界停止"}


@router.get("/runs/{run}/team")
async def ask_list(run: UUID, request: Request):
    return await listing(request, run, "ask")


@router.get("/work-runs/{run}/team")
async def work_list(run: UUID, request: Request):
    return await listing(request, run, "work")


@router.post("/runs/{run}/team/{identifier}/cancel", status_code=202)
async def ask_cancel(run: UUID, identifier: UUID, request: Request):
    return await cancel(request, run, identifier, "ask")


@router.post("/work-runs/{run}/team/{identifier}/cancel", status_code=202)
async def work_cancel(run: UUID, identifier: UUID, request: Request):
    return await cancel(request, run, identifier, "work")
