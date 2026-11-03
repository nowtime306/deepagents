"""Durable capacity, ownership and orphan cleanup against isolated PostgreSQL."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxApiException
from test_api import ORIGIN
from test_api import client as client

from app.durable_sandboxes import DurableSandboxes
from app.work_queue import WorkQueue


@pytest.fixture(autouse=True)
def sufficient_capacity(monkeypatch):
    monkeypatch.setattr("app.durable_sandboxes.resource_budget", AsyncMock(return_value=20 * 1024**3))


class Inventory:
    def __init__(self):
        self.items = {}
        self.fail_get = False
        self.fail_kill = False

    async def list_sandbox_infos(self, filters):
        return SimpleNamespace(sandbox_infos=list(self.items.values()), pagination=SimpleNamespace(has_next_page=False))

    async def kill_sandbox(self, identifier):
        if self.fail_kill:
            raise SandboxApiException("Unavailable", status_code=503)
        self.items.pop(identifier, None)

    async def get_sandbox_info(self, identifier):
        if self.fail_get:
            raise SandboxApiException("Unavailable", status_code=503)
        if identifier not in self.items:
            raise SandboxApiException("Not found", status_code=404)
        return self.items[identifier]

    async def close(self):
        pass


def prepare(client):
    connection, app = client
    task = connection.post("/api/v1/work-tasks", json={"title": "沙箱归属", "goal": "测试"}, headers=ORIGIN).json()
    task_id = UUID(task["id"])
    row = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_tasks WHERE id=%s", (task_id,))[0]
    queue = WorkQueue(app.state.db)
    identifier = connection.portal.call(queue.enqueue, task_id, row["owner_id"], 1)
    run = connection.portal.call(queue.claim)
    assert run["id"] == identifier
    return connection, app, run


def cleanup(connection, app, run):
    connection.portal.call(app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s", (run["id"],))
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run["id"],))
    connection.delete(f"/api/v1/work-tasks/{run['task_id']}", headers=ORIGIN)


def test_two_instances_share_capacity_and_enforce_lease(client, monkeypatch):
    connection, app, run = prepare(client)
    inventory = Inventory()
    monkeypatch.setattr("app.durable_sandboxes.SandboxManager.create", AsyncMock(return_value=inventory))
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True)
    first, second = DurableSandboxes(app.state.db, config), DurableSandboxes(app.state.db, config)

    async def exercise():
        assert await first.reserve(run["id"], uuid4(), "coding") is None
        results = await asyncio.gather(
            first.reserve(run["id"], run["lease_token"], "coding"),
            second.reserve(run["id"], run["lease_token"], "coding"),
        )
        assert sum(item is not None for item in results) == 1
        session = next(item for item in results if item is not None)
        assert session["owner_id"] == run["owner_id"]
        assert await second.reserve(run["id"], run["lease_token"], "coding") is None
        assert await first.owned(uuid4(), run["lease_token"], session["id"]) is None
        # Missing inventory cannot prove that an in-flight create never happened.
        assert not await first.release(session["id"])
        assert await second.reserve(run["id"], run["lease_token"], "coding") is None

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)


def test_lost_create_response_is_reconciled_after_restart(client, monkeypatch):
    connection, app, run = prepare(client)
    inventory = Inventory()
    monkeypatch.setattr("app.durable_sandboxes.SandboxManager.create", AsyncMock(return_value=inventory))

    async def create(image, **kwargs):
        identifier = str(uuid4())
        inventory.items[identifier] = SimpleNamespace(id=identifier, metadata=kwargs["metadata"])
        raise RuntimeError("Response lost")

    monkeypatch.setattr("app.durable_sandboxes.Sandbox.create", create)
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True)

    async def exercise():
        service = DurableSandboxes(app.state.db, config)
        with pytest.raises(RuntimeError):
            await service.create(run["id"], run["lease_token"], "coding")
        await app.state.db.execute(
            "UPDATE work_queue_runs SET lease_until=now()-interval '1 second' WHERE id=%s",
            (run["id"],),
        )
        # An unrelated sandbox from another deployment must survive cleanup.
        inventory.items["unrelated"] = SimpleNamespace(id="unrelated", metadata={"namespace": "other"})
        restarted = DurableSandboxes(app.state.db, config)
        assert await restarted.reconcile() == 1
        assert list(inventory.items) == ["unrelated"]
        row = (await app.state.db.fetch("SELECT state FROM work_sandbox_sessions WHERE run_id=%s", (run["id"],)))[0]
        assert row["state"] == "RELEASED"

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)


def test_management_outage_never_frees_capacity(client, monkeypatch):
    connection, app, run = prepare(client)
    inventory = Inventory()
    monkeypatch.setattr("app.durable_sandboxes.SandboxManager.create", AsyncMock(return_value=inventory))
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True)

    async def exercise():
        service = DurableSandboxes(app.state.db, config)
        session = await service.reserve(run["id"], run["lease_token"], "coding")
        await app.state.db.execute(
            "UPDATE work_sandbox_sessions SET sandbox_id='remote',state='READY' WHERE id=%s",
            (session["id"],),
        )
        inventory.fail_kill = True
        with pytest.raises(SandboxApiException):
            await service.release(session["id"])
        assert await service.reserve(run["id"], run["lease_token"], "coding") is None
        inventory.fail_kill, inventory.fail_get = False, True
        with pytest.raises(SandboxApiException):
            await service.release(session["id"])
        assert await service.reserve(run["id"], run["lease_token"], "coding") is None

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)


def test_low_memory_queues_second_environment_instead_of_oversubscribing(client, monkeypatch):
    connection, app, run = prepare(client)
    inventory = Inventory()
    monkeypatch.setattr("app.durable_sandboxes.SandboxManager.create", AsyncMock(return_value=inventory))
    monkeypatch.setattr("app.durable_sandboxes.resource_budget", AsyncMock(return_value=6 * 1024**3))
    service = DurableSandboxes(
        app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True)
    )

    async def exercise():
        first = await service.reserve(run["id"], run["lease_token"], "coding")
        assert first
        assert await service.reserve(run["id"], run["lease_token"], "browser") is None
        await app.state.db.execute("UPDATE work_sandbox_sessions SET sandbox_id='absent' WHERE id=%s", (first["id"],))
        assert await service.release(first["id"])
        assert await service.reserve(run["id"], run["lease_token"], "browser")

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)
