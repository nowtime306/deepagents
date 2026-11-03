"""Authenticated Work run submission, progress, approvals and artifact downloads."""

import difflib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.agent_versions import selected as selected_agent_version
from app.errors import fail
from app.harness import WORK_POLICY
from app.list_pagination import decode_cursor, page
from app.model_catalog import supports_chat
from app.operation_journal import receipts
from app.security import identity
from app.web_search import ensure_available
from app.work_artifacts import WorkArtifacts
from app.work_network import domain, policy
from app.workspace_rpc import parts

router = APIRouter(prefix="/api/v1", tags=["任务执行"])


class StartWork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    environment: Literal["coding", "browser"] = "coding"
    step_limit: int = Field(default=WORK_POLICY.model_calls, ge=5, le=100)
    token_limit: int = Field(default=WORK_POLICY.token_limit, ge=1000, le=1000000)
    agent_id: UUID | None = None
    agent_version_id: UUID | None = None
    restore_from_run: UUID | None = None
    network_domains: list[str] = Field(default_factory=list, max_length=10)
    repository_id: UUID | None = None
    model_routing: bool = False


class EditedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["execute", "delete"]
    args: dict


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest: str = Field(min_length=64, max_length=64)
    decision: Literal["approve", "reject", "edit"]
    edited_actions: list[EditedAction] | None = Field(default=None, max_length=20)


async def owned(request: Request, identifier: UUID) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT id,task_id,owner_id,title,goal,status,result,error_code,environment,"
        "used_tokens,model_calls,usage_unknown,input_files,pause_requested,created_at,updated_at,"
        "step_limit,token_limit,network_domains "
        "FROM work_queue_runs WHERE id=%s AND owner_id=%s",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "任务运行不存在或无权访问")
    return rows[0]


@router.post("/work-tasks/{identifier}/runs", status_code=202)
async def start(identifier: UUID, body: StartWork, request: Request) -> dict:
    user = await identity(request)
    if not request.app.state.settings.work_enabled:
        fail(503, "WORK_DISABLED", "任务执行尚未通过本机验收，当前仅开放草稿")
    online = await request.app.state.db.fetch(
        "SELECT 1 FROM work_worker_instances WHERE heartbeat_at>now()-interval '45 seconds' LIMIT 1"
    )
    if not online:
        fail(503, "WORKER_OFFLINE", "任务后台尚未运行，请先启动任务服务")
    try:
        normalized = list(dict.fromkeys(domain(value) for value in body.network_domains))
        await policy(normalized)
    except (ValueError, OSError):
        fail(422, "NETWORK_DOMAIN_INVALID", "授权域名无效、无法解析或指向非公网地址")
    from app.repository_runs import attach, freeze

    if body.repository_id and body.environment != "coding":
        fail(422, "REPOSITORY_ENVIRONMENT", "仓库开发任务请选择编程环境")
    frozen_repository = await freeze(request, user["id"], body.repository_id, body.restore_from_run)
    run_id = uuid4()
    async with request.app.state.db.pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT * FROM work_tasks WHERE id=%s AND owner_id=%s AND revision=%s FOR UPDATE",
            (identifier, user["id"], body.revision),
        )
        task = await cursor.fetchone()
        if not task:
            fail(409, "TASK_CONFLICT", "任务不存在或已修改，请刷新后重试")
        if await (
            await connection.execute("SELECT 1 FROM work_task_workflows WHERE task_id=%s", (identifier,))
        ).fetchone():
            fail(422, "WORKFLOW_WORK_REQUIRED", "这是固定流程任务，请从工作流页面明确创建新运行")
        restore_snapshot = None
        if body.restore_from_run:
            source = await (
                await connection.execute(
                    "SELECT r.id,s.id AS snapshot_id FROM work_queue_runs r JOIN work_snapshots s ON s.run_id=r.id "
                    "WHERE r.id=%s AND r.task_id=%s AND r.owner_id=%s AND r.status IN "
                    "('COMPLETED','CANCELLED','NEEDS_REVIEW','FAILED') "
                    "AND NOT EXISTS(SELECT 1 FROM work_sandbox_sessions w WHERE w.run_id=r.id AND w.state<>'RELEASED') "
                    "ORDER BY s.created_at DESC,s.id DESC LIMIT 1",
                    (body.restore_from_run, identifier, user["id"]),
                )
            ).fetchone()
            if not source:
                fail(409, "RECOVERY_UNAVAILABLE", "只能使用本任务已保存且沙箱已安全回收的历史成果创建副本")
            restore_snapshot = source["snapshot_id"]
        inputs = await (
            await connection.execute(
                "SELECT id,path,size,digest FROM work_input_files WHERE task_id=%s AND owner_id=%s AND "
                "active ORDER BY path",
                (identifier, user["id"]),
            )
        ).fetchall()
        snapshot = await request.app.state.capabilities.bindings(user["id"])
        if body.agent_id:
            cursor = await connection.execute(
                "SELECT id,name,instructions,model_id,revision,knowledge_ids,skill_ids,mcp_ids,"
                "web_enabled,web_mode,memory_enabled,workflow_version_ids,team,model_routing "
                "FROM agents WHERE id=%s AND owner_id=%s AND enabled FOR SHARE",
                (body.agent_id, user["id"]),
            )
            agent = await cursor.fetchone()
            if not agent:
                fail(422, "AGENT_UNAVAILABLE", "智能体不存在、已停用或无权访问")
            snapshot = jsonable_encoder(agent)
        if body.agent_version_id:
            snapshot = await selected_agent_version(request, user["id"], body.agent_id, body.agent_version_id)
        await request.app.state.capabilities.validate(user["id"], snapshot)
        snapshot = await request.app.state.capabilities.freeze(user["id"], snapshot)
        effective_model = snapshot.get("model_id", body.model_id)
        routing = snapshot.get("model_routing", False) if body.agent_id else body.model_routing
        if routing:
            from app.model_routing import resolve

            selected = await resolve(
                request.app.state.models,
                user["id"],
                run_id,
                effective_model,
                enabled=True,
                expected=snapshot.get("route_pin"),
                tools=True,
                connection=connection,
                openai_native=bool(snapshot.get("web_enabled") and snapshot.get("web_mode") == "openai_native"),
            )
            effective_model = selected.id
            snapshot["route_selection_id"] = str(selected.route_selection_id)
        cursor = await connection.execute(
            "SELECT * FROM model_configs WHERE id=%s AND enabled FOR SHARE", (effective_model,)
        )
        model = await cursor.fetchone()
        if not model or not supports_chat(model["kind"], model["protocol"]):
            fail(422, "MODEL_UNAVAILABLE", "请选择启用的聊天模型")
        if snapshot.get("web_enabled"):
            if snapshot.get("web_mode") == "openai_native":
                if model["provider"] != "openai" or model["protocol"] != "openai":
                    fail(422, "WEB_MODE_UNSUPPORTED", "OpenAI 原生联网需要 OpenAI 服务商的聊天模型")
            else:
                await ensure_available(request.app.state.db)
        cursor = await connection.execute(
            "SELECT 1 FROM work_queue_runs WHERE task_id=%s "
            "AND status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')",
            (identifier,),
        )
        if await cursor.fetchone():
            fail(409, "TASK_BUSY", "此任务已有待执行、运行中或等待审批的运行")
        await connection.execute(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,model_revision,"
            "environment,step_limit,token_limit,agent_snapshot,instructions,network_domains,"
            "input_files,restore_snapshot_id) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id,
                identifier,
                user["id"],
                task["title"],
                task["goal"],
                task["revision"],
                effective_model,
                model["revision"],
                body.environment,
                body.step_limit,
                body.token_limit,
                Jsonb(snapshot),
                snapshot.get("instructions", ""),
                Jsonb(normalized),
                Jsonb(jsonable_encoder(inputs)),
                restore_snapshot,
            ),
        )
        await attach(connection, user["id"], run_id, frozen_repository)
    return {"run_id": run_id, "status": "PENDING"}


@router.get("/work-tasks/{identifier}/runs")
async def listing(identifier: UUID, request: Request, before: str | None = None) -> dict:
    user = await identity(request)
    tasks = await request.app.state.db.fetch(
        "SELECT 1 FROM work_tasks WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not tasks:
        fail(404, "NOT_FOUND", "任务不存在或无权访问")
    scope = "work-runs:" + str(identifier)
    timestamp, cursor_id = decode_cursor(before, user["id"], scope)
    rows = await request.app.state.db.fetch(
        "SELECT id,status,result,error_code,created_at,updated_at FROM work_queue_runs "
        "WHERE task_id=%s AND owner_id=%s "
        "AND (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s::uuid)) "
        "ORDER BY created_at DESC,id DESC LIMIT 51",
        (identifier, user["id"], timestamp, timestamp, cursor_id),
    )
    return page(rows, 50, user["id"], scope)


@router.get("/work-runs/{identifier}")
async def detail(identifier: UUID, request: Request) -> dict:
    return await owned(request, identifier)


@router.get("/work-runs/{identifier}/events")
async def events(identifier: UUID, request: Request, after: int = 0) -> dict:
    await owned(request, identifier)
    if after < 0:
        fail(422, "INVALID_CURSOR", "事件位置无效")
    rows = await request.app.state.db.fetch(
        "SELECT event_id,event,data,created_at FROM work_run_events WHERE run_id=%s AND event_id>%s "
        "ORDER BY event_id LIMIT 100",
        (identifier, after),
    )
    return {"items": rows, "next_after": rows[-1]["event_id"] if rows else after}


@router.get("/work-approvals/{identifier}/input")
async def approval_input(identifier: UUID, request: Request) -> Response:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT a.payload,w.workflow_run_id FROM work_approvals a JOIN work_queue_runs r ON r.id=a.run_id "
        "JOIN work_run_workflows w ON w.run_id=r.id WHERE a.id=%s AND r.owner_id=%s",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "流程审批不存在或无权访问")
    actions = rows[0]["payload"].get("action_requests", [])
    if len(actions) != 1 or actions[0]["name"] not in {"workflow_approval", "workflow_code"}:
        fail(422, "WORKFLOW_APPROVAL_INVALID", "此审批没有独立流程输入")
    args = actions[0]["args"]
    events = await request.app.state.db.fetch(
        "SELECT data->>'input' AS text FROM workflow_node_events WHERE run_id=%s AND node_id=%s "
        "AND event='node.started' ORDER BY event_id DESC LIMIT 1",
        (rows[0]["workflow_run_id"], args["node_id"]),
    )
    text = events[0]["text"] if events else None
    if text is None or len(text) > 40000 or hashlib.sha256(text.encode()).hexdigest() != args["input_digest"]:
        fail(503, "WORKFLOW_INPUT_CHANGED", "完整输入校验失败，请停止并核对此任务")
    return Response(
        json.dumps({"text": text}, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/work-runs/{identifier}/cancel", status_code=202)
async def cancel(identifier: UUID, request: Request) -> dict:
    run = await owned(request, identifier)
    await request.app.state.db.execute(
        "UPDATE work_queue_runs SET cancel_requested=true,"
        "status=CASE WHEN status IN ('PENDING','WAITING_APPROVAL','PAUSED') "
        "THEN 'CANCELLED' ELSE status END,updated_at=now() WHERE id=%s AND owner_id=%s",
        (identifier, run["owner_id"]),
    )
    return {"accepted": True}


@router.get("/work-runs/{identifier}/approvals")
async def approvals(identifier: UUID, request: Request) -> dict:
    run = await owned(request, identifier)
    rows = await request.app.state.db.fetch(
        "SELECT id,payload,digest,expires_at,decision,edited_actions,created_at FROM work_approvals "
        "WHERE run_id=%s AND owner_id=%s "
        "ORDER BY created_at,id",
        (identifier, run["owner_id"]),
    )
    return {"items": rows}


@router.post("/work-approvals/{identifier}/decisions")
async def decide(identifier: UUID, body: Decision, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT a.*,r.status,r.resume_decision FROM work_approvals a JOIN work_queue_runs r ON r.id=a.run_id "
            "WHERE a.id=%s AND a.owner_id=%s AND r.owner_id=%s FOR UPDATE OF a,r",
            (identifier, user["id"], user["id"]),
        )
        approval = await cursor.fetchone()
        if not approval:
            fail(404, "NOT_FOUND", "审批不存在或无权访问")
        digest = hashlib.sha256(
            json.dumps(approval["payload"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if body.digest != digest or approval["digest"] != digest:
            fail(409, "APPROVAL_CHANGED", "审批参数已变化，请重新查看")
        edited = [action.model_dump() for action in body.edited_actions] if body.edited_actions else None
        if body.decision == "edit":
            original = approval["payload"]["action_requests"]
            if not edited or len(edited) != len(original):
                fail(422, "APPROVAL_EDIT_INVALID", "修改后的操作数量必须与待审批操作一致")
            for old, new in zip(original, edited, strict=True):
                if old["name"] != new["name"]:
                    fail(422, "APPROVAL_EDIT_INVALID", "不能更换操作类型")
                arguments = new["args"]
                if new["name"] == "execute":
                    command = arguments.get("command")
                    if (
                        not {"command"} <= set(arguments) <= {"command", "timeout"}
                        or not isinstance(command, str)
                        or not 1 <= len(command) <= 16000
                    ):
                        fail(422, "APPROVAL_EDIT_INVALID", "命令参数应为 1–16000 字符的 command")
                    seconds = arguments.get("timeout")
                    if seconds is not None and (type(seconds) is not int or not 1 <= seconds <= 60):
                        fail(422, "APPROVAL_EDIT_INVALID", "命令 timeout 应为 1–60 秒的整数")
                else:
                    if set(arguments) != {"file_path"} or not isinstance(arguments["file_path"], str):
                        fail(422, "APPROVAL_EDIT_INVALID", "删除操作必须提供 file_path")
                    try:
                        parts(arguments["file_path"])
                    except ValueError:
                        fail(422, "APPROVAL_EDIT_INVALID", "删除路径超出工作区")
        elif edited is not None:
            fail(422, "APPROVAL_EDIT_INVALID", "只有修改后批准才接受替换参数")
        if approval["decision"]:
            if approval["decision"] != body.decision or approval["edited_actions"] != edited:
                fail(409, "APPROVAL_DECIDED", "审批已处理，不能更改决定")
            return {"decision": approval["decision"]}
        if approval["status"] != "WAITING_APPROVAL" or approval["expires_at"] <= datetime.now(UTC):
            fail(409, "APPROVAL_EXPIRED", "审批已过期或运行状态已改变")
        await connection.execute(
            "UPDATE work_approvals SET decision=%s,edited_actions=%s,decided_at=now() WHERE id=%s",
            (body.decision, Jsonb(edited) if edited else None, identifier),
        )
        if body.decision in {"approve", "edit"}:
            try:
                await receipts(connection, approval, edited if edited else approval["payload"]["action_requests"])
            except (KeyError, ValueError, TypeError):
                fail(422, "APPROVAL_ARGUMENTS_INVALID", "操作参数无效，请修改参数或拒绝此操作")
        cursor = await connection.execute(
            "SELECT * FROM work_approvals WHERE run_id=%s AND consumed_at IS NULL ORDER BY created_at,id",
            (approval["run_id"],),
        )
        pending = await cursor.fetchall()
        if all(item["decision"] for item in pending):
            resume = {
                item["interrupt_id"]: {
                    "decisions": [{"type": "edit", "edited_action": action} for action in item["edited_actions"]]
                    if item["decision"] == "edit"
                    else [{"type": item["decision"]} for _ in item["payload"]["action_requests"]]
                }
                for item in pending
            }
            await connection.execute(
                "UPDATE work_queue_runs SET status='PENDING',resume_decision=%s,updated_at=now() WHERE id=%s",
                (Jsonb({**(approval["resume_decision"] or {}), **resume}), approval["run_id"]),
            )
    return {"decision": body.decision}


@router.get("/work-runs/{identifier}/artifacts")
async def artifacts(identifier: UUID, request: Request) -> dict:
    run = await owned(request, identifier)
    rows = await request.app.state.db.fetch(
        "SELECT id,path,digest,size,created_at FROM work_artifacts WHERE owner_id=%s AND run_id=%s "
        "AND snapshot_id=(SELECT id FROM work_snapshots WHERE run_id=%s ORDER BY created_at DESC,id DESC LIMIT 1) "
        "ORDER BY path",
        (run["owner_id"], identifier, identifier),
    )
    return {"items": rows}


@router.get("/work-artifacts/{identifier}/download")
async def download(identifier: UUID, request: Request) -> Response:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT size,digest,path FROM work_artifacts WHERE id=%s AND owner_id=%s",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "成果不存在或无权访问")
    storage = WorkArtifacts(request.app.state.db, request.app.state.settings.data_dir / "work-artifacts")
    try:
        content = storage.path(identifier).read_bytes()
    except OSError:
        fail(503, "ARTIFACT_UNAVAILABLE", "成果文件暂不可用")
    if len(content) != rows[0]["size"] or hashlib.sha256(content).hexdigest() != rows[0]["digest"]:
        fail(503, "ARTIFACT_CORRUPTED", "成果校验失败，请检查存储")
    filename = quote(PurePosixPath(rows[0]["path"]).name, safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/work-capabilities")
async def capabilities(request: Request) -> dict:
    await identity(request)
    online = await request.app.state.db.fetch(
        "SELECT 1 FROM work_worker_instances WHERE heartbeat_at>now()-interval '45 seconds' LIMIT 1"
    )
    return {
        "enabled": request.app.state.settings.work_enabled and bool(online),
        "worker_online": bool(online),
        "network": "默认禁止出站；可显式授权公网域名，内网、回环和元数据地址始终禁止",
        "command_approval": True,
    }


@router.get("/work-runs/{identifier}/operations")
async def operations(identifier: UUID, request: Request) -> dict:
    await owned(request, identifier)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,name,args,status,exit_code,output,created_at,started_at,finished_at "
            "FROM operation_executions WHERE run_id=%s ORDER BY created_at,action_index",
            (identifier,),
        )
    }


@router.post("/work-runs/{identifier}/pause", status_code=202)
async def pause_run(identifier: UUID, request: Request) -> dict:
    run = await owned(request, identifier)
    rows = await request.app.state.db.fetch(
        "UPDATE work_queue_runs SET pause_requested=true,status=CASE WHEN status IN ('PENDING','WAITING_APPROVAL') "
        "THEN 'PAUSED' ELSE status END,updated_at=now() WHERE id=%s AND owner_id=%s "
        "AND status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED') RETURNING status",
        (identifier, run["owner_id"]),
    )
    if not rows:
        fail(409, "WORK_NOT_ACTIVE", "此运行已经结束，不能暂停")
    return {
        "accepted": True,
        "status": rows[0]["status"],
        "message": "执行中的任务将在下一次模型调用边界或审批检查点安全暂停",
    }


@router.post("/work-runs/{identifier}/resume", status_code=202)
async def resume_run(identifier: UUID, request: Request) -> dict:
    run = await owned(request, identifier)
    if not request.app.state.settings.work_enabled:
        fail(503, "WORK_DISABLED", "任务执行尚未启用")
    async with request.app.state.db.pool.connection() as connection:
        current = await (
            await connection.execute(
                "SELECT task_id,status FROM work_queue_runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                (identifier, run["owner_id"]),
            )
        ).fetchone()
        if not current or current["status"] != "PAUSED":
            fail(409, "WORK_NOT_PAUSED", "只有已暂停的运行可以继续")
        if await (
            await connection.execute(
                "SELECT 1 FROM work_browser_controls c JOIN work_queue_runs r ON r.id=c.run_id "
                "WHERE r.task_id=%s AND c.status<>'CLOSED' LIMIT 1",
                (current["task_id"],),
            )
        ).fetchone():
            fail(409, "BROWSER_CONTROL_ACTIVE", "请先结束人工控制并确认浏览器状态，再恢复智能体")
        row = await (
            await connection.execute(
                "UPDATE work_queue_runs r SET pause_requested=false,status=CASE WHEN EXISTS "
                "(SELECT 1 FROM work_approvals a WHERE a.run_id=r.id AND a.consumed_at IS NULL AND a.decision IS NULL) "
                "THEN 'WAITING_APPROVAL' ELSE 'PENDING' END,updated_at=now() WHERE r.id=%s RETURNING status",
                (identifier,),
            )
        ).fetchone()
    return {"accepted": True, "status": row["status"]}


@router.get("/work-artifacts/{identifier}/diff")
async def artifact_diff(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT a.path,a.digest,a.size,r.input_files FROM work_artifacts a JOIN work_queue_runs r ON r.id=a.run_id "
        "WHERE a.id=%s AND a.owner_id=%s AND r.owner_id=%s",
        (identifier, user["id"], user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "成果不存在或无权访问")
    row = rows[0]
    try:
        content = (request.app.state.settings.data_dir / "work-artifacts" / str(identifier)).read_bytes()
        if len(content) != row["size"] or hashlib.sha256(content).hexdigest() != row["digest"]:
            raise ValueError("Artifact changed")
        after = content.decode("utf-8")
        original = next((item for item in row["input_files"] if item["path"] == row["path"]), None)
        before = ""
        if original:
            data = (request.app.state.settings.data_dir / "work-inputs" / str(UUID(original["id"]))).read_bytes()
            if len(data) != original["size"] or hashlib.sha256(data).hexdigest() != original["digest"]:
                raise ValueError("Input changed")
            before = data.decode("utf-8")
    except (ValueError, OSError, UnicodeError):
        fail(422, "DIFF_UNAVAILABLE", "此成果不是可对照的文本，或文件校验失败")
    if max(len(before), len(after)) > 40000 or max(len(before.splitlines()), len(after.splitlines())) > 2000:
        fail(413, "DIFF_TOO_LARGE", "逐行差异最多支持 4 万字符和 2000 行，请下载后对照")
    changes = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="初始输入" + row["path"],
            tofile="成果" + row["path"],
            lineterm="",
        )
    )
    return {"diff": "\n".join(changes[:1000]), "truncated": len(changes) > 1000}


@router.get("/work-runs/{identifier}/recovery")
async def recovery_info(identifier: UUID, request: Request) -> dict:
    await owned(request, identifier)
    jobs = await request.app.state.db.fetch(
        "SELECT status,snapshot_id,error_code,not_before,updated_at FROM work_recovery_jobs WHERE run_id=%s",
        (identifier,),
    )
    sessions = await request.app.state.db.fetch(
        "SELECT kind,state,retention_required,data_lost FROM work_sandbox_sessions WHERE run_id=%s ORDER BY created_at",
        (identifier,),
    )
    return {"job": jobs[0] if jobs else None, "sessions": sessions}


@router.post("/work-runs/{identifier}/recovery", status_code=202)
async def recovery_request(identifier: UUID, request: Request) -> dict:
    run = await owned(request, identifier)
    if run["status"] not in {"NEEDS_REVIEW", "FAILED", "CANCELLED", "COMPLETED"}:
        fail(409, "RECOVERY_NOT_STOPPED", "请先停止任务，再导出保留的工作区")
    retained = await request.app.state.db.fetch(
        "SELECT 1 FROM work_sandbox_sessions WHERE run_id=%s AND retention_required AND state<>'RELEASED'",
        (identifier,),
    )
    if not retained:
        fail(409, "RECOVERY_NOT_NEEDED", "没有可救援的保留工作区，请查看已保存成果")
    await request.app.state.db.execute(
        "INSERT INTO work_recovery_jobs(run_id,owner_id) VALUES(%s,%s) ON CONFLICT(run_id) DO UPDATE "
        "SET status='PENDING',not_before=now()+interval '90 seconds',error_code=NULL,updated_at=now() "
        "WHERE work_recovery_jobs.status='FAILED'",
        (identifier, run["owner_id"]),
    )
    return {"accepted": True, "message": "已排队救援，等待 90 秒安全窗口后停止残余进程并导出文件，不重放模型或命令"}
