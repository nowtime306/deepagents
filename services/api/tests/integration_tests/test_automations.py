"""Durable dispatch and shared budgets using isolated PostgreSQL and local models."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_api import ORIGIN, TestModel
from test_api import client as client

from app.ask_worker import AskWorker
from app.automation_scheduler import AutomationScheduler
from app.errors import APIError
from app.usage import UsageRecorder
from tests.unit_tests.test_harness import ToolModel


def version(connection):
    agent = connection.post(
        "/api/v1/agents", json={"name": "自动执行测试-" + uuid4().hex[:8], "model_id": "test"}, headers=ORIGIN
    )
    assert agent.status_code == 201, agent.text
    response = connection.post(f"/api/v1/agents/{agent.json()['id']}/publish", json={"revision": 1}, headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def schedule(connection, **changes):
    payload = {
        "name": "每日检查",
        "agent_version_id": version(connection),
        "goal": "只读测试目标",
        "starts_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        "missed_policy": "once",
        **changes,
    }
    response = connection.post("/api/v1/automations", json=payload, headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def batch(connection, **changes):
    response = connection.post(
        "/api/v1/batches",
        json={
            "name": "隔离批次",
            "agent_version_id": version(connection),
            "goals": ["第一项目标", "第二项目标", "第三项目标"],
            **changes,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_schedule_competing_dispatch_and_worker_execute(client):
    connection, app = client
    identifier = schedule(connection)
    available = connection.get("/api/v1/automations/targets")
    assert available.status_code == 200 and available.json()["items"]
    scheduler = AutomationScheduler(app.state)
    connection.portal.call(scheduler.tick)
    assert not connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"]
    assert (
        connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN).status_code
        == 200
    )

    async def competing():
        await asyncio.gather(scheduler.schedule(UUID(identifier)), scheduler.schedule(UUID(identifier)))

    connection.portal.call(competing)
    rows = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"]
    assert len(rows) == 1 and rows[0]["status"] == "DISPATCHED"
    worker = AskWorker(
        app.state.db, app.state.models, app.state.settings, lambda _: TestModel(responses=["自动任务完成"])
    )
    assert connection.portal.call(worker.tick)
    done = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]
    assert done["result_status"] == "SUCCEEDED"
    assert (
        connection.get(f"/api/v1/conversations/{done['target_id']}/messages").json()["items"][-1]["content"]["data"][
            "content"
        ]
        == "自动任务完成"
    )
    assert (
        connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN).status_code
        == 409
    )


def test_missed_policy_capacity_and_pause(client):
    connection, app = client
    identifier = schedule(
        connection, starts_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(), missed_policy="skip"
    )
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN)
    scheduler = AutomationScheduler(app.state)
    connection.portal.call(scheduler.schedule, UUID(identifier))
    assert (
        connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]["status"] == "SKIPPED_MISSED"
    )
    jobs = [schedule(connection) for _ in range(3)]
    for job in jobs:
        connection.put(f"/api/v1/automations/{job}/state", json={"enabled": True}, headers=ORIGIN)
        connection.portal.call(scheduler.schedule, UUID(job))
    statuses = [connection.get(f"/api/v1/automations/{job}/occurrences").json()["items"][0] for job in jobs]
    assert [row["status"] for row in statuses] == ["DISPATCHED", "DISPATCHED", "SKIPPED_BUSY"]
    connection.put(f"/api/v1/automations/{jobs[0]}/state", json={"enabled": False}, headers=ORIGIN)
    from app.automation_auth import authorize

    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    with pytest.raises(APIError, match="主动任务来源"):
        connection.portal.call(authorize, app.state.db, owner, UUID(statuses[0]["ask_run_id"]), "ask")


def test_batch_parallel_shared_budget_and_unknown_pause(client):
    connection, app = client
    identifier = batch(connection, model_call_limit=1)
    scheduler = AutomationScheduler(app.state)
    for _ in range(3):
        connection.portal.call(scheduler.batch, UUID(identifier))
    rows = connection.portal.call(
        app.state.db.fetch, "SELECT * FROM batch_items WHERE batch_id=%s ORDER BY position", (identifier,)
    )
    assert [r["status"] for r in rows] == ["RUNNING", "RUNNING", "WAITING"]
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])

    async def reserve():
        recorders = [
            UsageRecorder(app.state.db, owner, row["root_run_id"], "test", "ask", reserve=1) for row in rows[:2]
        ]
        return await asyncio.gather(
            *[r.on_chat_model_start({}, [[HumanMessage(content="预算")]], run_id=uuid4()) for r in recorders],
            return_exceptions=True,
        )

    results = connection.portal.call(reserve)
    assert sum(r is None for r in results) == 1
    assert [r.code for r in results if isinstance(r, APIError)] == ["BATCH_BUDGET_EXCEEDED"]
    # An expired request is classified UNKNOWN by a trigger in the same terminal-state transaction.
    connection.portal.call(
        app.state.db.execute,
        "UPDATE task_runs SET status='FAILED',error_code='ASK_RESULT_UNKNOWN' WHERE id=%s",
        (rows[0]["root_run_id"],),
    )
    connection.portal.call(scheduler.batch, UUID(identifier))
    status = connection.get("/api/v1/batches").json()["items"][0]
    assert status["status"] == "PAUSED" and status["items"][0]["status"] == "UNKNOWN"
    assert (
        connection.post(f"/api/v1/batches/{identifier}/action", json={"action": "resume"}, headers=ORIGIN).status_code
        == 409
    )
    assert (
        connection.post(
            f"/api/v1/batches/{identifier}/action",
            json={"action": "resume", "acknowledge_unknown": True},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert (
        connection.post(f"/api/v1/batches/{identifier}/action", json={"action": "cancel"}, headers=ORIGIN).status_code
        == 200
    )
    status = connection.get("/api/v1/batches").json()["items"][0]
    assert status["status"] == "CANCELLED" and status["items"][2]["status"] == "CANCELLED"


def test_schedule_resource_revocation_and_private_history(client):
    connection, app = client
    identifier = schedule(connection)
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN)
    rows = connection.portal.call(app.state.db.fetch, "SELECT snapshot FROM automations WHERE id=%s", (identifier,))
    connection.portal.call(
        app.state.db.execute, "UPDATE agents SET enabled=false WHERE id=%s", (rows[0]["snapshot"]["id"],)
    )
    connection.portal.call(AutomationScheduler(app.state).schedule, UUID(identifier))
    item = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]
    assert item["status"] == "REJECTED" and item["ask_run_id"] is None
    assert connection.get(f"/api/v1/automations/{uuid4()}/occurrences").status_code == 404
    assert (
        connection.post(
            "/api/v1/automations", json={"name": "伪造", "owner_id": str(uuid4())}, headers=ORIGIN
        ).status_code
        == 422
    )


def test_revoked_account_blocks_direct_model_callback(client):
    connection, app = client
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    connection.portal.call(app.state.db.execute, "UPDATE users SET enabled=false WHERE id=%s", (owner,))
    recorder = UsageRecorder(app.state.db, owner, uuid4(), "test", "ask")
    with pytest.raises(APIError, match="账号"):
        connection.portal.call(
            partial(recorder.on_chat_model_start, {}, [[HumanMessage(content="不能发送")]], run_id=uuid4())
        )


def test_hidden_model_callback_rejects_expired_worker_without_reservation(client):
    connection, app = client
    identifier = schedule(connection)
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN)
    connection.portal.call(AutomationScheduler(app.state).schedule, UUID(identifier))
    item = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    token, run = uuid4(), UUID(item["ask_run_id"])
    connection.portal.call(
        app.state.db.execute,
        "UPDATE task_runs SET status='RUNNING',lease_token=%s,lease_until=now()-interval '1 second' WHERE id=%s",
        (token, run),
    )
    recorder = UsageRecorder(app.state.db, owner, run, "test", "ask", lease_token=token)
    with pytest.raises(APIError) as failure:
        connection.portal.call(
            partial(recorder.on_chat_model_start, {}, [[HumanMessage(content="摘要也必须检查租约")]], run_id=uuid4())
        )
    assert failure.value.code == "ASK_LEASE_LOST"
    assert not connection.portal.call(app.state.db.fetch, "SELECT 1 FROM model_usage WHERE run_id=%s", (run,))


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local sandbox acceptance")
def test_automatic_work_uses_real_sandbox_and_records_terminal_occurrence(client):
    from opensandbox.config import ConnectionConfig

    from app.durable_sandboxes import DurableSandboxes
    from app.work_artifacts import WorkArtifacts
    from app.work_engine import WorkEngine

    connection, app = client
    app.state.settings.work_enabled = True
    identifier = schedule(connection, mode="work")
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN)
    connection.portal.call(AutomationScheduler(app.state).schedule, UUID(identifier))
    occurrence = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]
    run = occurrence["work_run_id"]
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    sandbox = DurableSandboxes(
        app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)
    )
    engine = WorkEngine(
        app.state.db,
        app.state.models,
        sandbox,
        WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts"),
        app.state.runtime.saver,
        lambda _: ToolModel(
            responses=[
                AIMessage(
                    content="自动工作已完成", usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
                )
            ]
        ),
        capabilities_factory=lambda _: app.state.capabilities,
    )

    async def perform():
        row = (
            await app.state.db.fetch(
                "UPDATE work_queue_runs SET status='RUNNING',lease_token=%s,lease_until=now()+interval '60 seconds' "
                "WHERE id=%s AND status='PENDING' RETURNING *",
                (uuid4(), run),
            )
        )[0]
        await engine.events.perform(row)

    try:
        connection.portal.call(perform)
        state = connection.get(f"/api/v1/work-runs/{run}").json()
        assert state["status"] == "COMPLETED" and state["used_tokens"] == 6, state
        ended = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]
        assert ended["result_status"] == "SUCCEEDED" and ended["target_id"] == state["task_id"]
    finally:
        connection.portal.call(sandbox.close)
        connection.portal.call(
            app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s AND state='RELEASED'", (run,)
        )
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run,))
