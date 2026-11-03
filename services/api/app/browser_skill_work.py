"""Single-use Work approval and fresh semantic targeting for published paths."""

import json
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from langchain_core.tools import StructuredTool

from app.browser_skill_trace import validate_draft
from app.errors import fail
from app.permissions import require_account
from app.resource_runtime import current_scope
from app.resource_tools import digest, skill_binding, used


def resolve_target(text: str, target: dict) -> str:
    """Match the pinned VOM1 syntax exactly; never reuse a recorded ref."""
    matches = []
    for line in text.splitlines():
        match = re.match(r'^\s*(@e\d+) ([a-zA-Z]+) ("(?:[^"\\]|\\.)*")(.*)$', line)
        if not match:
            continue
        ref, role, name, tail = match.groups()
        context = re.search(r" \[ctx: (.*)\]$", tail)
        if (
            role == target["role"]
            and json.loads(name) == target["name"]
            and (context.group(1) if context else "") == target.get("ctx", "")
        ):
            matches.append(ref)
    if len(matches) != 1:
        fail(409, "BROWSER_TARGET_AMBIGUOUS", "当前目标缺失或不唯一，已停止操作")
    return matches[0]


def browser_skill_tool(database, runtime, backend, run: dict, selection: dict):
    async def run_browser_skill(
        skill_id: str,
        version: int,
        parameters: dict[str, str],
        execution_boundary: Literal["local_authenticated_browser"],
        skill_name: str,
        step_count: int,
    ) -> str:
        args = {
            "skill_id": skill_id,
            "version": version,
            "parameters": parameters,
            "execution_boundary": execution_boundary,
            "skill_name": skill_name,
            "step_count": step_count,
        }
        if skill_id not in [str(v) for v in selection.get("skill_ids", [])]:
            fail(403, "BROWSER_SKILL_NOT_SELECTED", "该技能未绑定当前任务")
        if backend.journal is None:
            raise PermissionError("BrowserSkill requires Work approval")
        async with database.pool.connection() as connection:
            locked = await (await connection.execute("SELECT pg_try_advisory_xact_lock(710081) AS locked")).fetchone()
            if not locked["locked"] or await database.fetch(
                "SELECT 1 FROM browser_skill_recordings WHERE status IN ('starting','recording','stopping')"
            ):
                fail(409, "BROWSER_BUSY", "专用连接正在使用")
            rows = await database.fetch(
                "SELECT v.trajectory,s.name FROM browser_skill_versions v JOIN skills s ON s.id=v.skill_id "
                "WHERE v.skill_id=%s AND v.version=%s AND v.owner_id=%s AND s.owner_id=%s AND s.enabled "
                "",
                (UUID(skill_id), version, run["owner_id"], run["owner_id"]),
            )
            if not rows:
                fail(404, "NOT_FOUND", "发布版本已不可用")
            trajectory = rows[0]["trajectory"]
            scope = current_scope()
            frozen = scope.values.get(("skill", str(skill_id))) if scope else None
            if scope and not frozen:
                fail(409, "BROWSER_SKILL_NOT_RESOLVED", "本次运行尚未固定技能内容，不能执行浏览器路径")
            if scope:
                await skill_binding(database, run["owner_id"], selection, skill_id)
            if frozen:
                if frozen["version"] != version:
                    fail(409, "BROWSER_SKILL_VERSION_MISMATCH", "审批版本与本次运行固定技能版本不符")
                try:
                    pinned = json.loads(frozen["files"]["trajectory.json"])
                except (KeyError, ValueError, TypeError):
                    fail(409, "BROWSER_SKILL_PATH_MISSING", "本次固定技能不包含有效浏览器路径")
                if digest(pinned) != digest(trajectory):
                    fail(409, "BROWSER_SKILL_PATH_CHANGED", "发布路径与本次固定技能内容不符，已停止执行")
            validate_draft(trajectory)
            if skill_name != (frozen["name"] if frozen else rows[0]["name"]) or step_count != len(trajectory["steps"]):
                fail(409, "BROWSER_APPROVAL_SUMMARY_CHANGED", "审批中的技能名称或步骤数不符合发布版本")
            names = {p["name"] for p in trajectory["parameters"]}
            if set(parameters) != names or any(
                not isinstance(v, str) or not 1 <= len(v) <= 2000 for v in parameters.values()
            ):
                fail(422, "BROWSER_PARAMETER_INVALID", "请提供全部必填参数")
            for step in trajectory["steps"]:
                if "url" in step:
                    url = urlsplit(step["url"])
                    if (
                        url.scheme != "https"
                        or url.hostname not in run["network_domains"]
                        or url.port not in (None, 443)
                    ):
                        fail(403, "BROWSER_DOMAIN_DENIED", "路径超出任务授权域名")
            await guard(database, backend, run)
            operation = await backend.journal.start("run_browser_skill", args, semantic_digest=digest(trajectory))
            await used("skill", skill_id)
            session = None
            completed = 0
            try:
                health = await runtime.status()
                if not health["ready"]:
                    fail(503, health["code"], health["message"])
                reply = json.loads(await runtime.command("session", "start", "--browser", runtime.instance, "--json"))
                session = reply["session_id"]
                for step in trajectory["steps"]:
                    await guard(database, backend, run)
                    if scope:
                        await skill_binding(database, run["owner_id"], selection, skill_id)
                    current = await database.fetch(
                        "SELECT 1 FROM skills WHERE id=%s AND owner_id=%s AND enabled",
                        (UUID(skill_id), run["owner_id"]),
                    )
                    if not current:
                        fail(403, "BROWSER_SKILL_REVOKED", "技能权限已撤销")
                    if step["op"] != "navigate":
                        await check_session_domains(runtime, session, run["network_domains"])
                    await replay_step(runtime, session, step, parameters)
                    await check_session_domains(runtime, session, run["network_domains"])
                    completed += 1
                result = {"ok": True, "completed_steps": completed}
                await backend.journal.finish(operation, 0, json.dumps(result))
                return json.dumps(result)
            except BaseException:
                await backend.journal.unknown(operation)
                raise
            finally:
                if session:
                    await runtime.command("session", "stop", session, "--json")

    tool = StructuredTool.from_function(
        coroutine=run_browser_skill,
        description="在已显式授权的本机已登录浏览器执行绑定Skill路径；无网络硬隔离，可能使用登录账号提交数据。域名仅约束顶层导航。",
    )
    tool.metadata = {
        "requires_approval": True,
        "target": (
            "批准执行本次完整录制路径：本机已登录浏览器，无网络硬隔离；"
            "可能使用登录账号填写、点击和提交数据，域名仅约束顶层导航"
        ),
        "execution_boundary": "local_authenticated_browser",
        "approval_scope": "entire_recorded_path",
    }
    return tool


async def check_session_domains(runtime, session: str, domains: list[str]) -> None:
    """Stop on unexpected tab destinations; this is not a network firewall."""
    result = json.loads(await runtime.command("tab", "list", "--session", session, "--scope", "agent", "--json"))
    tabs = result.get("tabs", [])
    if len(tabs) != 1:
        fail(409, "BROWSER_TAB_CHANGED", "标签页发生变化，已停止")
    url = urlsplit(tabs[0].get("url", ""))
    if url.scheme != "https" or url.hostname not in domains or url.port not in (None, 443):
        fail(403, "BROWSER_DOMAIN_DENIED", "页面已离开任务授权域名，已停止")


async def replay_step(runtime, session: str, step: dict, parameters: dict[str, str]) -> None:
    """Perform one captured operation with a new strict semantic observation."""
    if step["op"] == "navigate":
        await runtime.command("navigate", step["url"], "--session", session, "--json")
        return
    sync = json.loads(await runtime.command("cooperate", "sync", "--session", session, "--policy", "strict", "--json"))
    state = sync.get("state", {})
    if state.get("mode") != "ready" or state.get("editing") or sync.get("frame_gaps"):
        fail(409, "BROWSER_HUMAN_CONTROL", "人工控制或观察不完整，已停止")
    observation = sync.get("observation", {})
    if observation.get("truncated"):
        fail(409, "BROWSER_OBSERVATION_TRUNCATED", "页面观察不完整")
    ref = resolve_target(observation.get("text", ""), step["target"])
    command = [step["op"], "--ref", ref, "--session", session, "--context", sync["context_id"], "--json"]
    if step["op"] in {"fill", "select"}:
        command.extend(["--value", parameters[step["parameter"]]])
    await runtime.command(*command, seconds=40)


async def guard(database, backend, run: dict) -> None:
    await backend.guard()
    rows = await database.fetch(
        "SELECT allow_local_browser,agent_snapshot,environment FROM work_queue_runs "
        "WHERE id=%s AND owner_id=%s AND status='RUNNING' AND lease_token=%s AND lease_until>now() "
        "AND NOT cancel_requested",
        (run["id"], run["owner_id"], run["lease_token"]),
    )
    if (
        not rows
        or rows[0]["allow_local_browser"] is not True
        or rows[0]["environment"] != "browser"
        or (rows[0]["agent_snapshot"] or {}).get("allow_local_browser") is not True
    ):
        fail(403, "BROWSER_LOCAL_OPT_IN_REQUIRED", "此运行未显式授权使用本机已登录浏览器")
    account = await require_account(database, run["owner_id"], "work.execute")
    await require_account(database, run["owner_id"], "skills.read")
    if not account["is_admin"]:
        fail(403, "BROWSER_HOST_ADMIN_REQUIRED", "本机专用浏览器仅供主机管理员使用")
