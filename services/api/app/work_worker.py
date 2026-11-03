"""Durable Work worker with fenced heartbeat and conservative interruption recovery."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.storage import Database
from app.work_queue import WorkQueue

logger = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    """A worker must stop when its lease can no longer be renewed."""


class WorkPaused(Exception):
    """A checkpoint and its approval have been durably saved."""


class CapacityUnavailable(Exception):
    """No sandbox has been created; the pending run can safely wait."""


class WorkWorker:
    """Consume durable runs independently of request/browser lifetimes."""

    def __init__(self, database: Database, execute: Callable[[dict], Awaitable[dict]]) -> None:
        self.database, self.execute = database, execute
        self.queue = WorkQueue(database)

    async def emit(self, run: dict, event: str, data: dict) -> None:
        """Serialize an event only while this worker owns the live run."""
        async with self.database.pool.connection() as connection:
            cursor = await connection.execute(
                "UPDATE work_queue_runs SET next_event_id=next_event_id+1 WHERE id=%s AND status='RUNNING' "
                "AND lease_token=%s AND lease_until>now() RETURNING next_event_id",
                (run["id"], run["lease_token"]),
            )
            row = await cursor.fetchone()
            if not row:
                raise LeaseLost("Work lease expired")
            await connection.execute(
                "INSERT INTO work_run_events(run_id,event_id,event,data) VALUES(%s,%s,%s,%s)",
                (run["id"], row["next_event_id"], event, Jsonb(data)),
            )

    async def heartbeat(self, run: dict) -> None:
        """A database outage or expired lease stops the executor conservatively."""
        while True:
            await asyncio.sleep(15)
            if not await self.queue.renew(run["id"], run["lease_token"]):
                states = await self.database.fetch(
                    "SELECT status,lease_token FROM work_queue_runs WHERE id=%s", (run["id"],)
                )
                if (
                    states
                    and states[0]["status"] in {"WAITING_APPROVAL", "PAUSED"}
                    and states[0]["lease_token"] is None
                ):
                    # Only transport cleanup remains after the engine's durable pause.
                    await asyncio.Future()
                raise LeaseLost("Work lease expired")
            rows = await self.database.fetch("SELECT cancel_requested FROM work_queue_runs WHERE id=%s", (run["id"],))
            if rows[0]["cancel_requested"]:
                raise RuntimeError("Cancellation requested")

    async def interrupt(self, run: dict, error_code: str) -> None:
        """Never requeue a run with potentially completed but unrecorded effects."""
        await self.database.execute(
            "UPDATE work_queue_runs SET status=CASE WHEN cancel_requested THEN 'CANCELLED' ELSE 'NEEDS_REVIEW' END,"
            "lease_token=NULL,lease_until=NULL,error_code=%s, "
            "updated_at=now() WHERE id=%s AND status='RUNNING' AND lease_token=%s",
            (error_code, run["id"], run["lease_token"]),
        )

        await self.database.execute(
            "UPDATE operation_executions SET status='UNKNOWN' WHERE run_id=%s AND lease_token=%s AND status='STARTED'",
            (run["id"], run["lease_token"]),
        )
        from app.workflow_tasks import reconcile

        await reconcile(self.database)

    async def perform(self, run: dict) -> None:
        """Race execution against lease loss, then cancel and await both branches."""
        await self.emit(run, "run.started", {})
        executor = asyncio.create_task(self.execute(run))
        heartbeat = asyncio.create_task(self.heartbeat(run))
        try:
            async with asyncio.timeout(1800):
                done, _ = await asyncio.wait({executor, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    heartbeat.result()
                    raise LeaseLost("Heartbeat stopped")
                result = executor.result()
                await self.complete(run, result)
        except WorkPaused:
            pass
        except CapacityUnavailable:
            await self.database.execute(
                "UPDATE work_queue_runs SET status='PENDING',lease_token=NULL,lease_until=NULL,"
                "available_after=now()+interval '15 seconds',error_code='WORK_CAPACITY_WAIT' "
                "WHERE id=%s AND lease_token=%s AND status='RUNNING'",
                (run["id"], run["lease_token"]),
            )
        except asyncio.CancelledError:
            executor.cancel()
            await asyncio.gather(executor, return_exceptions=True)
            await self.interrupt(run, "WORKER_STOPPED")
            raise
        except Exception as error:
            executor.cancel()
            await asyncio.gather(executor, return_exceptions=True)
            cause = error.__cause__ or error
            logger.error("Work execution interrupted: %s (%s, %s)", run["id"], type(cause).__name__,
                         getattr(cause, "code", "EXECUTION_UNCERTAIN"))
            from app.workflow_compiler import WorkflowNodeFailed
            from app.workflow_work import WorkflowStopped

            known = error.__cause__ if isinstance(error, WorkflowNodeFailed) else error
            if getattr(cause, "code", None) == "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED":
                await self.database.execute(
                    "UPDATE work_queue_runs SET status='PAUSED',error_code=%s,lease_token=NULL,lease_until=NULL,"
                    "updated_at=now() WHERE id=%s AND status='RUNNING' AND lease_token=%s",
                    (cause.code, run["id"], run["lease_token"]),
                )
            elif isinstance(known, WorkflowStopped):
                await self.finish(run, {"text": known.message}, "FAILED", error=known.code)
            else:
                await self.interrupt(run, getattr(cause, "code", "EXECUTION_UNCERTAIN"))
        finally:
            executor.cancel()
            heartbeat.cancel()
            await asyncio.gather(executor, heartbeat, return_exceptions=True)
            terminal = await self.database.fetch(
                "SELECT 1 FROM work_queue_runs WHERE id=%s AND status IN ('COMPLETED','FAILED','CANCELLED')",
                (run["id"],),
            )
            if terminal:
                from app.resource_runtime import release_credentials

                release_credentials(run["owner_id"], "work", run["id"])

    async def complete(self, run: dict, result: dict) -> None:
        """Commit completion and its final event together under a live lease."""
        await self.finish(run, result, "COMPLETED")

    async def finish(self, run: dict, result: dict, status: str, *, error: str | None = None) -> None:
        from app.workflow_tasks import sync

        async with self.database.pool.connection() as connection:
            cursor = await connection.execute(
                "UPDATE work_queue_runs SET status=CASE WHEN cancel_requested THEN 'CANCELLED' ELSE %s END,"
                "result=%s,error_code=%s,lease_token=NULL,lease_until=NULL, "
                "next_event_id=next_event_id+1,updated_at=now() WHERE id=%s AND status='RUNNING' "
                "AND lease_token=%s AND lease_until>now() RETURNING next_event_id,status",
                (status, Jsonb(result), error, run["id"], run["lease_token"]),
            )
            row = await cursor.fetchone()
            if not row:
                raise LeaseLost("Completion rejected for stale worker")
            await connection.execute(
                "INSERT INTO work_run_events(run_id,event_id,event,data) VALUES(%s,%s,%s,%s)",
                (run["id"], row["next_event_id"],
                 {"COMPLETED": "run.completed", "FAILED": "run.failed", "CANCELLED": "run.cancelled"}[row["status"]],
                 Jsonb({"error_code": error} if error else {})),
            )
            await sync(
                connection, run["id"], "SUCCEEDED" if row["status"] == "COMPLETED" else row["status"], result=result
            )

    async def tick(self) -> bool:
        """Claim at most one run; expired runs are held for review rather than replayed."""
        await self.queue.mark_expired_for_review()
        from app.workflow_tasks import reconcile

        await reconcile(self.database)
        run = await self.queue.claim()
        if run is None:
            return False
        await self.perform(run)
        return True

    async def register(self, identifier) -> None:
        while True:
            await self.database.execute(
                "INSERT INTO work_worker_instances(id) VALUES(%s) ON CONFLICT(id) DO UPDATE SET heartbeat_at=now()",
                (identifier,),
            )
            await asyncio.sleep(10)

    async def serve(self, stop: asyncio.Event) -> None:
        """Keep an independent worker heartbeat while processing durable runs."""
        identifier = uuid4()
        registration = asyncio.create_task(self.register(identifier))
        try:
            while not stop.is_set():
                if registration.done():
                    registration.result()
                await self.tick()
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except TimeoutError:
                    pass
        finally:
            registration.cancel()
            await asyncio.gather(registration, return_exceptions=True)
            await self.database.execute("DELETE FROM work_worker_instances WHERE id=%s", (identifier,))
