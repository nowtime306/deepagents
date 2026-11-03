"""Persistent bounded retrieval experiments; uncertain calls are never replayed."""

import asyncio
import logging
import time
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.errors import APIError, fail
from app.evaluation_metrics import question_result, summary
from app.knowledge_pins import guard as guard_pins
from app.permissions import require_account
from app.resource_runtime import resource_run
from app.resource_usage import operation

logger = logging.getLogger(__name__)


class EvaluationWorker:
    def __init__(self, database, knowledge):
        self.db, self.knowledge = database, knowledge

    async def reconcile(self):
        async with self.db.pool.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT id,operation_id,results,dataset_snapshot FROM knowledge_experiments "
                    "WHERE status='RUNNING' AND lease_until<=now() FOR UPDATE SKIP LOCKED LIMIT 20"
                )
            ).fetchall()
            for row in rows:
                await connection.execute(
                    "UPDATE resource_operations SET status='UNKNOWN',error_code='EVALUATION_INTERRUPTED',"
                    "finished_at=now(),cancel_requested=true,lease_until=now() WHERE id=%s AND status='RUNNING'",
                    (row["operation_id"],),
                )
                await connection.execute(
                    "UPDATE knowledge_experiments SET status='UNKNOWN',error_code='EVALUATION_INTERRUPTED',"
                    "finished_at=now(),metrics=%s WHERE id=%s",
                    (Jsonb(summary(row["results"], len(row["dataset_snapshot"]["questions"]))), row["id"]),
                )

    async def claim(self):
        async with self.db.pool.connection() as connection:
            from app.maintenance import slot

            if not await slot(connection):
                return None
            row = await (
                await connection.execute(
                    "SELECT * FROM knowledge_experiments WHERE status='PENDING' AND NOT cancel_requested "
                    "ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            ).fetchone()
            if not row:
                return None
            token = uuid4()
            await connection.execute(
                "UPDATE knowledge_experiments SET status='RUNNING',lease_token=%s,"
                "lease_until=now()+interval '90 seconds' "
                "WHERE id=%s",
                (token, row["id"]),
            )
            return {**row, "lease_token": token}

    async def guard(self, run, context=None):
        rows = await self.db.fetch(
            "UPDATE knowledge_experiments SET lease_until=now()+interval '90 seconds' WHERE id=%s AND lease_token=%s "
            "AND lease_until>now() AND status='RUNNING' AND NOT cancel_requested RETURNING id",
            (run["id"], run["lease_token"]),
        )
        if not rows:
            fail(409, "EVALUATION_STOPPED", "实验已取消、停止或租约失效")
        for permission in ("knowledge.manage", "knowledge.search"):
            await require_account(self.db, run["owner_id"], permission)
        knowledge = await self.knowledge.owned(run["knowledge_id"], run["owner_id"])
        await guard_pins(self.db, knowledge, run["resource_snapshot"])
        if context:
            updated = await self.db.fetch(
                "UPDATE resource_operations SET lease_until=now()+interval '90 seconds' WHERE id=%s AND lease_token=%s "
                "AND status='RUNNING' AND lease_until>now() AND NOT cancel_requested RETURNING id",
                (context["id"], context["lease_token"]),
            )
            if not updated:
                fail(409, "RESOURCE_LEASE_LOST", "实验的资源预算已停止或失效")
        return knowledge

    async def finish(self, run, status, results, context=None, error=None):
        async with self.db.pool.connection() as connection:
            active = await (
                await connection.execute(
                    "SELECT cancel_requested FROM knowledge_experiments WHERE id=%s AND lease_token=%s "
                    "AND status='RUNNING' AND lease_until>now() FOR UPDATE",
                    (run["id"], run["lease_token"]),
                )
            ).fetchone()
            if not active:
                return
            if active["cancel_requested"]:
                status, error = "CANCELLED", "EVALUATION_CANCELLED"
            if context:
                outcome = "SUCCEEDED" if status == "SUCCEEDED" else "UNKNOWN" if status == "UNKNOWN" else "FAILED"
                closed = await (
                    await connection.execute(
                        "UPDATE resource_operations SET status=%s,error_code=%s,finished_at=now() WHERE id=%s "
                        "AND lease_token=%s AND status='RUNNING' AND lease_until>now() RETURNING id",
                        (outcome, error, context["id"], context["lease_token"]),
                    )
                ).fetchone()
                if not closed:
                    status, error = "UNKNOWN", "RESOURCE_LEASE_LOST"
            await connection.execute(
                "UPDATE knowledge_experiments SET status=%s,results=%s,metrics=%s,error_code=%s,finished_at=now() "
                "WHERE id=%s",
                (
                    status,
                    Jsonb(results),
                    Jsonb(summary(results, len(run["dataset_snapshot"]["questions"]))),
                    error,
                    run["id"],
                ),
            )

    async def perform(self, run):
        async with resource_run(self.db, run["owner_id"], "resource", run["id"]):
            return await self._perform(run)

    async def _perform(self, run):
        context, results = None, []
        try:
            async with asyncio.timeout(900):
                knowledge = await self.guard(run)
                model_id = knowledge["embedding_model_id"] or knowledge["rerank_model_id"]
                model = await self.knowledge.registry.get(model_id) if model_id else None
                context = await operation(
                    self.db, run["owner_id"], model, "knowledge", "query", resource=run["knowledge_id"]
                )
                context.update({"expected_pins": run["resource_snapshot"], "require_manage": True})
                calls = min(100, 2 * len(run["dataset_snapshot"]["questions"]))
                await self.db.execute(
                    "UPDATE resource_operations SET model_call_limit=%s,token_limit=512000 WHERE id=%s",
                    (calls, context["id"]),
                )
                await self.db.execute(
                    "UPDATE knowledge_experiments SET operation_id=%s "
                    "WHERE id=%s AND lease_token=%s AND status='RUNNING'",
                    (context["id"], run["id"], run["lease_token"]),
                )
                for question in run["dataset_snapshot"]["questions"]:
                    await self.guard(run, context)
                    started = time.perf_counter()
                    found = await self.knowledge.search(
                        run["knowledge_id"], run["owner_id"], question["question"], 10, usage_context=context
                    )
                    await self.guard(run, context)
                    uncertain = await self.db.fetch(
                        "SELECT 1 FROM model_usage WHERE run_id=%s AND status IN ('PENDING','UNKNOWN') LIMIT 1",
                        (context["id"],),
                    )
                    if uncertain:
                        fail(502, "EVALUATION_USAGE_UNKNOWN", "有资源请求用量或结果未知，已停止实验，未自动重试")
                    if found.get("warning"):
                        fail(502, "EVALUATION_RETRIEVAL_FAILED", "检索发生降级或异常，已停止，不能当作固定条件实验")
                    results.append(question_result(question, found, (time.perf_counter() - started) * 1000))
                    await self.db.execute(
                        "UPDATE knowledge_experiments SET results=%s WHERE id=%s AND lease_token=%s "
                        "AND lease_until>now() AND status='RUNNING' AND NOT cancel_requested",
                        (Jsonb(results), run["id"], run["lease_token"]),
                    )
                await self.finish(run, "SUCCEEDED", results, context)
        except asyncio.CancelledError:
            await self.finish(run, "UNKNOWN", results, context, "EVALUATION_INTERRUPTED")
            raise
        except Exception as error:
            rows = await self.db.fetch("SELECT cancel_requested FROM knowledge_experiments WHERE id=%s", (run["id"],))
            unknown = context and await self.db.fetch(
                "SELECT 1 FROM model_usage WHERE run_id=%s AND status IN ('PENDING','UNKNOWN') LIMIT 1",
                (context["id"],),
            )
            status = (
                "CANCELLED"
                if rows and rows[0]["cancel_requested"]
                else "UNKNOWN"
                if unknown or isinstance(error, TimeoutError)
                else "FAILED"
            )
            code = (
                error.code
                if isinstance(error, APIError)
                else "EVALUATION_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "EVALUATION_FAILED"
            )
            await self.finish(run, status, results, context, code)

    async def tick(self):
        await self.reconcile()
        run = await self.claim()
        if run:
            await self.perform(run)
        return bool(run)

    async def serve(self):
        while True:
            try:
                active = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("evaluation consumer temporarily unavailable")
                active = False
            if not active:
                await asyncio.sleep(2)
