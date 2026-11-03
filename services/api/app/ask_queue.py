"""Persistent Ask admission with fenced leases and no replay of ambiguous requests."""

import hashlib
from uuid import UUID, uuid4

from app.storage import Database


def conversation_lock(identifier: UUID) -> int:
    """Derive a signed advisory key; collisions only serialize unrelated conversations."""
    return int.from_bytes(hashlib.sha256(identifier.bytes).digest()[:4], "big", signed=True)


class AskQueue:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def candidates(self) -> list[dict]:
        return await self.database.fetch(
            "SELECT id,conversation_id FROM task_runs WHERE status='PENDING' AND NOT cancel_requested "
            "ORDER BY CASE WHEN origin='interactive' THEN 0 ELSE 1 END,created_at,id LIMIT 32"
        )

    async def claim(self, identifier: UUID, worker: UUID) -> dict | None:
        """Caller must retain the conversation advisory lock on its checkpoint connection."""
        if fence := getattr(self.database, "service_fence", None):
            await fence.check()
        rows = await self.database.admitted(
            "UPDATE task_runs SET status='RUNNING',worker_id=%s,lease_token=%s,"
            "lease_until=now()+interval '60 seconds',claimed_at=now() "
            "WHERE id=%s AND status='PENDING' AND NOT cancel_requested RETURNING *",
            (worker, uuid4(), identifier),
        )
        return rows[0] if rows else None

    async def renew(self, identifier: UUID, token: UUID) -> bool:
        rows = await self.database.fetch(
            "UPDATE task_runs SET lease_until=now()+interval '60 seconds' "
            "WHERE id=%s AND lease_token=%s AND lease_until>now() AND status='RUNNING' RETURNING id",
            (identifier, token),
        )
        return bool(rows)

    async def reconcile(self) -> int:
        """Atomically finish cancelled queued work and fail expired work without replay."""
        rows = await self.database.fetch(
            "WITH ended AS (UPDATE task_runs SET status=CASE WHEN status='PENDING' THEN 'CANCELLED' ELSE 'FAILED' END,"
            "error_code=CASE WHEN status='PENDING' THEN NULL ELSE 'ASK_RESULT_UNKNOWN' END,"
            "finished_at=now(),lease_token=NULL,lease_until=NULL,next_event_id=next_event_id+1 "
            "WHERE (status='PENDING' AND cancel_requested) OR "
            "(status='RUNNING' AND lease_token IS NOT NULL AND lease_until<=now()) "
            "RETURNING id,status,error_code,next_event_id) "
            "INSERT INTO run_events(run_id,event_id,event,data) SELECT id,next_event_id,"
            "CASE WHEN status='CANCELLED' THEN 'run.cancelled' ELSE 'run.failed' END,"
            "jsonb_build_object('message',CASE WHEN status='CANCELLED' THEN '排队请求已取消' "
            "ELSE '执行中断，结果未知。不会自动重放；请核对后发起新一轮提问。' END) FROM ended RETURNING run_id"
        )
        return len(rows)
