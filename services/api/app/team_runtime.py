"""Bounded native task delegation with durable attribution and live authorization."""

import asyncio
import hashlib
import json
from uuid import UUID, uuid4

from deepagents import SubAgentMiddleware
from deepagents.backends import StateBackend
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.errors import APIError, fail
from app.graphs import build_ask
from app.harness import RunGuard
from app.team_config import subset
from app.team_context import active_member
from app.token_estimates import output_reserve
from app.web_search import search_tool


class TeamRun:
    def __init__(self, database, models, capabilities, factory, run, owner, mode, lease, snapshot, guard):
        self.database, self.models, self.capabilities, self.factory = database, models, capabilities, factory
        self.run, self.owner, self.mode, self.lease, self.snapshot, self.guard = (
            run,
            owner,
            mode,
            lease,
            snapshot,
            guard,
        )
        self.members = {item["name"]: item for item in snapshot.get("team_pins", [])}
        self.slots = asyncio.Semaphore(2)
        self.reserves = {}
        self.configs = {}
        self.table = "task_runs" if mode == "ask" else "work_queue_runs"
        self.column = "ask_run_id" if mode == "ask" else "work_run_id"

    async def authority(self, connection) -> None:
        row = await (
            await connection.execute(
                f"SELECT id FROM {self.table} WHERE id=%s AND status='RUNNING' AND NOT cancel_requested "
                "AND lease_token IS NOT DISTINCT FROM %s AND (lease_token IS NULL OR lease_until>now()) FOR UPDATE",
                (self.run, self.lease),
            )
        ).fetchone()
        if not row:
            fail(409, "TEAM_LEASE_LOST", "父运行已取消或租约失效，团队不能继续写入")

    async def check(self, member: dict) -> None:
        await self.guard()
        rows = await self.database.fetch(
            "SELECT knowledge_ids,skill_ids,mcp_ids,memory_enabled,web_enabled,web_mode,team "
            "FROM agents WHERE id=%s AND owner_id=%s AND enabled",
            (self.snapshot["id"], self.owner),
        )
        if not rows or not any(item["version_id"] == member["version_id"] for item in rows[0]["team"]):
            fail(403, "TEAM_MEMBER_REVOKED", "团队成员已移除或主智能体已停用")
        child = member["snapshot"]
        subset(rows[0], child)
        versions = await self.database.fetch(
            "SELECT a.knowledge_ids,a.skill_ids,a.mcp_ids,a.memory_enabled,a.web_enabled,a.web_mode "
            "FROM agent_versions v JOIN agents a ON a.id=v.agent_id WHERE v.id=%s AND v.owner_id=%s "
            "AND v.active AND a.enabled",
            (member["version_id"], self.owner),
        )
        if not versions:
            fail(403, "TEAM_MEMBER_REVOKED", "团队成员版本已下线或停用")
        subset(versions[0], child)
        context = active_member.get()
        if context:
            calls = await self.database.fetch(
                "SELECT status,cancel_requested FROM team_calls WHERE id=%s", (context["id"],)
            )
            if not calls or calls[0]["cancel_requested"] or calls[0]["status"] != "RUNNING":
                fail(409, "TEAM_MEMBER_CANCELLED", "该成员任务已取消或失效")

    async def middleware(self, workspace=None, *, project_id=None) -> list:
        specs = []
        for name, member in self.members.items():
            child = dict(member["snapshot"])
            # The conversation switch can further restrict a published team's search.
            if not self.snapshot.get("web_enabled"):
                child["web_enabled"] = False
            config = await self.models.get(child["model_id"])
            if config.revision != child["model_revision"]:
                fail(409, "TEAM_MODEL_CHANGED", "成员模型已变化，请重新发布版本")
            if child.get("model_routing"):
                from app.model_routing import resolve

                config = await resolve(
                    self.models,
                    self.owner,
                    self.run,
                    child["model_id"],
                    enabled=True,
                    scope="team:" + name,
                    expected=child.get("route_pin"),
                    tools=True,
                    openai_native=bool(child.get("web_enabled") and child.get("web_mode") == "openai_native"),
                )
            self.configs[name] = config
            config = config.model_copy(update={"max_output_tokens": min(output_reserve(config), 8192)})
            self.reserves[name] = output_reserve(config)
            model = self.factory(config)
            if hasattr(model, "max_retries"):
                model.max_retries = 0
            tools, middleware = await self.capabilities.for_owner(
                self.owner,
                child,
                workspace=workspace,
                project_id=project_id,
                usage_context={"id": self.run, "mode": self.mode, "lease_token": self.lease},
            )
            if child.get("web_enabled"):
                tools.append(
                    {"type": "web_search"}
                    if child["web_mode"] == "openai_native"
                    else search_tool(self.database, self.models.vault, self.owner, UUID(child["id"]))
                )

            async def guard(selected=member):
                await self.check(selected)

            graph = build_ask(
                model,
                True,
                tools=tools,
                middleware=[RunGuard(guard), *middleware],
                instructions=child["instructions"] + "\n本次团队角色目标：" + member["goal"],
                backend=workspace,
            )
            specs.append(
                {
                    "name": name,
                    "description": member["goal"],
                    "runnable": graph.with_config(metadata={"platform_role": name}),
                }
            )
        if not specs:
            return []
        return [
            SubAgentMiddleware(
                backend=workspace or StateBackend(),
                subagents=specs,
                private_state_keys=frozenset({"files", "model_call_count", "run_model_call_count"}),
            ),
            DelegationControl(self),
        ]

    async def claim(self, member: dict, tool_id: str, arguments: dict) -> dict:
        await self.check(member)
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        async with self.database.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(710022,hashtext(%s))", (str(self.run),))
            await self.authority(connection)
            row = await (
                await connection.execute(
                    f"SELECT * FROM team_calls WHERE {self.column}=%s AND tool_call_id=%s", (self.run, tool_id)
                )
            ).fetchone()
            if row:
                if row["input_digest"] != digest or row["role_name"] != member["name"]:
                    fail(409, "TEAM_CALL_ID_REUSED", "委派标识被不同参数复用，未执行新请求")
                return {**row, "repeated": True}
            count = await (
                await connection.execute(f"SELECT count(*) AS n FROM team_calls WHERE {self.column}=%s", (self.run,))
            ).fetchone()
            if count["n"] >= 8:
                fail(422, "TEAM_DELEGATION_LIMIT", "本轮达到八次委派上限")
            row = await (
                await connection.execute(
                    f"INSERT INTO team_calls(id,owner_id,{self.column},lease_token,tool_call_id,"
                    "role_name,version_id,model_id,input_digest) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        uuid4(),
                        self.owner,
                        self.run,
                        self.lease,
                        tool_id,
                        member["name"],
                        member["version_id"],
                        self.configs[member["name"]].id
                        if member["name"] in self.configs
                        else member["snapshot"]["model_id"],
                        digest,
                    ),
                )
            ).fetchone()
        return row

    async def finish(self, identifier, status: str, result: str, code: str | None = None) -> None:
        try:
            async with self.database.pool.connection() as connection:
                await self.authority(connection)
                await connection.execute(
                    "UPDATE team_calls SET status=%s,result=%s,error_code=%s,finished_at=now() "
                    "WHERE id=%s AND status='RUNNING' AND lease_token IS NOT DISTINCT FROM %s",
                    (status, result[:32000], code, identifier, self.lease),
                )
        except APIError as error:
            if error.code != "TEAM_LEASE_LOST":
                raise


class DelegationControl(AgentMiddleware):
    def __init__(self, team: TeamRun):
        self.team = team

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        if call["name"] != "task":
            return await handler(request)
        role = call["args"].get("subagent_type")
        member = self.team.members.get(role) if isinstance(role, str) else None
        if member is None:
            fail(422, "TEAM_MEMBER_INVALID", "只能委派给此团队明确配置的角色")
        async with self.team.slots:
            row = await self.team.claim(member, call["id"], call["args"])
            if row.get("repeated"):
                if row["status"] in {"RUNNING", "UNKNOWN"}:
                    fail(409, "TEAM_RESULT_UNKNOWN", "此委派已经发出但结果未知，停止执行，不自动重放")
                content = row["result"] or "此委派已经结束，未自动重放。"
                return ToolMessage(content=content, tool_call_id=call["id"])
            config = self.team.configs[member["name"]]
            token = active_member.set(
                {
                    "id": row["id"],
                    "role_name": member["name"],
                    "model_id": config.id,
                    "revision": config.revision,
                    "route_selection_id": config.route_selection_id,
                    "reserve": self.team.reserves[member["name"]],
                }
            )
            try:
                result = await handler(request)
                await self.team.check(member)
                messages = result.update.get("messages", []) if isinstance(result, Command) else [result]
                if messages and getattr(messages[-1], "status", None) == "error":
                    fail(422, "TEAM_INPUT_INVALID", "成员委派参数不正确，未完成执行")
                text = (
                    messages[-1].content if messages and isinstance(messages[-1].content, str) else "成员未返回文本结果"
                )
                await self.team.finish(row["id"], "SUCCEEDED", text)
                return result
            except asyncio.CancelledError:
                await self.team.finish(row["id"], "UNKNOWN", "成员执行中断，结果未知。", "TEAM_RESULT_UNKNOWN")
                raise
            except Exception as error:
                code = error.code if isinstance(error, APIError) else "TEAM_RESULT_UNKNOWN"
                status = (
                    "CANCELLED" if code == "TEAM_MEMBER_CANCELLED" else "UNKNOWN" if "UNKNOWN" in code else "FAILED"
                )
                text = "成员已取消。" if status == "CANCELLED" else "成员失败，未自动重试，请检查运行记录。"
                await self.team.finish(row["id"], status, text, code)
                if status == "UNKNOWN":
                    fail(409, "TEAM_RESULT_UNKNOWN", "成员请求结果未知，执行停止；请核对后明确发起新运行")
                return ToolMessage(content=text, tool_call_id=call["id"])
            finally:
                active_member.reset(token)
