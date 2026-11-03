"""Internal durable queue for future Work workers; no execution API is exposed."""

from uuid import UUID, uuid4

from app.storage import Database


class WorkQueue:
    """Claim runs with expiring, fenced leases; ambiguous runs require review."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def enqueue(self, task_id: UUID, owner_id: UUID, revision: int) -> UUID | None:
        """Snapshot a user's current task only if its revision still matches."""
        run_id = uuid4()
        rows = await self.database.fetch(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision) "
            "SELECT %s,id,owner_id,title,goal,revision FROM work_tasks "
            "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id",
            (run_id, task_id, owner_id, revision),
        )
        return run_id if rows else None

    async def claim(self) -> dict | None:
        """Atomically claim one pending run across all worker processes."""
        if fence := getattr(self.database, "service_fence", None):
            await fence.check()
        token = uuid4()
        rows = await self.database.admitted(
            "WITH candidate AS (SELECT r.id FROM work_queue_runs r WHERE r.status='PENDING' "
            "AND r.available_after<=now() "
            "AND pg_try_advisory_xact_lock(710038,hashtext(r.task_id::text)) "
            "AND NOT EXISTS (SELECT 1 FROM work_browser_controls c JOIN work_queue_runs cr ON cr.id=c.run_id "
            "WHERE cr.task_id=r.task_id AND c.status<>'CLOSED') "
            "AND NOT EXISTS (SELECT 1 FROM work_sandbox_sessions s WHERE s.kind=r.environment AND s.state<>'RELEASED' "
            "AND NOT(s.run_id=r.id AND r.environment='browser' AND s.browser_persistent AND s.state='READY')) "
            "ORDER BY CASE WHEN r.origin='interactive' THEN 0 ELSE 1 END,r.created_at,r.id "
            "FOR UPDATE OF r SKIP LOCKED LIMIT 1) "
            "UPDATE work_queue_runs r SET status='RUNNING',error_code=NULL,lease_token=%s, "
            "lease_until=now()+interval '60 seconds',updated_at=now() "
            "FROM candidate WHERE r.id=candidate.id RETURNING r.*",
            (token,),
        )
        return rows[0] if rows else None

    async def renew(self, run_id: UUID, token: UUID) -> bool:
        """Renew only a live lease; a stale worker cannot regain ownership."""
        rows = await self.database.fetch(
            "UPDATE work_queue_runs SET lease_until=now()+interval '60 seconds',updated_at=now() "
            "WHERE id=%s AND status='RUNNING' AND lease_token=%s AND lease_until>now() RETURNING id",
            (run_id, token),
        )
        return bool(rows)

    async def finish(self, run_id: UUID, token: UUID, *, success: bool) -> bool:
        """Finish only while the worker still holds its unexpired lease."""
        rows = await self.database.fetch(
            "UPDATE work_queue_runs SET status=%s,lease_token=NULL,lease_until=NULL,updated_at=now() "
            "WHERE id=%s AND status='RUNNING' AND lease_token=%s AND lease_until>now() RETURNING id",
            ("COMPLETED" if success else "FAILED", run_id, token),
        )
        return bool(rows)

    async def mark_expired_for_review(self) -> int:
        """Do not automatically repeat runs whose side effects are unknown."""
        rows = await self.database.fetch(
            "UPDATE work_queue_runs SET status='NEEDS_REVIEW',lease_token=NULL,lease_until=NULL,updated_at=now() "
            "WHERE status='RUNNING' AND lease_until<=now() RETURNING id"
        )
        for row in rows:
            await self.database.execute(
                "UPDATE operation_executions SET status='UNKNOWN' WHERE run_id=%s AND status='STARTED'", (row["id"],)
            )
        return len(rows)
