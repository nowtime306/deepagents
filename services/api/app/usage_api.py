"""Owner-scoped per-turn totals and network-free draft estimates."""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.agent_versions import selected as selected_agent_version
from app.errors import fail
from app.harness import ASK_POLICY
from app.security import identity
from app.token_estimates import IMAGE_RESERVE, content_tokens, output_reserve, text_tokens

router = APIRouter(prefix="/api/v1", tags=["Token 用量"])


def totals(rows: list[dict]) -> dict:
    known = sum(row["reported_calls"] for row in rows)
    return {
        "calls": sum(row["calls"] for row in rows),
        "reported_calls": known,
        "unknown_calls": sum(row["unknown_calls"] for row in rows),
        "pending_calls": sum(row["pending_calls"] for row in rows),
        "input_tokens": sum(row["input_tokens"] or 0 for row in rows) if known or not rows else None,
        "output_tokens": sum(row["output_tokens"] or 0 for row in rows) if known or not rows else None,
    }


async def summaries(database, owner: UUID, run_ids: list[UUID]) -> list[dict]:
    if not run_ids:
        return []
    rows = await database.fetch(
        "WITH scoped AS (SELECT u.*,CASE WHEN u.status='PENDING' AND NOT ("
        "EXISTS(SELECT 1 FROM task_runs r WHERE r.id=u.run_id AND r.status IN ('PENDING','RUNNING')) OR "
        "EXISTS(SELECT 1 FROM work_queue_runs r WHERE r.id=u.run_id AND r.status='RUNNING') OR "
        "EXISTS(SELECT 1 FROM workflow_runs r WHERE r.id=u.run_id AND r.status IN ('PENDING','RUNNING')) OR "
        "EXISTS(SELECT 1 FROM resource_operations r WHERE r.id=u.run_id AND r.status='RUNNING' AND r.lease_until>now())"
        ") THEN 'UNKNOWN' ELSE u.status END AS effective_status FROM model_usage u "
        "WHERE u.owner_id=%s AND u.run_id=ANY(%s::uuid[])) "
        "SELECT run_id,count(*) AS calls,count(*) FILTER(WHERE effective_status='KNOWN') AS reported_calls,"
        "count(*) FILTER(WHERE effective_status='UNKNOWN') AS unknown_calls,"
        "count(*) FILTER(WHERE effective_status='PENDING') AS pending_calls,"
        "sum(input_tokens) FILTER(WHERE effective_status='KNOWN') AS input_tokens,"
        "sum(output_tokens) FILTER(WHERE effective_status='KNOWN') AS output_tokens,"
        "bool_or(route_selection_id IS NOT NULL) AS routed "
        "FROM scoped GROUP BY run_id",
        (owner, run_ids),
    )
    # PostgreSQL SUM(bigint) is numeric; expose token counts as JSON integers.
    for row in rows:
        for key in ("input_tokens", "output_tokens"):
            row[key] = int(row[key]) if row[key] is not None else None
    return rows


@router.get("/conversations/{identifier}/usage")
async def conversation_usage(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    exists = await request.app.state.db.fetch(
        "SELECT id FROM conversations WHERE id=%s AND owner_id=%s",
        (identifier, user["id"]),
    )
    if not exists:
        fail(404, "NOT_FOUND", "对话不存在或无权访问")
    runs = await request.app.state.db.fetch("SELECT id FROM task_runs WHERE conversation_id=%s", (identifier,))
    rows = await summaries(request.app.state.db, user["id"], [row["id"] for row in runs])
    return {"runs": rows, "total": totals(rows)}


@router.get("/work-runs/{identifier}/usage")
async def work_usage(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    exists = await request.app.state.db.fetch(
        "SELECT id FROM work_queue_runs WHERE id=%s AND owner_id=%s",
        (identifier, user["id"]),
    )
    if not exists:
        fail(404, "NOT_FOUND", "任务运行不存在或无权访问")
    rows = await summaries(
        request.app.state.db,
        user["id"],
        await work_run_ids(
            request.app.state.db,
            user["id"],
            identifier,
        ),
    )
    return {"total": totals(rows), "routed": any(row["routed"] for row in rows)}


async def work_run_ids(database, owner: UUID, identifier: UUID) -> list[UUID]:
    children = await database.fetch(
        "SELECT j.run_id FROM external_workflow_jobs j JOIN api_credentials k ON k.id=j.credential_id "
        "WHERE k.parent_work_run=%s AND k.owner_id=%s",
        (identifier, owner),
    )
    return [identifier, *[row["run_id"] for row in children]]


class EstimateInput(BaseModel):
    mode: Literal["ask", "work"] = "ask"
    model_id: str = Field(min_length=1, max_length=120)
    message: str = Field(default="", max_length=32000)
    conversation_id: UUID | None = None
    task_id: UUID | None = None
    agent_id: UUID | None = None
    agent_version_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)


async def selected_context(body: EstimateInput, request: Request, owner: UUID) -> tuple[dict, list[dict], bool]:
    database = request.app.state.db
    history, limited = [], False
    agent, version = body.agent_id, body.agent_version_id
    if body.conversation_id:
        rows = await database.fetch(
            "SELECT agent_id,agent_version_id FROM conversations WHERE id=%s AND owner_id=%s",
            (body.conversation_id, owner),
        )
        if not rows:
            fail(404, "NOT_FOUND", "对话不存在或无权访问")
        agent, version = rows[0]["agent_id"], rows[0]["agent_version_id"]
        history = await database.fetch(
            "SELECT content FROM conversation_messages WHERE conversation_id=%s "
            "ORDER BY created_at DESC,id DESC LIMIT 101",
            (body.conversation_id,),
        )
        limited = len(history) > 100
        history = history[:100]
    selection = await request.app.state.capabilities.bindings(owner)
    if agent:
        rows = await database.fetch("SELECT * FROM agents WHERE id=%s AND owner_id=%s AND enabled", (agent, owner))
        if not rows:
            fail(404, "NOT_FOUND", "智能体不存在或已停用")
        selection = rows[0]
    if version:
        selection = await selected_agent_version(request, owner, agent, version)
    return selection, history, limited


@router.post("/usage/estimate")
async def estimate(body: EstimateInput, request: Request) -> dict:
    user = await identity(request)
    selection, history, limited = await selected_context(body, request, user["id"])
    model = await request.app.state.models.get(selection.get("model_id") or body.model_id)
    from functools import partial

    from app.local_tokenizer import encoder

    estimate_text = partial(text_tokens, config=model)
    incoming = estimate_text(body.message) + estimate_text(selection.get("instructions", "")) + 800
    for item in history:
        content = item["content"]
        # Persisted user attachments contain IDs, never inline image Base64.
        incoming += estimate_text(json.dumps(content, ensure_ascii=False)) + 8
        incoming += IMAGE_RESERVE * sum(
            file.get("media_type", "").startswith("image/") for file in content.get("attachments", [])
        )
    if body.attachment_ids:
        files = await request.app.state.db.fetch(
            "SELECT extracted,media_type FROM conversation_attachments WHERE id=ANY(%s::uuid[]) "
            "AND conversation_id=%s AND owner_id=%s",
            (body.attachment_ids, body.conversation_id, user["id"]),
        )
        if len(files) != len(set(body.attachment_ids)) or len(set(body.attachment_ids)) != len(body.attachment_ids):
            fail(404, "NOT_FOUND", "附件不存在或无权访问")
        incoming += sum(
            IMAGE_RESERVE if file["media_type"].startswith("image/") else content_tokens(file["extracted"], model)
            for file in files
        )
    if body.mode == "work":
        rows = await request.app.state.db.fetch(
            "SELECT goal FROM work_tasks WHERE id=%s AND owner_id=%s",
            (body.task_id, user["id"]),
        )
        if not rows:
            fail(404, "NOT_FOUND", "任务不存在或无权访问")
        incoming += estimate_text(rows[0]["goal"]) + 1200
    incoming += sum(len(selection.get(key, [])) for key in ("skill_ids", "mcp_ids", "knowledge_ids")) * 300
    incoming += (
        estimate_text(json.dumps(selection.get("team") or [], ensure_ascii=False)) if selection.get("team") else 0
    )
    reserve = output_reserve(model, body.mode)
    reference_tokens = 0
    if body.mode == "ask" and body.conversation_id:
        from app.conversation_references import context

        async with request.app.state.db.pool.connection() as connection:
            reference_text, _ = await context(connection, body.conversation_id, user["id"])
        reference_tokens = estimate_text(reference_text)
        incoming += reference_tokens
    return {
        "reference_input_estimate": reference_tokens,
        "input_estimate": incoming,
        "output_reserve": reserve,
        "first_call_reserve": incoming + reserve,
        "method": encoder(model).name if encoder(model) else "local-character-heuristic-v1",
        "history_limited": limited,
        "scope": "first_call",
        "mode": body.mode,
        "turn_token_limit": ASK_POLICY.token_limit if body.mode == "ask" else None,
        "note": "本地粗估首轮输入；图片与工具按预留计算。输出为单次上限，"
        "多轮工具、摘要和检索会额外消耗，实际以服务商返回为准。"
        + ("团队成员消耗共用本轮预算；此首轮估算不包含后续委派。" if selection.get("team") else ""),
    }
