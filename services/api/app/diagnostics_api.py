"""Numeric run diagnostics; no prompts, provider keys, tool arguments or reasoning text."""

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Request

from app.errors import fail
from app.harness import ASK_POLICY, WORK_POLICY
from app.security import identity
from app.usage_api import work_run_ids

router = APIRouter(prefix="/api/v1", tags=["运行诊断"])


@router.get("/harness/policies")
async def policies(request: Request) -> dict:
    await identity(request)
    return {
        "ask": {**asdict(ASK_POLICY), "permissions": "只读检索、技能与已授权工具；"
                "可委派已发布只读团队，无命令或写文件"},
        "work": {**asdict(WORK_POLICY), "permissions": "受控工作区；命令、删除和写工具需审批"},
        "note": "这里是新运行的默认策略；任务可有独立预算，历史运行以保存的快照为准。",
    }


async def calls_for(database, owner: UUID, identifiers: list[UUID]) -> list[dict]:
    rows = await database.fetch(
        "SELECT u.call_id,u.run_id,u.mode,u.model_id,u.model_revision,u.role_name,u.team_call_id,m.name AS model_name,"
        "u.status,u.input_tokens,u.output_tokens,u.cache_read_tokens,u.cache_creation_tokens,u.reasoning_tokens,"
        "u.estimated_input_tokens,u.output_token_reserve,u.estimated_cost,u.currency,u.input_rate,u.output_rate,"
        "u.failure_code,u.created_at,u.finished_at,"
        "CASE WHEN u.finished_at IS NOT NULL THEN greatest(0,extract(epoch FROM "
        "(u.finished_at-u.created_at))*1000)::bigint END AS duration_ms,"
        "CASE WHEN u.first_token_at IS NOT NULL THEN greatest(0,extract(epoch FROM "
        "(u.first_token_at-u.created_at))*1000)::bigint END AS first_token_ms,"
        "(EXISTS(SELECT 1 FROM task_runs r WHERE r.id=u.run_id AND r.status IN ('PENDING','RUNNING')) OR "
        "EXISTS(SELECT 1 FROM work_queue_runs r WHERE r.id=u.run_id AND r.status='RUNNING') OR "
        "EXISTS(SELECT 1 FROM workflow_runs r WHERE r.id=u.run_id AND r.status IN ('PENDING','RUNNING'))) AS active "
        "FROM model_usage u LEFT JOIN model_configs m ON m.id=u.model_id "
        "WHERE u.owner_id=%s AND u.run_id=ANY(%s::uuid[]) ORDER BY u.created_at,u.call_id",
        (owner, identifiers),
    )
    for row in rows:
        if row["status"] == "PENDING" and not row["active"]:
            row["status"], row["failure_code"] = "UNKNOWN", "USAGE_INTERRUPTED"
        row.pop("active")
    return rows


def stopping_reason(run: dict) -> str:
    reasons = {
        "ASK_BUDGET_EXCEEDED": "本轮模型调用次数或 Token 预算已达到上限。",
        "WORK_BUDGET_EXCEEDED": "任务模型预算或执行租约不可用，停止后续调用。",
        "WORK_USAGE_UNAVAILABLE": "服务商没有返回有效用量，任务停止以避免失控消耗。",
        "MODEL_VERSION_CHANGED": "模型已修改或停用，需要创建新运行。",
        "SERVER_RESTARTED": "API 重启中断了短对话，历史保留；不会自动重复模型请求。",
        "ASK_RESULT_UNKNOWN": "执行中断，模型或工具结果未知；不会自动重放，请核对后明确发起新一轮。",
        "TEAM_RESULT_UNKNOWN": "成员请求结果未知，团队已停止；不会自动重放，请核对后明确发起新运行。",
        "TEAM_DELEGATION_LIMIT": "本轮已达到八次委派上限。",
        "ASK_NO_PROGRESS": "相同工具调用连续得到相同结果，已停止无进展重复。",
        "REFERENCE_CHANGED": "引用已更新、删除或不可用，本轮停止，请明确发起新一轮。",
        "ACCOUNT_UNAVAILABLE": "账号未通过审核或已停用，停止后续调用。",
        "PERMISSION_REQUIRED": "当前角色的功能权限已撤销，停止后续调用。",
        "WORK_CAPACITY_WAIT": "等待可用任务环境，尚未开始新的执行。",
        "RESTORED_REVIEW": "这是恢复的数据副本，须人工核对后继续。",
    }
    if run.get("error_code") in reasons:
        return reasons[run["error_code"]]
    return {
        "SUCCEEDED": "本轮正常完成。", "COMPLETED": "任务正常完成。",
        "PENDING": "等待执行。", "RUNNING": "正在运行，统计会继续更新。",
        "CANCELLED": "运行已取消；已发生的模型消耗仍保留。",
        "WAITING_APPROVAL": "等待用户审批，暂不继续受控操作。",
        "PAUSED": "运行已暂停。", "NEEDS_REVIEW": "外部结果或恢复状态需要人工核查，不自动重放。",
        "FAILED": "运行失败，请结合调用状态、审批和恢复记录检查。",
    }.get(run["status"], "请结合运行记录检查当前状态。")


def budget_for(run: dict, mode: str, calls: list[dict]) -> dict:
    frozen = run.get("harness_policy") if mode == "ask" else {
        "model_calls": run["step_limit"], "token_limit": run["token_limit"],
    }
    known = [row for row in calls if row["status"] == "KNOWN"]
    return {
        "snapshot_available": frozen is not None,
        "model_call_limit": frozen.get("model_calls") if frozen else None,
        "token_limit": frozen.get("token_limit") if frozen else None,
        "calls_recorded": len(calls),
        "reported_tokens": sum(row["input_tokens"] + row["output_tokens"] for row in known) if known else None,
        "unknown_calls": sum(row["status"] == "UNKNOWN" for row in calls),
        "pending_calls": sum(row["status"] == "PENDING" for row in calls),
    }


async def diagnostic(request: Request, identifier: UUID, mode: str) -> dict:
    user = await identity(request)
    database = request.app.state.db
    if mode == "ask":
        rows = await database.fetch(
            "SELECT r.*,c.owner_id FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE r.id=%s AND c.owner_id=%s", (identifier, user["id"]),
        )
    else:
        rows = await database.fetch(
            "SELECT * FROM work_queue_runs WHERE id=%s AND owner_id=%s", (identifier, user["id"]),
        )
    if not rows:
        fail(404, "NOT_FOUND", "运行不存在或无权访问")
    run = rows[0]
    identifiers = [identifier] if mode == "ask" else await work_run_ids(database, user["id"], identifier)
    calls = await calls_for(database, user["id"], identifiers)
    snapshot = run.get("agent_snapshot") or {}
    native = snapshot.get("ask_native_mode")
    search_mode = native or (snapshot.get("web_mode", "tavily") if snapshot.get("web_enabled") else None)
    known = "ask_native_mode" in snapshot or "web_enabled" in snapshot
    event_table = "run_events" if mode == "ask" else "work_run_events"
    observed = await database.fetch(
        f"SELECT count(*) FILTER(WHERE event='tool.started') AS started,"
        f"count(*) FILTER(WHERE event='tool.completed') AS returned FROM {event_table} "
        "WHERE run_id=%s AND data->>'name'='search_web'", (identifier,),
    )
    files = run.get("attachment_snapshot") or run.get("input_files") or []
    context_events = []
    if mode == "ask":
        context_events = await database.fetch(
            "SELECT event,data,timestamp FROM run_events WHERE run_id=%s "
            "AND event IN ('context.observed','context.compacted','context.references',"
            "'checkpoint.repaired','tool.repetition_stopped') "
            "ORDER BY event_id LIMIT 100", (identifier,),
        )
    return {
        "run_id": identifier, "mode": mode, "status": run["status"], "error_code": run.get("error_code"),
        "team_enabled": bool(snapshot.get("team")),
        "reason": stopping_reason(run), "budget": budget_for(run, mode, calls), "calls": calls,
        "context": {
            "events": context_events,
            "attachment_count": len(files),
            "image_count": sum(file.get("media_type", "").startswith("image/") for file in files),
            "attachment_text_characters": sum(len(file.get("extracted", "")) for file in files),
            "max_input_estimate": max((row["estimated_input_tokens"] or 0 for row in calls), default=0) or None,
        },
        "execution": {"durable": bool(run.get("worker_id")), "claimed_at": run.get("claimed_at"),
                      "lease_until": run.get("lease_until") if mode == "ask" else None},
        "search": {
            "configuration_known": known, "enabled": bool(search_mode), "mode": search_mode,
            "server_managed": search_mode in {"responses", "openai_native"},
            "tool_started": observed[0]["started"], "tool_returned": observed[0]["returned"],
            "note": "授权不等于已搜索。应用事件仅覆盖主图工具返回；服务端与子智能体搜索没有独立采集。",
        },
        "note": "缓存/推理属于输入或输出的细分，不再次相加；未报告显示未知。"
        "耗时为平台观察值。费用按历史输入/输出单价估算，未单独计算缓存折扣或工具费用。",
    }


@router.get("/runs/{identifier}/diagnostics")
async def ask_diagnostic(identifier: UUID, request: Request) -> dict:
    return await diagnostic(request, identifier, "ask")


@router.get("/work-runs/{identifier}/diagnostics")
async def work_diagnostic(identifier: UUID, request: Request) -> dict:
    return await diagnostic(request, identifier, "work")
