"""Durable short workflow consumer; uncertain executions never replay automatically."""

import asyncio
import logging
from uuid import uuid4

from app.errors import fail
from app.workflow_compiler import WorkflowNodeFailed
from app.workflow_definition import Definition

logger = logging.getLogger(__name__)


async def reconcile_startup(database):
    """An API restart must not interrupt work still owned by an independent Ask worker."""
    await database.execute(
        "UPDATE workflow_runs r SET status=CASE WHEN EXISTS "
        "(SELECT 1 FROM external_workflow_jobs j WHERE j.run_id=r.id) OR snapshot ? 'parent_ask_run' "
        "OR snapshot ? 'parent_work_run' "
        "THEN 'NEEDS_REVIEW' ELSE 'FAILED' END,finished_at=now() WHERE status='RUNNING' "
        "AND NOT (snapshot ? 'parent_work_run' AND EXISTS("
        "SELECT 1 FROM work_queue_runs wr WHERE wr.id::text=r.snapshot->>'parent_work_run' "
        "AND (wr.status IN ('PENDING','PAUSED','WAITING_APPROVAL') OR (wr.status='RUNNING' "
        "AND wr.lease_until>now() AND NOT wr.cancel_requested)))) "
        "AND NOT (snapshot ? 'parent_ask_run' AND EXISTS("
        "SELECT 1 FROM task_runs a WHERE a.id::text=r.snapshot->>'parent_ask_run' "
        "AND a.status='RUNNING' AND a.lease_token IS NOT NULL AND a.lease_until>now() "
        "AND NOT a.cancel_requested))"
    )


class WorkflowQueue:
    def __init__(self, database, runtime) -> None:
        self.database, self.runtime = database, runtime

    async def claim(self) -> dict | None:
        async with self.database.pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT r.*,j.credential_id,k.owner_id,k.version_id,k.parent_work_run,"
                    "k.parent_lease_token FROM workflow_runs r "
                    "JOIN external_workflow_jobs j ON j.run_id=r.id JOIN api_credentials k ON k.id=j.credential_id "
                    "WHERE r.status='PENDING' ORDER BY r.created_at,r.id FOR UPDATE OF r SKIP LOCKED LIMIT 1",
                )
            ).fetchone()
            if not row:
                return None
            token = uuid4()
            await connection.execute("UPDATE workflow_runs SET status='RUNNING' WHERE id=%s", (row["id"],))
            await connection.execute(
                "UPDATE external_workflow_jobs SET lease_token=%s,lease_until=now()+interval '120 seconds' "
                "WHERE run_id=%s",
                (token, row["id"]),
            )
            return {**row, "lease_token": token}

    async def guard(self, run: dict) -> None:
        rows = await self.database.fetch(
            "SELECT 1 FROM external_workflow_jobs j JOIN workflow_runs r ON r.id=j.run_id "
            "JOIN api_credentials k ON k.id=j.credential_id JOIN workflow_versions v ON v.id=k.version_id "
            "WHERE j.run_id=%s AND j.lease_token=%s AND j.lease_until>now() AND r.status='RUNNING' "
            "AND (k.parent_lease_token IS NULL OR EXISTS(SELECT 1 FROM work_queue_runs wr "
            "JOIN agents a ON a.id=(wr.agent_snapshot->>'id')::uuid AND a.owner_id=wr.owner_id "
            "WHERE wr.id=k.parent_work_run AND wr.lease_token=k.parent_lease_token AND wr.status='RUNNING' "
            "AND wr.lease_until>now() AND NOT wr.cancel_requested AND a.enabled "
            "AND a.workflow_version_ids ? k.version_id::text)) "
            "AND k.revoked_at IS NULL AND k.expires_at>now() "
            "AND (k.organization_id IS NULL OR EXISTS(SELECT 1 FROM organization_members m "
            "JOIN organization_workflows s ON s.organization_id=m.organization_id AND s.version_id=k.version_id "
            "WHERE m.organization_id=k.organization_id AND m.user_id=k.caller_id AND m.role<>'viewer'))",
            (run["id"], run["lease_token"]),
        )
        if not rows:
            fail(409, "WORKFLOW_REVOKED", "调用授权、发布版本或执行租约已失效")
        if run.get("parent_work_run"):
            from app.ask_workflows import check_scope

            parent = await self.database.fetch(
                "SELECT agent_snapshot FROM work_queue_runs WHERE id=%s", (run["parent_work_run"],)
            )
            await check_scope(
                self.runtime.capabilities,
                run["owner_id"],
                parent[0]["agent_snapshot"],
                run["snapshot"]["resource_pins"],
            )

    async def finish(self, run: dict, status: str, output: str | None = None, node: str | None = None) -> None:
        await self.database.execute(
            "UPDATE workflow_runs r SET status=%s,output=%s,error_node=%s,finished_at=now() "
            "FROM external_workflow_jobs j WHERE r.id=j.run_id AND r.id=%s AND j.lease_token=%s AND r.status='RUNNING'",
            (status, output, node, run["id"], run["lease_token"]),
        )

        await self.database.execute(
            "UPDATE api_call_logs SET outcome=%s WHERE run_id=%s AND EXISTS "
            "(SELECT 1 FROM workflow_runs WHERE id=%s AND status=%s)",
            (status, run["id"], run["id"], status),
        )

    async def once(self) -> bool:
        run = await self.claim()
        if not run:
            return False
        try:
            await self.guard(run)
            snapshot = run["snapshot"]
            context = None
            if run["parent_work_run"]:
                parent = (
                    await self.database.fetch("SELECT * FROM work_queue_runs WHERE id=%s", (run["parent_work_run"],))
                )[0]
                await self.database.execute(
                    "UPDATE work_queue_runs SET resource_budgeted=true WHERE id=%s AND lease_token=%s "
                    "AND status='RUNNING' AND lease_until>now()",
                    (parent["id"], parent["lease_token"]),
                )
                context = {"mode": "work", "id": parent["id"], "lease_token": parent["lease_token"]}
            async with asyncio.timeout(90):
                output = await self.runtime.run(
                    Definition.model_validate(snapshot["definition"]),
                    run["owner_id"],
                    snapshot["model_id"],
                    snapshot["instructions"],
                    run["input"],
                    run["id"],
                    snapshot["resource_pins"],
                    guard=lambda: self.guard(run),
                    usage_context=context,
                )
            await self.guard(run)
            await self.finish(run, "SUCCEEDED", output)
        except asyncio.CancelledError:
            await self.finish(run, "NEEDS_REVIEW")
            raise
        except Exception as error:
            await self.finish(run, "FAILED", node=error.node_id if isinstance(error, WorkflowNodeFailed) else None)
        return True

    async def serve(self) -> None:
        while True:
            try:
                await self.database.execute(
                    "UPDATE workflow_runs w SET status='NEEDS_REVIEW',finished_at=now() "
                    "WHERE w.status='RUNNING' AND w.snapshot ? 'parent_ask_run' AND NOT EXISTS("
                    "SELECT 1 FROM task_runs r WHERE r.id::text=w.snapshot->>'parent_ask_run' "
                    "AND r.status='RUNNING' AND NOT r.cancel_requested "
                    "AND (r.lease_token IS NULL OR r.lease_until>now()))",
                )
                await self.database.execute(
                    "UPDATE workflow_runs r SET status='NEEDS_REVIEW',finished_at=now() "
                    "FROM external_workflow_jobs j WHERE r.id=j.run_id AND r.status='RUNNING' AND j.lease_until<=now()",
                )
                if await self.once():
                    continue
            except Exception as error:
                logger.error("Workflow consumer failed (%s)", type(error).__name__)
            await asyncio.sleep(0.5)
