"""Formal Workflow task router/request responses; storage/auth boundaries only are substituted."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app import workflow_tasks
from app.errors import APIError
from app.workflow_definition import validate
from evaluation_runner import fixed_code_contract as contract
from evaluation_runner.fixed_fixtures import FixedFixtures
from fastapi import FastAPI
from fastapi.responses import JSONResponse


class Cursor:
    def __init__(self, value=None):
        self.value = value

    async def fetchone(self):
        return self.value


@pytest.fixture
def formal(monkeypatch):
    owner, workflow, version = uuid4(), uuid4(), uuid4()
    writes = []

    class Database:
        pool = None

        def __init__(self):
            self.pool = self

        @asynccontextmanager
        async def connection(self):
            yield self

        async def fetch(self, sql, args=()):
            if sql.startswith("SELECT 1 FROM work_worker_instances"):
                return [1]
            source = {
                "id": workflow,
                "owner_id": owner,
                "revision": 1,
                "name": "F05",
                "instructions": "",
                "model_id": "fixed",
                "definition": contract.definition(),
                "layout": {},
            }
            if sql.startswith("SELECT * FROM workflows"):
                return [source] if args == (workflow, owner) else []
            if sql.startswith("SELECT * FROM workflow_versions"):
                return [
                    {
                        **source,
                        "id": version,
                        "workflow_id": workflow,
                        "version": 1,
                        "draft_revision": 1,
                        "active": True,
                    }
                ]
            raise AssertionError("Unexpected contract read")

        async def execute(self, sql, args):
            if sql.startswith("SELECT revision FROM workflows"):
                return Cursor({"revision": 1})
            if sql.startswith("SELECT 1 FROM workflow_versions"):
                return Cursor({"id": version})
            writes.append((sql, args))
            if sql.startswith("INSERT INTO workflow_runs"):
                return Cursor({"id": args[0]})
            assert sql.startswith(
                (
                    "INSERT INTO work_tasks",
                    "INSERT INTO work_task_workflows",
                    "INSERT INTO work_queue_runs",
                    "INSERT INTO work_run_workflows",
                )
            )
            return Cursor()

    async def identity(request):
        return {"id": owner}

    async def permission(*args):
        return None

    async def resources(request, definition, requested_owner, model):
        assert requested_owner == owner and model == "fixed"
        validate(definition)
        assert definition.model_dump() == contract.definition()
        return {"models": {"fixed": 1}}

    monkeypatch.setattr(workflow_tasks, "identity", identity)
    monkeypatch.setattr(workflow_tasks, "require_account", permission)
    monkeypatch.setattr(workflow_tasks, "validate_definition", resources)
    app = FastAPI()
    app.state.db, app.state.settings = Database(), SimpleNamespace(work_enabled=True)
    app.include_router(workflow_tasks.router)

    @app.exception_handler(APIError)
    async def error(request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}})

    return app, workflow, writes


@pytest.mark.parametrize("selection", [{}, {"revision": 1, "version": 1}, {"revision": 2}])
async def test_formal_selection_refuses_missing_ambiguous_or_stale_before_queue_insert(formal, selection):
    app, workflow, writes = formal
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://formal-task.invalid"
    ) as client:
        response = await client.post(f"/api/v1/workflows/{workflow}/work-runs", json={"text": "seed", **selection})
    assert response.status_code == (409 if selection == {"revision": 2} else 422)
    assert not writes


@pytest.mark.parametrize("selection", [{"revision": 1}, {"version": 1}])
async def test_formal_task_response_and_fixed_fixture_retains_all_three_server_ids(formal, selection):
    app, workflow, writes = formal
    path, payload = f"/api/v1/workflows/{workflow}/work-runs", {"text": "seed", **selection}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://formal-task.invalid"
    ) as client:
        response = await client.post(path, json=payload)
    assert response.status_code == 202, response.text
    value = response.json()
    assert set(value) == {"run_id", "task_id", "workflow_run_id", "status"} and value["status"] == "PENDING"
    assert len({value[key] for key in ("run_id", "task_id", "workflow_run_id")}) == 3
    attempt = {"attempt_id": str(uuid4())}
    journal = SimpleNamespace(save=lambda: None)
    # Consume the actual formal router Response object, never a self-invented response dictionary.
    fixtures = FixedFixtures(
        SimpleNamespace(attest=lambda: None, http=SimpleNamespace(request=lambda *args, **kwargs: response)),
        journal,
        attempt,
    )
    retained = fixtures.mutate("start", "POST", path, body=payload, accepted=(202,))
    assert retained == value
    snapshot = next(args[3].obj for sql, args in writes if sql.startswith("INSERT INTO workflow_runs"))
    assert snapshot["definition"] == contract.definition() and snapshot["revision"] == 1
    assert snapshot.get("published_version") == selection.get("version")
