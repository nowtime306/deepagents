"""Owner-scoped browser viewing and exclusive human control of retained task sessions."""

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from opensandbox.sandbox import Sandbox
from psycopg.types.json import Jsonb

from app.browser_bridge import BrowserBridge
from app.browser_state import public_result
from app.browser_tools import validate_steps
from app.durable_sandboxes import DurableSandboxes
from app.errors import APIError, fail
from app.permissions import require_account
from app.work_backend import WorkBackend

PUBLIC_CONTROL = "id,run_id,status,lease_until,deadline,error_code,release_environment,clear_login,created_at"
logger = logging.getLogger(__name__)


class BrowserWorkbench:
    def __init__(self, database, vault, config, artifacts) -> None:
        self.database, self.vault, self.artifacts = database, vault, artifacts
        self.sandboxes = DurableSandboxes(database, config)

    async def run(self, identifier, owner):
        rows = await self.database.fetch(
            "SELECT * FROM work_queue_runs WHERE id=%s AND owner_id=%s", (identifier, owner)
        )
        if not rows or rows[0]["environment"] != "browser":
            fail(404, "BROWSER_RUN_NOT_FOUND", "浏览器任务不存在或无权访问")
        return rows[0]

    async def control(self, identifier, owner):
        rows = await self.database.fetch(
            "SELECT * FROM work_browser_controls WHERE id=%s AND owner_id=%s", (identifier, owner)
        )
        if not rows:
            fail(404, "BROWSER_CONTROL_NOT_FOUND", "人工控制不存在或无权访问")
        return rows[0]

    async def guard_view(self, run, session):
        await require_account(self.database, run["owner_id"], "work.read")
        rows = await self.database.fetch(
            "SELECT 1 FROM work_sandbox_sessions s JOIN work_queue_runs r ON r.id=s.run_id "
            "WHERE s.id=%s AND r.id=%s AND r.owner_id=%s AND s.state='READY' "
            "AND (r.status IN ('PAUSED','WAITING_APPROVAL','NEEDS_REVIEW') OR "
            "(r.status='RUNNING' AND r.lease_until>now() AND NOT r.cancel_requested))",
            (session["id"], run["id"], run["owner_id"]),
        )
        if not rows:
            fail(409, "BROWSER_VIEW_STOPPED", "浏览器环境已结束或正在回收")

    async def guard_control(self, control, *, cleanup=False):
        if not cleanup:
            await require_account(self.database, control["owner_id"], "work.execute")
        rows = await self.database.fetch(
            "SELECT 1 FROM work_browser_controls c JOIN work_queue_runs r ON r.id=c.run_id "
            "JOIN work_tasks t ON t.id=r.task_id WHERE c.id=%s AND c.owner_id=%s AND r.owner_id=c.owner_id "
            "AND t.owner_id=c.owner_id AND c.lease_token=%s AND c.lease_until>now() "
            "AND ((%s AND c.status='CLOSING') OR (NOT %s AND c.status IN ('CREATING','READY') AND c.deadline>now() "
            "AND (r.status='PAUSED' OR (r.status='NEEDS_REVIEW' AND r.updated_at<now()-interval '90 seconds')) "
            "AND NOT EXISTS(SELECT 1 FROM work_recovery_jobs j WHERE j.run_id=r.id AND j.status='RUNNING')))",
            (control["id"], control["owner_id"], control["lease_token"], cleanup, cleanup),
        )
        if not rows:
            fail(409, "BROWSER_CONTROL_EXPIRED", "人工控制已结束、过期，或任务尚未安全暂停")

    @asynccontextmanager
    async def connect(self, run, *, control=None, cleanup=False):
        rows = await self.database.fetch(
            "SELECT s.*,t.token_ciphertext,t.state_revision FROM work_sandbox_sessions s "
            "JOIN work_browser_transports t ON t.session_id=s.id AND t.owner_id=s.owner_id "
            "WHERE s.run_id=%s AND s.owner_id=%s AND s.state='READY' AND s.kind='browser' "
            "AND t.token_ciphertext<>'' ORDER BY s.created_at DESC LIMIT 1",
            (run["id"], run["owner_id"]),
        )
        if not rows:
            fail(409, "BROWSER_NOT_OPEN", "浏览器尚未打开或已过期；请先由任务打开页面")
        session = rows[0]

        async def guard():
            if control:
                await self.guard_control(control, cleanup=cleanup)
            else:
                await self.guard_view(run, session)

        await guard()
        sandbox = await Sandbox.connect(
            session["sandbox_id"], connection_config=self.sandboxes.config, skip_health_check=True
        )
        try:
            info = await sandbox.get_info()
            namespace = str((await self.database.fetch("SELECT id FROM work_sandbox_namespace"))[0]["id"])
            metadata = info.metadata or {}
            if any(
                metadata.get(key) != value
                for key, value in {
                    "purpose": "owner-agent-work",
                    "namespace": namespace,
                    "session": str(session["id"]),
                }.items()
            ):
                fail(403, "BROWSER_SESSION_MISMATCH", "浏览器环境与当前任务归属不一致")
            backend = WorkBackend(sandbox, guard)
            bridge = BrowserBridge(self.database, self.vault, backend, run, session)
            bridge.transport.key = self.vault.decrypt(session["token_ciphertext"]).get_secret_value()
            bridge.revision, bridge.started = session["state_revision"], True
            yield bridge
        finally:
            await sandbox.close()

    async def action(self, identifier, owner, request_id: UUID, kind: str, parameters: dict):
        control = await self.control(identifier, owner)
        if control["status"] != "READY":
            fail(409, "BROWSER_CONTROL_NOT_READY", "人工控制尚未就绪或已结束")
        run = await self.run(control["run_id"], owner)
        await self.guard_control(control)
        validate_action(kind, parameters, run["network_domains"])
        async with self.connect(run, control=control) as bridge:
            fingerprint = hmac.new(
                bridge.transport.key.encode(), json.dumps([kind, parameters], sort_keys=True).encode(), hashlib.sha256
            ).hexdigest()
            async with self.database.pool.connection() as connection:
                await connection.execute("SELECT pg_advisory_xact_lock(710036,hashtext(%s))", (str(identifier),))
                await self.guard_control(control)
                previous = await (
                    await connection.execute(
                        "SELECT * FROM work_browser_actions WHERE control_id=%s AND request_id=%s",
                        (identifier, request_id),
                    )
                ).fetchone()
                if previous:
                    if previous["digest"] != fingerprint:
                        fail(409, "BROWSER_ACTION_CONFLICT", "相同操作标识对应了不同内容")
                    if previous["status"] != "SUCCEEDED":
                        fail(409, "BROWSER_ACTION_UNKNOWN", "此操作尚未确认，不能再次执行")
                    return previous["result"]
                if await (
                    await connection.execute(
                        "SELECT 1 FROM work_browser_actions WHERE control_id=%s AND "
                        "(status='PENDING' OR (status='UNKNOWN' AND reviewed_at IS NULL)) LIMIT 1",
                        (identifier,),
                    )
                ).fetchone():
                    fail(409, "BROWSER_ACTION_PENDING", "请等待上一操作完成，或先检查未确认结果")
                await connection.execute(
                    "INSERT INTO work_browser_actions(control_id,request_id,kind,digest) VALUES(%s,%s,%s,%s)",
                    (identifier, request_id, kind, fingerprint),
                )
            try:
                receipt = await bridge.transport.request(
                    {"op": kind, "parameters": parameters, "request_id": request_id.hex}
                )
                await bridge.save()
                if not receipt.get("ok"):
                    fail(409, "BROWSER_ACTION_UNKNOWN", "页面操作结果未确认，请先检查当前页面，不要重复提交")
                result = public_result(receipt["result"])
                await self.database.execute(
                    "UPDATE work_browser_actions SET status='SUCCEEDED',result=%s "
                    "WHERE control_id=%s AND request_id=%s",
                    (Jsonb(result), identifier, request_id),
                )
                return result
            except BaseException:
                await self.database.execute(
                    "UPDATE work_browser_actions SET status='UNKNOWN' WHERE control_id=%s "
                    "AND request_id=%s AND status='PENDING'",
                    (identifier, request_id),
                )
                raise

    async def admit(self, run_id, owner, request_id, *, release_environment=False, clear_login=False):
        release_environment = release_environment or clear_login
        await require_account(self.database, owner, "work.execute")
        run = await self.run(run_id, owner)
        async with self.database.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(710039,hashtext(%s))", (str(owner),))
            await connection.execute("SELECT pg_advisory_xact_lock(710038,hashtext(%s))", (str(run["task_id"]),))
            current = await (
                await connection.execute(
                    "SELECT * FROM work_queue_runs WHERE id=%s AND owner_id=%s FOR UPDATE",
                    (run_id, owner),
                )
            ).fetchone()
            if current["status"] != "PAUSED" and not (
                current["status"] == "NEEDS_REVIEW"
                and (
                    await (
                        await connection.execute(
                            "SELECT 1 FROM work_queue_runs WHERE id=%s AND updated_at<now()-interval '90 seconds'",
                            (run_id,),
                        )
                    ).fetchone()
                )
            ):
                fail(409, "BROWSER_NOT_PAUSED", "请先安全暂停任务；结果未知的中断需要等待90秒安全窗口")
            if await (
                await connection.execute(
                    "SELECT 1 FROM work_recovery_jobs WHERE run_id=%s AND status='RUNNING'",
                    (run_id,),
                )
            ).fetchone():
                fail(409, "BROWSER_RECOVERY_ACTIVE", "任务正在救援，请等待导出完成")
            previous = await (
                await connection.execute(
                    "SELECT * FROM work_browser_controls WHERE owner_id=%s AND (request_id=%s OR status<>'CLOSED') "
                    "ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                    (owner, request_id),
                )
            ).fetchone()
            if previous:
                if (
                    previous["request_id"] != request_id
                    or previous["run_id"] != run_id
                    or previous["release_environment"] != release_environment
                    or previous["clear_login"] != clear_login
                ):
                    fail(409, "BROWSER_CONTROL_BUSY", "已有人工控制，或相同请求标识对应了不同操作，请先结束原控制")
                return previous["id"]
            session = await (
                await connection.execute(
                    "SELECT s.id,s.state,t.token_ciphertext FROM work_sandbox_sessions s "
                    "LEFT JOIN work_browser_transports t "
                    "ON t.session_id=s.id WHERE s.run_id=%s AND s.owner_id=%s "
                    "AND s.kind='browser' AND s.state<>'RELEASED' "
                    "ORDER BY s.created_at DESC LIMIT 1 FOR UPDATE OF s",
                    (run_id, owner),
                )
            ).fetchone()
            if not release_environment and (
                not session or session["state"] != "READY" or not session["token_ciphertext"]
            ):
                fail(409, "BROWSER_NOT_OPEN", "任务尚未打开浏览器，或环境已过期；请先让任务打开页面")
            identifier = uuid4()
            await connection.execute(
                "INSERT INTO work_browser_controls(id,run_id,owner_id,request_id,lease_token,status,session_id,"
                "release_environment,clear_login,lease_until) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "CASE WHEN %s THEN now() ELSE now()+interval '120 seconds' END)",
                (
                    identifier,
                    run_id,
                    owner,
                    request_id,
                    uuid4(),
                    "CLOSING" if release_environment else "READY",
                    session["id"] if session else None,
                    release_environment,
                    clear_login,
                    release_environment,
                ),
            )
            if session:
                await connection.execute(
                    "UPDATE work_sandbox_sessions SET browser_persistent=false,retention_required=true WHERE id=%s",
                    (session["id"],),
                )
        return identifier

    async def request_close(self, identifier, owner, *, release_environment=False, clear_login=False):
        release_environment = release_environment or clear_login
        await require_account(self.database, owner, "work.execute")
        control = await self.control(identifier, owner)
        if control["status"] == "CLOSED":
            return
        pending = await self.database.fetch(
            "SELECT 1 FROM work_browser_actions WHERE control_id=%s AND status='PENDING' "
            "AND created_at>now()-interval '90 seconds' LIMIT 1",
            (identifier,),
        )
        if pending:
            fail(409, "BROWSER_ACTION_PENDING", "上一操作仍在执行，请等待结果后结束控制")
        await self.database.execute(
            "UPDATE work_browser_controls SET status='CLOSING',release_environment=release_environment OR %s,"
            "clear_login=clear_login OR %s,lease_until=now(),updated_at=now() "
            "WHERE id=%s AND owner_id=%s AND status<>'CLOSED'",
            (release_environment, clear_login, identifier, owner),
        )

    async def finish_close(self, control):
        run = await self.run(control["run_id"], control["owner_id"])
        unresolved = await self.database.fetch(
            "SELECT 1 FROM work_browser_actions WHERE control_id=%s AND status<>'SUCCEEDED' "
            "AND reviewed_at IS NULL LIMIT 1",
            (control["id"],),
        )
        if unresolved and not control["release_environment"]:
            fail(409, "BROWSER_ACTION_REVIEW", "仍有未确认操作，请检查页面后确认，或明确释放环境")
        sessions = await self.database.fetch(
            "SELECT * FROM work_sandbox_sessions WHERE run_id=%s AND kind='browser' AND state<>'RELEASED'",
            (run["id"],),
        )
        for session in sessions:
            if not control["clear_login"]:
                try:
                    async with self.connect(run, control=control, cleanup=True) as bridge:
                        await bridge.save()
                        await self.artifacts.capture(run, bridge.backend)
                except Exception:
                    if not control["release_environment"]:
                        raise
                    await self.database.execute(
                        "UPDATE work_browser_states SET status='REVIEW' WHERE task_id=%s AND owner_id=%s",
                        (run["task_id"], run["owner_id"]),
                    )
            await self.guard_control(control, cleanup=True)
            if control["release_environment"] or run["status"] != "PAUSED":
                if not await self.sandboxes.release(session["id"]):
                    fail(409, "BROWSER_RELEASE_UNKNOWN", "浏览器环境销毁尚未确认，仍保留配额")
            else:
                await self.database.execute(
                    "UPDATE work_sandbox_sessions SET browser_persistent=true,retention_required=true,updated_at=now() "
                    "WHERE id=%s AND state='READY'",
                    (session["id"],),
                )
        async with self.database.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(710035,hashtext(%s))", (str(run["task_id"]),))
            await self.guard_control(control, cleanup=True)
            if control["clear_login"]:
                await connection.execute(
                    "INSERT INTO work_browser_states(task_id,owner_id) VALUES(%s,%s) ON CONFLICT(task_id) DO UPDATE "
                    "SET state_ciphertext='',revision=work_browser_states.revision+1,status='EMPTY',updated_at=now()",
                    (run["task_id"], run["owner_id"]),
                )
            await connection.execute(
                "UPDATE work_browser_controls SET status='CLOSED',error_code=NULL,updated_at=now() "
                "WHERE id=%s AND lease_token=%s AND status='CLOSING'",
                (control["id"], control["lease_token"]),
            )

    async def tick(self):
        await self.database.execute(
            "UPDATE work_browser_actions SET status='UNKNOWN' WHERE status='PENDING' "
            "AND created_at<now()-interval '90 seconds'"
        )
        active = await self.database.fetch("SELECT * FROM work_browser_controls WHERE status='READY' LIMIT 50")
        for control in active:
            try:
                await self.guard_control(control)
            except APIError:
                pending = await self.database.fetch(
                    "SELECT 1 FROM work_browser_actions WHERE control_id=%s AND status='PENDING' "
                    "AND created_at>now()-interval '90 seconds' LIMIT 1",
                    (control["id"],),
                )
                if not pending:
                    await self.database.execute(
                        "UPDATE work_browser_controls SET status='CLOSING',lease_until=now(),updated_at=now() "
                        "WHERE id=%s AND status='READY'",
                        (control["id"],),
                    )
        async with self.database.pool.connection() as connection:
            control = await (
                await connection.execute(
                    "SELECT * FROM work_browser_controls WHERE status='CLOSING' AND lease_until<=now() "
                    "ORDER BY updated_at FOR UPDATE SKIP LOCKED LIMIT 1",
                )
            ).fetchone()
            if not control:
                return False
            control["lease_token"] = uuid4()
            await connection.execute(
                "UPDATE work_browser_controls SET lease_token=%s,lease_until=now()+interval '120 seconds' WHERE id=%s",
                (control["lease_token"], control["id"]),
            )
        try:
            await self.finish_close(control)
        except BaseException as error:
            await self.database.execute(
                "UPDATE work_browser_controls SET status='UNKNOWN',error_code=%s,updated_at=now() "
                "WHERE id=%s AND lease_token=%s AND status='CLOSING'",
                (
                    error.code if isinstance(error, APIError) else "BROWSER_CLOSE_UNKNOWN",
                    control["id"],
                    control["lease_token"],
                ),
            )
            if not isinstance(error, Exception):
                raise
        return True

    async def serve(self):
        while True:
            try:
                if await self.tick():
                    continue
            except Exception as error:
                # State remains durable; never retry a page action from this maintenance loop.
                logger.error("Browser maintenance failed (%s)", type(error).__name__)
            await asyncio.sleep(1)

    async def close(self):
        await self.sandboxes.close()


def validate_action(kind: str, parameters: dict, domains: list[str]) -> None:
    if kind in {"open", "new_tab", "select_tab", "close_tab", "scroll"}:
        validate_steps([{"op": kind, "parameters": parameters}], domains, persistent=True)
    elif kind == "pointer":
        if set(parameters) != {"x", "y"} or any(
            type(parameters[key]) is not int or not 0 <= parameters[key] < bound
            for key, bound in [("x", 1280), ("y", 720)]
        ):
            fail(422, "BROWSER_ACTION_INVALID", "点击位置超出浏览器视图")
    elif kind == "type":
        if (
            set(parameters) != {"text"}
            or not isinstance(parameters["text"], str)
            or not 1 <= len(parameters["text"]) <= 4000
        ):
            fail(422, "BROWSER_ACTION_INVALID", "输入内容需为 1–4000 个字符")
    elif kind == "key":
        if set(parameters) != {"key"} or not isinstance(parameters["key"], str) or parameters["key"] not in {
            "Enter",
            "Tab",
            "Escape",
            "Backspace",
            "Delete",
            "ArrowUp",
            "ArrowDown",
            "ArrowLeft",
            "ArrowRight",
            "Control+a",
        }:
            fail(422, "BROWSER_ACTION_INVALID", "此快捷键未开放")
    else:
        fail(422, "BROWSER_ACTION_INVALID", "此人工操作未开放")
