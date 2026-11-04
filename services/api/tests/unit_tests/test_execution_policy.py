"""Normal imports; private author run uses a disclosed candidate package, not active proof."""

import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.ask_worker import AskWorker
from app.ask_workflows import workflow_tool
from app.config import Settings
from app.errors import APIError
from app.execution_policy import BLOCKED, CODE, bind, install_gate, leaves, require_execution
from app.main import create_app


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Pure candidate tests must not connect a database")

    monkeypatch.setattr("psycopg.AsyncConnection.connect", forbidden)


def test_default_and_per_instance_isolation():
    a, b = SimpleNamespace(), SimpleNamespace()
    bind(a, Settings(restricted_execution=True))
    bind(b, Settings())
    assert a.restricted_execution is True and b.restricted_execution is False
    require_execution(b)
    with pytest.raises(APIError) as error:
        require_execution(a)
    assert error.value.status == 503 and error.value.code == CODE
    with pytest.raises(ValueError):
        bind(a, Settings())
    assert a.restricted_execution is True


@pytest.mark.parametrize("flag", ["work_enabled", "automation_enabled", "evaluation_enabled", "media_worker_enabled"])
def test_conflicting_workers_fail_before_any_app(flag):
    with pytest.raises(ValueError):
        Settings(restricted_execution=True, **{flag: True})
    assert getattr(Settings(**{flag: True}), flag) is True


def test_env_applies_to_both_native_hosts(monkeypatch):
    monkeypatch.setenv("RESTRICTED_EXECUTION", "true")
    for name in ("WORK_ENABLED", "AUTOMATION_ENABLED", "EVALUATION_ENABLED", "MEDIA_WORKER_ENABLED"):
        monkeypatch.setenv(name, "false")
    settings = Settings.from_env()
    db = SimpleNamespace()
    worker = AskWorker(db, None, settings)
    assert worker.database.restricted_execution is True
    assert settings.ask_worker_enabled is True


@pytest.mark.parametrize("module,name", [(module, name) for module, names in BLOCKED.items() for name in sorted(names)])
async def test_every_current_frozen_execution_identity_rejects_before_handler(module, name):
    app = create_app(Settings(restricted_execution=True))
    routes = [
        r
        for r in leaves(app.routes)
        if (r.endpoint.__module__.rsplit(".", 1)[-1], r.endpoint.__name__) == (module, name)
    ]
    assert len(routes) == 1
    route = routes[0]
    path = re.sub(r"\{[^}]+\}", str(uuid4()), route.path)
    method = next(iter(route.methods - {"HEAD", "OPTIONS"}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path)
    assert response.status_code == 503 and response.json()["error"]["code"] == CODE


async def test_other_app_is_not_contaminated():
    restricted = create_app(Settings(restricted_execution=True))
    normal = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=normal), base_url="http://test") as client:
        response = await client.post("/api/v1/agent-evaluations/runs")
    assert response.status_code != 503  # Normal auth/origin/validation behavior runs, not restricted gate.
    assert restricted is not normal


async def test_management_and_ask_transport_can_reach_handlers(monkeypatch):
    # Admission unit: harmless callbacks stand for downstream auth/CRUD/Ask code, not a real Ask acceptance claim.
    @asynccontextmanager
    async def slot(*args, **kwargs):
        yield

    monkeypatch.setattr("app.maintenance.request_slot", slot)
    app = create_app(Settings(restricted_execution=True))
    called = []

    @app.get("/management-probe")
    async def management():
        called.append("metadata")
        return {"ok": True}

    @app.post("/ask-probe")
    async def ask():
        called.append("ask")
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Origin": "http://localhost:3100"}
    ) as client:
        assert (await client.get("/management-probe")).status_code == 200
        assert (await client.post("/ask-probe")).status_code == 200
    assert called == ["metadata", "ask"]


async def test_internal_ask_workflow_rejects_without_database_query():
    class Database:
        restricted_execution = True

        async def fetch(self, *args, **kwargs):
            pytest.fail("No database query may run")

    tool = workflow_tool(
        SimpleNamespace(database=Database(), models=None), uuid4(), {"workflow_version_ids": []}, {"id": uuid4()}
    )
    with pytest.raises(APIError) as error:
        await tool.coroutine(version_id=uuid4(), text="fixed")
    assert error.value.code == CODE


def test_missing_frozen_identity_fails_instead_of_ignoring():
    with pytest.raises(ValueError):
        install_gate(FastAPI())


@pytest.mark.parametrize("restricted", [True, False])
async def test_actual_lifespan_recovery_and_consumers_follow_instance_policy(tmp_path, monkeypatch, restricted):
    import asyncio

    import app.main as main

    calls = []

    async def noop(*args, **kwargs):
        pass

    class Pool:
        open = close = noop

    class Database:
        def __init__(self, *args):
            self.pool = Pool()

        async def fetch(self, sql, *args):
            if "FROM task_runs WHERE status" in sql:
                calls.append("ask-recovery")
            return []

        async def execute(self, sql, *args):
            assert "UPDATE repository_actions" in sql
            calls.append("repository-recovery")

    class BrowserSkills:
        def __init__(self, *args):
            pass

        async def recover(self):
            calls.append("browser-recovery")

        close = noop

    class Connection:
        async def execute(self, *args):
            return self

        async def fetchone(self):
            return (True,)

    @asynccontextmanager
    async def guard_context():
        yield Connection()

    async def connect(*args, **kwargs):
        return guard_context()

    @asynccontextmanager
    async def saver_context(*args, **kwargs):
        yield SimpleNamespace()

    class Runtime:
        def __init__(self, *args, **kwargs):
            pass

        close = noop

    class Queue:
        def __init__(self, *args, **kwargs):
            pass

        async def serve(self):
            calls.append("workflow-consumer")
            await asyncio.Event().wait()

    class Fence:
        def __init__(self, *args, **kwargs):
            pass

        start = close = noop

        async def monitor(self):
            await asyncio.Event().wait()

    async def reconcile(*args):
        calls.append("workflow-recovery")

    monkeypatch.setattr(main, "Database", Database)
    monkeypatch.setattr(main.AsyncConnection, "connect", connect)
    monkeypatch.setattr(main.AsyncPostgresSaver, "from_conn_string", saver_context)
    monkeypatch.setattr(main, "CredentialVault", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(main, "ModelRegistry", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(main, "KnowledgeService", lambda *a, **k: SimpleNamespace(vectors=SimpleNamespace(close=noop)))
    monkeypatch.setattr(main, "MCPService", lambda *a, **k: None)
    monkeypatch.setattr(main, "CapabilityService", lambda *a, **k: None)
    monkeypatch.setattr(main, "Runtime", Runtime)
    monkeypatch.setattr(main, "WorkflowRuntime", lambda *a, **k: None)
    monkeypatch.setattr(main, "WorkflowQueue", Queue)
    monkeypatch.setattr(main, "reconcile_startup", reconcile)
    monkeypatch.setattr("app.browser_skill_runtime.BrowserSkillRuntime", BrowserSkills)
    monkeypatch.setattr("app.service_fence.ServiceFence", Fence)
    monkeypatch.setattr("app.identity_delivery.start_delivery", lambda *a: calls.append("delivery-start"))

    async def stop(*a):
        calls.append("delivery-stop")

    monkeypatch.setattr("app.identity_delivery.stop_delivery", stop)
    app = create_app(Settings(restricted_execution=restricted, data_dir=tmp_path))
    async with app.router.lifespan_context(app):
        assert app.state.db.restricted_execution is restricted
        await asyncio.sleep(0)
    assert set(calls) == (
        set()
        if restricted
        else {
            "browser-recovery",
            "repository-recovery",
            "workflow-recovery",
            "ask-recovery",
            "workflow-consumer",
            "delivery-start",
            "delivery-stop",
        }
    )


def test_new_setting_is_keyword_only_and_old_settings_double_defaults():
    import inspect

    assert inspect.signature(Settings).parameters["restricted_execution"].kind is inspect.Parameter.KEYWORD_ONLY
    db = SimpleNamespace()
    bind(db, SimpleNamespace())
    assert db.restricted_execution is False


async def test_real_independent_ask_host_binds_env_policy_before_serve(monkeypatch):
    import app.ask_server as host

    seen = []
    monkeypatch.setenv("RESTRICTED_EXECUTION", "true")
    for name in ("WORK_ENABLED", "AUTOMATION_ENABLED", "EVALUATION_ENABLED", "MEDIA_WORKER_ENABLED"):
        monkeypatch.setenv(name, "false")

    async def noop(*args, **kwargs):
        pass

    class Pool:
        open = close = noop

    class Database:
        def __init__(self, *args):
            self.pool = Pool()

    class Connection:
        async def execute(self, *args):
            return self

        async def fetchone(self):
            return (True,)

    @asynccontextmanager
    async def context():
        yield Connection()

    async def connect(*args, **kwargs):
        return context()

    class Fence:
        def __init__(self, *args, **kwargs):
            pass

        start = close = noop

    async def fenced(fence, operation):
        await operation

    async def serve(worker, event):
        assert worker.settings.restricted_execution is True
        assert worker.database.restricted_execution is True
        seen.append("restricted-ask")

    monkeypatch.setattr(host, "Database", Database)
    monkeypatch.setattr(host.AsyncConnection, "connect", connect)
    monkeypatch.setattr(host, "ModelRegistry", lambda *a, **k: None)
    monkeypatch.setattr(host, "CredentialVault", lambda *a, **k: None)
    monkeypatch.setattr(host, "ServiceFence", Fence)
    monkeypatch.setattr(host, "run_fenced", fenced)
    monkeypatch.setattr(host.AskWorker, "serve", serve)
    await host.main()
    assert seen == ["restricted-ask"]
