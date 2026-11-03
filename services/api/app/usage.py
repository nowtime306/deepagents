"""Per-call usage ledger; absent provider statistics remain unknown rather than zero."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from app.automation_auth import authorize
from app.errors import fail
from app.harness import ASK_POLICY
from app.team_context import active_member
from app.token_estimates import DEFAULT_OUTPUT, message_tokens, text_tokens
from app.usage_details import reported_usage


class UsageRecorder(AsyncCallbackHandler):
    raise_error = True

    def __init__(
        self,
        database,
        owner: UUID,
        run: UUID,
        model: str,
        mode: str,
        *,
        revision: int | None = None,
        reserve: int = DEFAULT_OUTPUT,
        team_budget: bool = False,
        lease_token: UUID | None = None,
        call_kind: Literal["chat", "embedding", "rerank"] = "chat",
        input_estimate: int | None = None,
        authority: Callable[[], Awaitable[None]] | None = None,
        route_selection_id: UUID | None = None,
    ) -> None:
        self.database, self.owner, self.run, self.model, self.mode = database, owner, run, model, mode
        self.revision = revision
        self.reserve = reserve
        self.team_budget = team_budget
        self.lease_token = lease_token
        self.call_kind, self.input_estimate = call_kind, input_estimate
        self.authority = authority
        self.route_selection_id = route_selection_id
        self.first_tokens: set[UUID] = set()

    async def on_chat_model_start(self, serialized, messages, *, run_id: UUID, **kwargs) -> None:
        # Native summarization can call the model before graph middleware guards.
        await authorize(self.database, self.owner, self.run, self.mode)
        if self.authority:
            await self.authority()
        if self.lease_token and self.mode in {"ask", "work", "resource"}:
            table = {"ask": "task_runs", "work": "work_queue_runs", "resource": "resource_operations"}[self.mode]
            live = await self.database.fetch(
                f"SELECT 1 FROM {table} WHERE id=%s AND lease_token=%s AND lease_until>now() "
                "AND status='RUNNING' AND NOT cancel_requested",
                (self.run, self.lease_token),
            )
            if not live:
                fail(409, self.mode.upper() + "_LEASE_LOST", "执行租约已失效或运行已停止，未请求模型")
        member = active_member.get() or {}
        model = member.get("model_id", self.model) if self.call_kind == "chat" else self.model
        revision = member.get("revision", self.revision) if self.call_kind == "chat" else self.revision
        reserve = member.get("reserve", self.reserve) if self.call_kind == "chat" else self.reserve
        rows = await self.database.fetch(
            "SELECT revision,model,provider,protocol FROM model_configs WHERE id=%s AND enabled", (model,)
        )
        configuration = SimpleNamespace(**rows[0]) if rows else None
        if revision is not None:
            if not rows or rows[0]["revision"] != revision:
                fail(409, "MODEL_VERSION_CHANGED", "模型配置已变化或停用，请创建新运行")
        estimate = message_tokens(messages, configuration) + text_tokens(
            json.dumps(kwargs.get("invocation_params", {}).get("tools", []), ensure_ascii=False, default=str),
            configuration,
        )
        if self.input_estimate is not None:
            estimate = self.input_estimate
        async with self.database.pool.connection() as connection:
            from app.maintenance import slot
            from app.model_routing import authorize_selection

            if not await slot(connection):
                fail(503, "PLATFORM_MAINTENANCE", "平台正在离线备份，未发送模型请求")
            route = member.get("route_selection_id", self.route_selection_id) if self.call_kind == "chat" else None
            await authorize_selection(connection, self.owner, self.run, route, model, revision)
            table = "task_runs" if self.mode == "ask" else "work_queue_runs"
            batch = None
            if self.mode in {"ask", "work"}:
                batch = await (
                    await connection.execute(
                        f"SELECT b.* FROM agent_batches b JOIN batch_items i ON i.batch_id=b.id "
                        f"JOIN {table} r ON r.batch_item_id=i.id WHERE r.id=%s FOR UPDATE OF b",
                        (self.run,),
                    )
                ).fetchone()
            if batch:
                spent = await (
                    await connection.execute(
                        "SELECT count(*) AS calls,coalesce(sum(CASE WHEN u.status='KNOWN' "
                        "THEN u.input_tokens+u.output_tokens "
                        "ELSE coalesce(u.estimated_input_tokens,0)+coalesce(u.output_token_reserve,0) END),0) AS spent "
                        "FROM model_usage u JOIN batch_items i ON i.root_run_id=u.run_id "
                        "WHERE i.batch_id=%s AND u.owner_id=%s",
                        (batch["id"], self.owner),
                    )
                ).fetchone()
                if (
                    batch["status"] not in {"ACTIVE", "PAUSED"}
                    or spent["calls"] >= batch["model_call_limit"]
                    or spent["spent"] + estimate + reserve > batch["token_limit"]
                ):
                    fail(422, "BATCH_BUDGET_EXCEEDED", "此批次达到共享调用或 Token 预算，未发送新请求")
            await connection.execute("SELECT pg_advisory_xact_lock(710021,hashtext(%s))", (str(self.run),))
            if await (await connection.execute("SELECT 1 FROM model_usage WHERE call_id=%s", (run_id,))).fetchone():
                return
            work_budgeted = self.team_budget
            if self.mode == "work" and not work_budgeted:
                work = await (
                    await connection.execute(
                        "SELECT resource_budgeted FROM work_queue_runs WHERE id=%s",
                        (self.run,),
                    )
                ).fetchone()
                work_budgeted = bool(work and work["resource_budgeted"])
            if self.mode in {"ask", "resource", "workflow"} or work_budgeted:
                limit, tokens = ASK_POLICY.model_calls, ASK_POLICY.token_limit
                if self.mode == "workflow":
                    workflow = await (
                        await connection.execute(
                            "SELECT r.snapshot FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
                            "WHERE r.id=%s AND w.owner_id=%s AND r.status='RUNNING'",
                            (self.run, self.owner),
                        )
                    ).fetchone()
                    if not workflow:
                        fail(409, "WORKFLOW_STOPPED", "工作流运行已停止")
                    policy = workflow["snapshot"].get("definition", {})
                    limit, tokens = policy.get("model_calls", 20), policy.get("token_limit", 128000)
                if self.mode == "resource":
                    operation = await (
                        await connection.execute(
                            "SELECT model_call_limit,token_limit FROM resource_operations WHERE id=%s AND owner_id=%s "
                            "AND status='RUNNING' AND lease_until>now()",
                            (self.run, self.owner),
                        )
                    ).fetchone()
                    if not operation:
                        fail(409, "RESOURCE_LEASE_LOST", "资源操作租约失效")
                    limit, tokens = operation["model_call_limit"], operation["token_limit"]
                if self.mode == "ask":
                    policy = await (
                        await connection.execute("SELECT harness_policy FROM task_runs WHERE id=%s", (self.run,))
                    ).fetchone()
                    if policy:
                        stored = policy["harness_policy"] or {}
                        limit = min(limit, stored.get("model_calls", limit))
                        tokens = min(tokens, stored.get("token_limit", tokens))
                if self.mode == "work":
                    row = await (
                        await connection.execute(
                            "SELECT step_limit,token_limit FROM work_queue_runs WHERE id=%s AND owner_id=%s "
                            "AND status='RUNNING' AND lease_until>now() AND NOT cancel_requested",
                            (self.run, self.owner),
                        )
                    ).fetchone()
                    if not row:
                        fail(409, "TEAM_LEASE_LOST", "父任务不再可用")
                    limit, tokens = row["step_limit"], row["token_limit"]
                row = await (
                    await connection.execute(
                        "SELECT count(*) AS calls,coalesce(sum(CASE WHEN status='KNOWN' "
                        "THEN input_tokens+output_tokens "
                        "ELSE coalesce(estimated_input_tokens,0)+coalesce(output_token_reserve,0) END),0) AS spent "
                        "FROM model_usage WHERE run_id=%s AND owner_id=%s",
                        (self.run, self.owner),
                    )
                ).fetchone()
                if row["calls"] >= limit or row["spent"] + estimate + reserve > tokens:
                    fail(422, self.mode.upper() + "_BUDGET_EXCEEDED", "本轮达到共享模型调用或 Token 预算")
            await connection.execute(
                "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,"
                "input_rate,output_rate,currency,estimated_input_tokens,output_token_reserve,"
                "role_name,team_call_id,call_kind) "
                "SELECT %s,%s,%s,%s,%s,%s,p.input_per_million,p.output_per_million,p.currency,%s,%s,%s,%s,%s "
                "FROM (SELECT 1) a "
                "LEFT JOIN model_cost_rates p ON p.model_id=%s ON CONFLICT(call_id) DO NOTHING",
                (
                    run_id,
                    self.run,
                    self.owner,
                    self.mode,
                    model,
                    revision,
                    estimate,
                    reserve,
                    member.get("role_name", "lead"),
                    member.get("id"),
                    self.call_kind,
                    model,
                ),
            )
            if route:
                await connection.execute(
                    "UPDATE model_usage SET route_selection_id=%s WHERE call_id=%s", (route, run_id)
                )
            from app.money import reserve as reserve_money

            await reserve_money(connection, self.owner, run_id, model, estimate, reserve)
            from app.local_tokenizer import encoder

            encoded = encoder(configuration) if configuration else None
            method = encoded.name if encoded else "local-character-heuristic-v1"
            await connection.execute("UPDATE model_usage SET estimate_method=%s WHERE call_id=%s", (method, run_id))

    async def on_llm_new_token(self, token: str, *, run_id: UUID, chunk=None, **kwargs) -> None:
        tool_chunks = getattr(getattr(chunk, "message", None), "tool_call_chunks", None)
        if run_id in self.first_tokens or not (token or tool_chunks):
            return
        self.first_tokens.add(run_id)
        await self.database.execute(
            "UPDATE model_usage SET first_token_at=clock_timestamp() WHERE call_id=%s "
            "AND status='PENDING' AND first_token_at IS NULL",
            (run_id,),
        )

    async def on_llm_end(self, response, *, run_id: UUID, **kwargs) -> None:
        usage = reported_usage(response)
        if usage is None:
            await self.unknown(run_id)
            return
        incoming, outgoing = usage["input_tokens"], usage["output_tokens"]
        async with self.database.pool.connection() as connection:
            await self.finish_known(connection, run_id, usage, incoming, outgoing)
        await self.sync_work()

    async def finish_known(self, connection, run_id, usage, incoming, outgoing):
        changed = await (
            await connection.execute(
                "UPDATE model_usage SET status='KNOWN',input_tokens=%s,output_tokens=%s,finished_at=clock_timestamp(),"
                "cache_read_tokens=%s,cache_creation_tokens=%s,reasoning_tokens=%s,"
                "estimated_cost=(%s*input_rate+%s*output_rate)/%s WHERE call_id=%s "
                "AND status IN ('PENDING','UNKNOWN') RETURNING call_id",
                (
                    incoming,
                    outgoing,
                    usage["cache_read_tokens"],
                    usage["cache_creation_tokens"],
                    usage["reasoning_tokens"],
                    incoming,
                    outgoing,
                    Decimal(1000000),
                    run_id,
                ),
            )
        ).fetchone()
        if changed:
            from app.money import settle

            await settle(connection, run_id, usage)

    async def sync_work(self) -> None:
        if self.mode != "work":
            return
        rows = await self.database.fetch(
            "UPDATE work_queue_runs r SET model_calls=(SELECT count(*) FROM model_usage u WHERE u.run_id=r.id),"
            "used_tokens=(SELECT coalesce(sum(input_tokens+output_tokens),0) FROM model_usage u "
            "WHERE u.run_id=r.id AND u.status='KNOWN'),usage_unknown=r.usage_unknown OR EXISTS("
            "SELECT 1 FROM model_usage u WHERE u.run_id=r.id AND u.status='UNKNOWN') "
            "WHERE r.id=%s AND r.owner_id=%s AND r.resource_budgeted AND r.status='RUNNING' "
            "AND r.lease_token=%s AND r.lease_until>now() RETURNING r.used_tokens,r.token_limit",
            (self.run, self.owner, self.lease_token),
        )
        if rows and rows[0]["used_tokens"] > rows[0]["token_limit"]:
            fail(422, "WORK_BUDGET_EXCEEDED", "服务商报告的总用量超过任务预算，已停止后续操作")

    async def unknown(self, run_id: UUID, code: str = "USAGE_NOT_REPORTED") -> None:
        async with self.database.pool.connection() as connection:
            changed = await (
                await connection.execute(
                    "UPDATE model_usage SET status='UNKNOWN',finished_at=clock_timestamp(),failure_code=%s "
                    "WHERE call_id=%s AND status='PENDING' RETURNING call_id",
                    (code, run_id),
                )
            ).fetchone()
            if changed:
                from app.money import settle

                await settle(connection, run_id, None)
        await self.sync_work()

    async def on_llm_error(self, error, *, run_id: UUID, **kwargs) -> None:
        code = "MODEL_REQUEST_FAILED"
        if isinstance(error, asyncio.CancelledError):
            code = "MODEL_REQUEST_CANCELLED"
        elif isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
            code = "MODEL_REQUEST_TIMEOUT"
        await self.unknown(run_id, code)
