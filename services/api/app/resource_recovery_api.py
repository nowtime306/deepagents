"""Explicit configuration recovery never grants permission to replay previous effects."""

import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.configuration_history import safe_origin
from app.errors import fail
from app.model_catalog import supports_chat
from app.permissions import require_account
from app.resource_runtime import freeze_model, resource_run
from app.security import identity

router = APIRouter(prefix="/api/v1/resource-runs/work", tags=["运行配置恢复"])
RECOVERY = "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED"


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_phase: int = Field(ge=1)
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConnectionRecords:
    """Use the confirmation transaction for stage and snapshot insertion."""

    def __init__(self, connection):
        self.connection = connection

    async def fetch(self, query, params):
        return await (await self.connection.execute(query, params)).fetchall()

    async def execute(self, query, params):
        return await self.connection.execute(query, params)


async def owned(connection, identifier, owner):
    run = await (await connection.execute(
        "SELECT * FROM work_queue_runs WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, owner),
    )).fetchone()
    if not run:
        fail(404, "NOT_FOUND", "运行不存在或无权访问")
    if run["status"] != "PAUSED" or run["error_code"] != RECOVERY:
        fail(409, "RESOURCE_RECOVERY_NOT_WAITING", "此运行没有等待配置恢复确认")
    if run["cancel_requested"] or run["lease_token"] is not None or run["lease_until"] is not None:
        fail(409, "RESOURCE_RECOVERY_NOT_SAFE", "运行已取消或执行租约尚未释放，请先停止并核对")
    return run


async def preview(connection, run, models):
    phase = await (await connection.execute(
        "SELECT coalesce(max(phase),1) AS phase FROM run_resource_stages WHERE owner_id=%s AND mode='work' "
        "AND run_id=%s AND (phase=1 OR confirmed_by IS NOT NULL)", (run["owner_id"], run["id"]),
    )).fetchone()
    phase = phase["phase"]
    snapshots = await (await connection.execute(
        "SELECT * FROM run_resource_snapshots WHERE owner_id=%s AND mode='work' AND run_id=%s "
        "AND phase=%s ORDER BY resource_kind,resource_id", (run["owner_id"], run["id"], phase),
    )).fetchall()
    checkpoint = await (await connection.execute(
        "SELECT checkpoint_id,checkpoint FROM checkpoints WHERE thread_id=%s AND checkpoint_ns='' "
        "ORDER BY checkpoint_id DESC LIMIT 1", ("work:" + str(run["id"]),),
    )).fetchone()
    blockers = []
    if not checkpoint or not checkpoint["checkpoint"].get("channel_versions"):
        blockers.append("未找到完整的原运行检查点，不能安全继续，请人工核对")
    evidence = await (await connection.execute(
        "SELECT EXISTS(SELECT 1 FROM work_approvals WHERE run_id=%s) AS approvals,"
        "EXISTS(SELECT 1 FROM operation_executions WHERE run_id=%s) AS operations,"
        "EXISTS(SELECT 1 FROM model_usage WHERE run_id=%s AND status IN ('PENDING','UNKNOWN')) AS unknown,"
        "EXISTS(SELECT 1 FROM work_browser_controls WHERE run_id=%s AND status<>'CLOSED') AS browser",
        (run["id"],) * 4,
    )).fetchone()
    for key, reason in {
        "approvals": "存在旧审批，不能将旧批准复用于新配置，请人工核对",
        "operations": "存在既有操作执行凭据，请核对成果与执行记录后人工恢复",
        "unknown": "存在结果未知的模型调用，不自动重复发送",
        "browser": "浏览器仍由人工控制，请先结束控制并核对状态",
    }.items():
        if evidence[key]:
            blockers.append(reason)
    decisions = run.get("resume_decision") or {}
    if any(value is not True for value in decisions.values()):
        blockers.append("存在操作审批恢复参数，不能跨配置阶段复用")
    if (run.get("agent_snapshot") or {}).get("route_selection_id"):
        blockers.append("本次使用模型路由，需核对接收方授权后人工恢复")
    current, changes = [], []
    workflow = await (await connection.execute(
        "SELECT 1 FROM work_run_workflows WHERE run_id=%s", (run["id"],),
    )).fetchone()
    for snapshot in snapshots:
        kind, identifier = snapshot["resource_kind"], snapshot["resource_id"]
        if kind in {"agent", "agent_version"}:
            continue
        if kind != "model":
            blockers.append("已解析模型以外的资源，需核对工具或脚本内容后人工恢复")
            continue
        row = await (await connection.execute(
            "SELECT * FROM model_configs WHERE id=%s FOR SHARE", (identifier,),
        )).fetchone()
        if not row:
            blockers.append("模型资源已删除，请先恢复资源")
            continue
        declaration = await (await connection.execute(
            "SELECT supports_tools FROM model_capability_declarations WHERE model_id=%s FOR SHARE", (identifier,),
        )).fetchone()
        row = {**row, "supports_tools": declaration["supports_tools"] if declaration else None}
        compatible = row["enabled"] and supports_chat(row["kind"], row["protocol"])
        if not compatible:
            blockers.append("模型已停用或用途、协议不支持聊天，请先修复配置")
        if identifier == run["model_id"] and not workflow and row["supports_tools"] is not True:
            compatible = False
            blockers.append("任务模型工具能力尚未验证或不支持，请先验证并声明工具能力")
        current.append(row)
        safe = {key: row[key] for key in ("id", "revision", "kind", "protocol", "provider",
                                         "temperature", "max_output_tokens", "supports_images", "supports_tools")}
        safe["service_target"] = safe_origin(row["base_url"])
        safe["service_target_digest"] = hashlib.sha256(row["base_url"].encode()).hexdigest()
        changes.append({"resource_kind": kind, "resource_id": identifier, "before": snapshot["snapshot"],
                        "after": safe, "compatible": bool(compatible)})
    if not current:
        blockers.append("历史模型配置未记录，不能将当前配置冒充原配置")
    digest = hashlib.sha256(json.dumps(
        {"run": str(run["id"]), "phase": phase, "checkpoint": checkpoint,
         "business": run.get("agent_snapshot"), "current": current, "blockers": blockers,
         "snapshots": [(str(row["snapshot_id"]), row["revision"]) for row in snapshots]},
        sort_keys=True, default=str,
    ).encode()).hexdigest()
    return {"run_id": str(run["id"]), "mode": "work", "phase": phase, "confirmation_digest": digest,
            "can_confirm": not blockers, "manual_review": blockers, "changes": changes,
            "checkpoint_recorded": bool(checkpoint)}, current


@router.get("/{identifier}/configuration-recovery")
async def details(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    await require_account(request.app.state.db, owner, "work.execute")
    async with request.app.state.db.pool.connection() as connection:
        run = await owned(connection, identifier, owner)
        result, _ = await preview(connection, run, request.app.state.models)
    return result


@router.post("/{identifier}/configuration-recovery", status_code=202)
async def confirm(identifier: UUID, body: Confirmation, request: Request):
    owner = (await identity(request))["id"]
    await require_account(request.app.state.db, owner, "work.execute")
    if not request.app.state.settings.work_enabled:
        fail(503, "WORK_DISABLED", "任务执行尚未启用")
    async with request.app.state.db.pool.connection() as connection:
        run = await owned(connection, identifier, owner)
        result, current = await preview(connection, run, request.app.state.models)
        if body.expected_phase != result["phase"] or body.confirmation_digest != result["confirmation_digest"]:
            fail(409, "RESOURCE_RECOVERY_CONFLICT", "配置或检查点已改变，请重新查看差异再确认")
        if not result["can_confirm"]:
            fail(409, "RESOURCE_RECOVERY_MANUAL_REVIEW", "；".join(result["manual_review"]))
        await request.app.state.capabilities.validate(owner, run["agent_snapshot"])
        phase = result["phase"] + 1
        await connection.execute(
            "INSERT INTO run_resource_stages(owner_id,mode,run_id,phase,confirmed_by,reason,safe_changes) "
            "VALUES(%s,'work',%s,%s,%s,'confirmed-configuration-recovery',%s)",
            (owner, identifier, phase, owner, Jsonb(result["changes"])),
        )
        async with resource_run(ConnectionRecords(connection), owner, "work", identifier, phase=phase) as scope:
            try:
                for row in current:
                    await freeze_model(
                        request.app.state.models.from_row(row), request.app.state.models.vault,
                        credential_marker=hashlib.sha256(row["api_key_ciphertext"].encode()).hexdigest(),
                    )
            finally:
                # A rolled-back confirmation must not leave usable credentials for an uncommitted stage.
                scope.cached_credentials.clear()
        await connection.execute(
            "UPDATE work_queue_runs SET status='PENDING',pause_requested=false,error_code=NULL,"
            "resource_resume_pending=true,available_after=now(),updated_at=now() WHERE id=%s", (identifier,),
        )
    return {"accepted": True, "status": "PENDING", "phase": phase}
