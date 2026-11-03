"""Meter non-chat model work with the existing ledger and parent-run budgets."""

import asyncio
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.automation_auth import authorize
from app.errors import fail
from app.usage import UsageRecorder
from app.usage_details import count


async def operation(
    database, owner, model, kind: str, action: str, *, resource=None, source=None, revision=None
) -> dict:
    from app.resource_runtime import active_resources

    active = active_resources.get()
    identifier = active.run if active and active.mode == "resource" else uuid4()
    token = uuid4()
    await database.execute(
        "UPDATE resource_operations SET status='UNKNOWN',error_code='RESOURCE_RESULT_UNKNOWN',finished_at=now() "
        "WHERE owner_id=%s AND status='RUNNING' AND lease_until<=now()",
        (owner,),
    )
    async with database.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710028,hashtext(%s))", (str(owner),))
        current = await (
            await connection.execute(
                "SELECT 1 FROM resource_operations WHERE owner_id=%s AND status='RUNNING' AND lease_until>now()",
                (owner,),
            )
        ).fetchone()
        if current:
            fail(409, "RESOURCE_BUSY", "此账号已有资源处理操作，请等待完成或失效核对")
        await connection.execute(
            "INSERT INTO resource_operations"
            "(id,owner_id,resource_kind,action,model_id,model_revision,lease_token,lease_until,"
            "resource_id,source_id,source_revision,model_call_limit,token_limit) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,now()+interval '90 seconds',%s,%s,%s,%s,%s)",
            (
                identifier,
                owner,
                kind,
                action,
                model.id if model else None,
                model.revision if model else None,
                token,
                resource,
                source,
                revision,
                16 if kind == "knowledge" and action == "index" else 2,
                768000 if kind == "knowledge" and action == "index" else 64000,
            ),
        )
    return {"id": identifier, "lease_token": token, "mode": "resource"}


async def finish_operation(database, context: dict, status: str, error: str | None = None) -> bool:
    rows = await database.fetch(
        "UPDATE resource_operations SET status=%s,error_code=%s,finished_at=now() WHERE id=%s AND lease_token=%s "
        "AND status='RUNNING' AND lease_until>now() RETURNING id",
        (status, error, context["id"], context["lease_token"]),
    )
    return bool(rows)


async def link_model_snapshot(connection, call, owner, run, model):
    """Associate manually metered media calls with their actually resolved configuration."""
    await connection.execute(
        "WITH actual AS (UPDATE run_resource_snapshots SET used_at=coalesce(used_at,now()) "
        "WHERE owner_id=%s AND mode='resource' AND run_id=%s AND resource_kind='model' AND resource_id=%s "
        "RETURNING snapshot_id,phase) UPDATE model_usage SET resource_snapshot_id=actual.snapshot_id,"
        "resource_phase=actual.phase FROM actual WHERE call_id=%s",
        (owner, run, model, call),
    )


class EmbeddingMeter:
    def __init__(self, database, owner: UUID, model, context: dict, *, call_kind="embedding", knowledge=None):
        self.db, self.owner, self.model, self.context = database, owner, model, context
        self.kind, self.knowledge = call_kind, knowledge

    async def start(self, texts: list[str]) -> tuple:
        await parent_authority(self.db, self.owner, self.context)
        if self.knowledge:
            from app.knowledge_usage import authority

            await authority(self.db, self.owner, self.knowledge, self.context, self.model, self.kind)
        if self.context["mode"] == "resource":
            await self.db.execute(
                "UPDATE resource_operations SET lease_until=now()+interval '90 seconds' WHERE id=%s "
                "AND lease_token=%s AND status='RUNNING' AND lease_until>now() AND NOT cancel_requested",
                (self.context["id"], self.context["lease_token"]),
            )
        if self.context["mode"] == "work":
            await self.db.execute(
                "UPDATE work_queue_runs SET resource_budgeted=true WHERE id=%s AND owner_id=%s AND lease_token=%s "
                "AND lease_until>now() AND status='RUNNING'",
                (self.context["id"], self.owner, self.context["lease_token"]),
            )
        recorder = UsageRecorder(
            self.db,
            self.owner,
            self.context["id"],
            self.model.id,
            self.context["mode"],
            revision=self.model.revision,
            reserve=128 if self.kind == "rerank" else 0,
            lease_token=self.context["lease_token"],
            call_kind=self.kind,
            team_budget=self.context["mode"] == "work",
            input_estimate=sum(len(text.encode()) for text in texts) + 128,
            authority=self.context.get("guard"),
        )
        identifier = uuid4()
        await recorder.on_chat_model_start({}, [], run_id=identifier)
        return identifier, recorder

    async def finish(self, receipt: tuple, usage) -> None:
        identifier, recorder = receipt
        incoming = count(usage.get("prompt_tokens")) if isinstance(usage, dict) else None
        total = count(usage.get("total_tokens")) if isinstance(usage, dict) else None
        outgoing = 0 if total == incoming and incoming is not None else None
        if self.kind == "rerank" and isinstance(usage, dict):
            stats = (usage.get("meta") or {}).get("tokens") or usage.get("usage") or {}
            incoming = count(stats.get("input_tokens", stats.get("prompt_tokens")))
            outgoing = count(stats.get("output_tokens", stats.get("completion_tokens")))
            total = incoming + outgoing if incoming is not None and outgoing is not None else None
        if incoming is None or outgoing is None or total is None:
            await recorder.unknown(identifier)
            if self.context.get("expected_pins"):
                fail(502, "EVALUATION_USAGE_UNKNOWN", "资源模型未报告有效用量，已停止实验；未自动重试")
            if self.context["mode"] == "work":
                fail(502, "WORK_USAGE_UNAVAILABLE", "资源模型未报告有效用量，已停止任务；请核对消耗")
            return
        response = LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            usage_metadata={"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": total},
                        )
                    )
                ]
            ]
        )
        await recorder.on_llm_end(response, run_id=identifier)

    async def error(self, receipt: tuple, error: BaseException) -> None:
        identifier, recorder = receipt
        code = (
            "EMBEDDING_REQUEST_CANCELLED" if isinstance(error, asyncio.CancelledError) else "EMBEDDING_RESULT_UNKNOWN"
        )
        await recorder.unknown(identifier, code)


async def parent_authority(database, owner, context):
    if context.get("guard"):
        await context["guard"]()
    if context["mode"] == "workflow":
        rows = await database.fetch(
            "SELECT 1 FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
            "WHERE r.id=%s AND w.owner_id=%s AND r.status='RUNNING'",
            (context["id"], owner),
        )
        if not rows:
            fail(409, "WORKFLOW_STOPPED", "所属工作流已停止")
        await authorize(database, owner, context["id"], "workflow")
        return
    if context["mode"] not in {"ask", "work"}:
        return
    if context["mode"] == "ask":
        sql = "SELECT 1 FROM task_runs r JOIN conversations c ON c.id=r.conversation_id WHERE c.owner_id=%s "
    else:
        sql = "SELECT 1 FROM work_queue_runs r WHERE r.owner_id=%s "
    rows = await database.fetch(
        sql + "AND r.id=%s AND r.status='RUNNING' AND NOT r.cancel_requested "
        "AND r.lease_token IS NOT DISTINCT FROM %s AND (r.lease_token IS NULL OR r.lease_until>now())",
        (owner, context["id"], context["lease_token"]),
    )
    if not rows:
        fail(409, "RESOURCE_PARENT_STOPPED", "所属运行已停止、取消或租约失效")
    await authorize(database, owner, context["id"], context["mode"])
