"""Worker completion, fencing and shutdown against real PostgreSQL."""

import asyncio
from uuid import uuid4

import pytest
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.work_worker import WorkWorker


def test_worker_commits_result_and_ordered_events(client):
    connection, app, run = prepare(client)

    async def execute(current):
        assert current["id"] == run["id"]
        return {"summary": "已验证"}

    worker = WorkWorker(app.state.db, execute)
    try:
        connection.portal.call(worker.perform, run)
        row = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (run["id"],))[0]
        assert row["status"] == "COMPLETED" and row["result"]["summary"] == "已验证"
        events = connection.portal.call(
            app.state.db.fetch,
            "SELECT * FROM work_run_events WHERE run_id=%s ORDER BY event_id",
            (run["id"],),
        )
        assert [(item["event_id"], item["event"]) for item in events] == [(1, "run.started"), (2, "run.completed")]
    finally:
        cleanup(connection, app, run)


def test_stale_worker_cannot_commit_or_replace_new_lease(client):
    connection, app, run = prepare(client)
    replacement = uuid4()

    async def execute(current):
        await app.state.db.execute("UPDATE work_queue_runs SET lease_token=%s WHERE id=%s", (replacement, run["id"]))
        return {"summary": "stale"}

    worker = WorkWorker(app.state.db, execute)
    try:
        connection.portal.call(worker.perform, run)
        row = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (run["id"],))[0]
        assert row["status"] == "RUNNING" and row["lease_token"] == replacement and row["result"] is None
    finally:
        cleanup(connection, app, run)


def test_cancellation_at_completion_boundary_does_not_report_success(client):
    connection, app, run = prepare(client)

    async def execute(current):
        await app.state.db.execute("UPDATE work_queue_runs SET cancel_requested=true WHERE id=%s", (run["id"],))
        return {"text": "已产生的结果仍可核查，但目标不标记完成"}

    try:
        connection.portal.call(WorkWorker(app.state.db, execute).perform, run)
        state = connection.portal.call(app.state.db.fetch,
                                      "SELECT status,result FROM work_queue_runs WHERE id=%s", (run["id"],))[0]
        assert state["status"] == "CANCELLED" and state["result"]["text"]
        events = connection.portal.call(app.state.db.fetch,
            "SELECT event FROM work_run_events WHERE run_id=%s ORDER BY event_id", (run["id"],))
        assert [item["event"] for item in events] == ["run.started", "run.cancelled"]
    finally:
        cleanup(connection, app, run)


def test_worker_shutdown_cancels_executor_and_holds_run_for_review(client):
    connection, app, run = prepare(client)

    async def exercise():
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def execute(current):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        worker = WorkWorker(app.state.db, execute)
        task = asyncio.create_task(worker.perform(run))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        row = (await app.state.db.fetch("SELECT * FROM work_queue_runs WHERE id=%s", (run["id"],)))[0]
        assert row["status"] == "NEEDS_REVIEW" and row["lease_token"] is None

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)
