"""Database-driven dispatch: no model execution or implicit retries in the scheduler."""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.automation_target import capacity, channel_pins, enqueue, target
from app.automation_types import BatchInput, ScheduleInput
from app.errors import APIError
from app.knowledge_ocr import collect as collect_ocr
from app.memory_jobs import collect as collect_memories
from app.notification_channels import dispatch
from app.notifications import collect
from app.schedule_clock import next_due

logger = logging.getLogger(__name__)


class AutomationScheduler:
    def __init__(self, state):
        self.state, self.db = state, state.db

    async def schedule(self, identifier, *, now=None):
        now = now or datetime.now(UTC)
        async with self.db.pool.connection() as connection:
            from app.maintenance import slot

            if not await slot(connection):
                return
            row = await (
                await connection.execute(
                    "SELECT * FROM automations WHERE id=%s AND enabled AND deleted_at IS NULL "
                    "AND next_due_at<=%s FOR UPDATE SKIP LOCKED",
                    (identifier, now),
                )
            ).fetchone()
            if not row:
                return
            due, occurrence = row["next_due_at"], uuid4()
            status, error = "DISPATCHED", None
            if (now - due).total_seconds() > 60 and row["missed_policy"] == "skip":
                status = "SKIPPED_MISSED"
            if status == "DISPATCHED":
                busy = await (
                    await connection.execute(
                        "SELECT 1 FROM automation_occurrences WHERE automation_id=%s "
                        "AND status='DISPATCHED' AND result_status IS NULL",
                        (identifier,),
                    )
                ).fetchone()
                available = await capacity(connection, row["owner_id"])
                if not available or (busy and row["overlap_policy"] == "skip"):
                    if row["overlap_policy"] == "queue":
                        return  # Keep at most one pending due time; do not accumulate a missed-time backlog.
                    status = "SKIPPED_BUSY"
            try:
                body = ScheduleInput(**{key: row[key] for key in ScheduleInput.model_fields if key in row})
                pins = []
                if status == "DISPATCHED":
                    await target(self.state, row["owner_id"], body)
                    pins = await channel_pins(self.db, row["owner_id"], row["notify_channel_ids"])
                await connection.execute(
                    "INSERT INTO automation_occurrences"
                    "(id,automation_id,owner_id,scheduled_for,title,status,notify_pins) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (occurrence, identifier, row["owner_id"], due, row["name"], status, Jsonb(pins)),
                )
                if status == "DISPATCHED":
                    async with connection.transaction():
                        run, destination = await enqueue(
                            connection,
                            row,
                            f"计划时刻：{due.isoformat()}；任务时区：{row['timezone']}。\n\n{row['goal']}",
                            occurrence=occurrence,
                        )
                        await connection.execute(
                            "UPDATE automation_occurrences SET ask_run_id=%s,work_run_id=%s,"
                            "root_run_id=%s,target_id=%s "
                            "WHERE id=%s",
                            (
                                run if row["mode"] == "ask" else None,
                                run if row["mode"] == "work" else None,
                                run,
                                destination,
                                occurrence,
                            ),
                        )
            except APIError as failure:
                status, error = "REJECTED", failure.code
                await connection.execute(
                    "INSERT INTO automation_occurrences"
                    "(id,automation_id,owner_id,scheduled_for,title,status,error_code,finished_at) "
                    "VALUES(%s,%s,%s,%s,%s,'REJECTED',%s,now()) ON CONFLICT(id) DO UPDATE "
                    "SET status='REJECTED',error_code=%s,finished_at=now()",
                    (occurrence, identifier, row["owner_id"], due, row["name"], error, error),
                )
            upcoming = next_due(
                row["kind"],
                now,
                start=row["starts_at"],
                timezone=row["timezone"],
                interval_seconds=row["interval_seconds"],
                cron=row["cron"],
            )
            await connection.execute(
                "UPDATE automations SET next_due_at=%s,enabled=%s,completed_at=%s,last_error=%s,updated_at=now() "
                "WHERE id=%s",
                (
                    upcoming,
                    upcoming is not None and status != "REJECTED",
                    now if upcoming is None else None,
                    error,
                    identifier,
                ),
            )
            if status == "REJECTED":
                await connection.execute("UPDATE automations SET paused_at=now() WHERE id=%s", (identifier,))

    async def batch(self, identifier):
        async with self.db.pool.connection() as connection:
            from app.maintenance import slot

            if not await slot(connection):
                return
            row = await (
                await connection.execute(
                    "SELECT * FROM agent_batches WHERE id=%s AND status='ACTIVE' AND deleted_at IS NULL "
                    "FOR UPDATE SKIP LOCKED",
                    (identifier,),
                )
            ).fetchone()
            if not row:
                return
            items = await (
                await connection.execute("SELECT * FROM batch_items WHERE batch_id=%s ORDER BY position", (identifier,))
            ).fetchall()
            problems = [
                i
                for i in items
                if not i["acknowledged"]
                and (
                    i["status"] == "UNKNOWN"
                    or (row["on_failure"] == "pause" and i["status"] in {"FAILED", "CANCELLED"})
                )
            ]
            if problems:
                await connection.execute(
                    "UPDATE agent_batches SET status='PAUSED',last_error=%s,notified=false WHERE id=%s",
                    (problems[0]["error_code"] or "ITEM_FAILED", identifier),
                )
                return
            waiting = [i for i in items if i["status"] == "WAITING"]
            running = sum(i["status"] == "RUNNING" for i in items)
            if not waiting and not running:
                await connection.execute(
                    "UPDATE agent_batches SET status='COMPLETED',updated_at=now() WHERE id=%s", (identifier,)
                )
                return
            if not waiting or running >= row["max_parallel"] or not await capacity(connection, row["owner_id"]):
                return
            try:
                body = BatchInput(
                    name=row["name"],
                    agent_version_id=row["agent_version_id"],
                    mode=row["mode"],
                    budget=row["item_budget"],
                    environment=row["environment"],
                    network_domains=row["network_domains"],
                    goals=[waiting[0]["goal"]],
                )
                await target(self.state, row["owner_id"], body)
                item = waiting[0]
                async with connection.transaction():
                    run, destination = await enqueue(connection, row, item["goal"], item=item["id"])
                    await connection.execute(
                        "UPDATE batch_items SET status='RUNNING',ask_run_id=%s,work_run_id=%s,"
                        "root_run_id=%s,target_id=%s "
                        "WHERE id=%s",
                        (
                            run if row["mode"] == "ask" else None,
                            run if row["mode"] == "work" else None,
                            run,
                            destination,
                            item["id"],
                        ),
                    )
            except APIError as error:
                await connection.execute(
                    "UPDATE agent_batches SET status='PAUSED',last_error=%s WHERE id=%s", (error.code, identifier)
                )

    async def tick(self):
        rows = await self.db.fetch(
            "SELECT id FROM automations WHERE enabled AND deleted_at IS NULL AND next_due_at<=now() "
            "ORDER BY next_due_at LIMIT 20"
        )
        for row in rows:
            await self.schedule(row["id"])
        rows = await self.db.fetch(
            "SELECT id FROM agent_batches WHERE status='ACTIVE' AND deleted_at IS NULL ORDER BY created_at LIMIT 40"
        )
        for row in rows:
            await self.batch(row["id"])
        await collect(self.db)
        await collect_memories(self.db)
        await collect_ocr(self.db)
        await dispatch(self.state)

    async def serve(self):
        while True:
            try:
                await self.tick()
            except Exception as error:
                logger.error("Automatic dispatch failed (%s)", type(error).__name__)
            await asyncio.sleep(1)
