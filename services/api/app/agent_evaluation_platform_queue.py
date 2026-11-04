"""Durable fenced queue primitives. No consumer or completion shortcut is started by this module."""

from uuid import UUID, uuid4

from app.permissions import require_account


def permissions(definition: dict) -> set[str]:
    needed = {"evaluation.run"}
    for case in definition["cases"]:
        needed |= {
            "ask": {"chat.read", "chat.run", "agents.manage"},
            "work": {"work.read", "work.manage", "work.execute"},
            "workflow": {"workflows.read", "workflows.manage", "workflows.execute"},
        }[case["mode"]]
        if case["fixture_profile"]["id"] in {"grounded-warranty", "unanswerable-warranty"}:
            needed |= {"knowledge.read", "knowledge.manage", "knowledge.search"}
    return needed


async def authorize(database, owner, definition):
    for permission in sorted(permissions(definition)):
        await require_account(database, owner, permission)


class Queue:
    """A dispatcher must still verify publication, source, effects and reports; leasing is not execution."""

    def __init__(self, database):
        self.db = database

    async def claim(self, worker: UUID):
        async with self.db.pool.connection() as conn:
            # Global single active evaluation target until a reviewed capacity controller exists.
            await conn.execute("SELECT pg_advisory_xact_lock(710051)")
            active = await (
                await conn.execute(
                    "SELECT 1 FROM agent_evaluation_runs WHERE status IN "
                    "('RUNNING','CANCEL_REQUESTED') OR cleanup_status='RETAINED' LIMIT 1"
                )
            ).fetchone()
            if active:
                return None
            row = await (
                await conn.execute(
                    "SELECT * FROM agent_evaluation_runs WHERE status='QUEUED' AND NOT "
                    "cancel_requested ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
                )
            ).fetchone()
            if not row:
                return None
            token = uuid4()
            return await (
                await conn.execute(
                    (
                        "UPDATE agent_evaluation_runs SET "
                        "status='RUNNING',worker_id=%s,lease_token=%s,lease_until=now()+interval '60 "
                        "seconds',started_at=coalesce(started_at,now()),revision=revision+1 WHERE "
                        "id=%s AND owner_id=%s AND status='QUEUED' RETURNING *"
                    ),
                    (worker, token, row["id"], row["owner_id"]),
                )
            ).fetchone()

    async def renew(self, run, owner, token):
        rows = await self.db.fetch(
            (
                "SELECT snapshot FROM agent_evaluation_runs WHERE id=%s AND owner_id=%s AND "
                "lease_token=%s AND lease_until>now() AND status='RUNNING' AND NOT "
                "cancel_requested"
            ),
            (run, owner, token),
        )
        if not rows:
            return False
        await authorize(self.db, owner, rows[0]["snapshot"]["definition"])
        renewed = await self.db.fetch(
            (
                "UPDATE agent_evaluation_runs SET lease_until=now()+interval '60 seconds' "
                "WHERE id=%s AND owner_id=%s AND lease_token=%s AND lease_until>now() AND "
                "status='RUNNING' AND NOT cancel_requested RETURNING id"
            ),
            (run, owner, token),
        )
        return bool(renewed)

    async def begin_dispatch(self, run, owner, token):
        if not await self.renew(run, owner, token):
            return False
        rows = await self.db.fetch(
            (
                "UPDATE agent_evaluation_runs SET dispatch_started=true WHERE id=%s AND "
                "owner_id=%s AND lease_token=%s AND lease_until>now() AND status='RUNNING' AND "
                "NOT cancel_requested RETURNING id"
            ),
            (run, owner, token),
        )
        return bool(rows)

    async def expire(self):
        # Retain dispatched/unknown effects and their capacity; only confirmed pre-dispatch work can requeue.
        async with self.db.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT * FROM agent_evaluation_runs WHERE status IN "
                    "('RUNNING','CANCEL_REQUESTED') AND lease_until<=now() FOR UPDATE SKIP LOCKED"
                )
            ).fetchall()
            for row in rows:
                retained = row["dispatch_started"]
                cancelled = row["cancel_requested"] and not retained
                status = "NEEDS_REVIEW" if retained else "CANCELLED" if cancelled else "QUEUED"
                gate = "NEEDS_REVIEW" if retained or cancelled else row["gate_verdict"]
                await conn.execute(
                    (
                        "UPDATE agent_evaluation_runs SET "
                        "status=%s,gate_verdict=%s,cleanup_status=%s,lease_token=NULL,leas"
                        "e_until=NULL,worker_id=NULL,revision=revision+1,finished_at=CASE "
                        "WHEN %s THEN now() ELSE finished_at END,completed_samples=CASE WHEN %s "
                        "THEN total_samples ELSE completed_samples END WHERE id=%s AND owner_id="
                        "%s"
                    ),
                    (
                        status,
                        gate,
                        "RETAINED" if retained else row["cleanup_status"],
                        cancelled,
                        cancelled,
                        row["id"],
                        row["owner_id"],
                    ),
                )
                if cancelled:
                    await conn.execute(
                        (
                            "UPDATE agent_evaluation_samples SET "
                            "status='CANCELLED',gate_verdict='NEEDS_REVIEW',finished_at=now() WHERE "
                            "run_id=%s AND owner_id=%s AND status='PENDING'"
                        ),
                        (row["id"], row["owner_id"]),
                    )
                await conn.execute(
                    "INSERT INTO agent_evaluation_events(owner_id,run_id,event) VALUES(%s,%s,'lease.expired')",
                    (row["owner_id"], row["id"]),
                )
