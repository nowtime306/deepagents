"""Formal-router/PG contract with real sessions and no main worker/key fallback."""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.configuration_history import begin_context, end_context
from app.errors import APIError
from app.manage import migrate
from app.model_api import router as models_router
from app.models import CredentialVault, ModelRegistry
from app.security import COOKIE, token_digest
from app.storage import Database
from app.workflow_api import router as workflows_router

ORIGIN = {"Origin": "http://formal-router.invalid"}


def isolated_scope():
    url, data = os.getenv("TEST_DATABASE_URL", ""), os.getenv("TEST_DATA_DIR", "")
    if not url or not data:
        pytest.skip("Requires explicit independent TEST_DATABASE_URL and TEST_DATA_DIR")
    path = Path(data)
    config = conninfo_to_dict(url)
    if (
        not config.get("dbname", "").endswith("_test")
        or config.get("host") not in {"localhost", "127.0.0.1", "::1"}
        or not path.is_absolute()
        or not path.name.endswith("_test")
        or not path.is_dir()
    ):
        pytest.fail("Only an explicitly isolated _test database/data directory is permitted")
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
        pytest.fail("Test data links are forbidden")
    return url, path


@pytest.fixture
def client():
    url, data = isolated_scope()
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(migrate(url))
    database = Database(url)

    @asynccontextmanager
    async def lifespan(app):
        await database.pool.open()
        await database.pool.wait()
        yield
        await database.pool.close()

    def no_provider(_):
        raise AssertionError("Response contract must not execute a model")

    app = FastAPI(lifespan=lifespan)
    app.state.db, app.state.models = database, ModelRegistry(database, CredentialVault(data / "response-contract.key"))
    app.state.capabilities, app.state.runtime = SimpleNamespace(), SimpleNamespace(model_factory=no_provider)
    app.include_router(models_router)
    app.include_router(workflows_router)

    @app.middleware("http")
    async def context(request, next_handler):
        tokens = begin_context(source="system:response-contract")
        try:
            return await next_handler(request)
        finally:
            end_context(tokens)

    @app.exception_handler(APIError)
    async def api_error(request, error):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})

    with TestClient(app, backend_options={"loop_factory": factory}) as connection:
        owner, token = uuid4(), uuid4().hex
        connection.portal.call(
            database.execute,
            "INSERT INTO users(id,username,password_hash,is_admin,role_id) VALUES(%s,%s,%s,true,'admin')",
            (owner, "response-contract-" + uuid4().hex, "fixture-real-session-no-login"),
        )
        connection.portal.call(
            database.execute,
            "INSERT INTO auth_sessions(token_hash,owner_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",
            (token_digest(token), owner),
        )
        connection.cookies.set(COOKIE, token)
        yield connection, app, owner


def test_real_put_responses_are_ids_while_read_models_and_workflow_revisions_are_two(client):
    from evaluation_runner import fixed_versioned_workflow as facts

    connection, app, owner = client
    body = {
        "name": "F01 contract " + uuid4().hex,
        "model": "eval::AET-F01::v1",
        "base_url": "http://controlled.invalid",
        "kind": "chat",
        "provider": "custom",
        "protocol": "openai",
        "supports_tools": True,
        "max_output_tokens": 4096,
    }
    created = connection.post("/api/v1/admin/models", headers=ORIGIN, json=body)
    assert created.status_code == 201, created.text
    model = created.json()["id"]
    maintained = connection.put(
        "/api/v1/admin/models/" + model, headers=ORIGIN, json={**body, "revision": 1, "temperature": 0.4}
    )
    assert maintained.status_code == 200 and maintained.json() == {"id": model}
    selected = next(row for row in connection.get("/api/v1/admin/models").json()["items"] if row["id"] == model)
    assert selected["revision"] == 2 and selected["temperature"] == 0.4
    assert (
        connection.put("/api/v1/admin/models/" + model, headers=ORIGIN, json={**body, "revision": 1}).status_code == 409
    )

    workflow_body = {"name": "F01 workflow " + uuid4().hex, "model_id": model, "definition": facts.graph("upper")}
    created = connection.post("/api/v1/workflows", headers=ORIGIN, json=workflow_body)
    assert created.status_code == 201, created.text
    workflow = created.json()["id"]
    changed = connection.put(
        "/api/v1/workflows/" + workflow,
        headers=ORIGIN,
        json={**workflow_body, "revision": 1, "definition": facts.graph("lower")},
    )
    assert changed.status_code == 200 and changed.json() == {"id": workflow}
    row = next(item for item in connection.get("/api/v1/workflows").json()["items"] if item["id"] == workflow)
    assert row["revision"] == 2 and row["definition"] == facts.graph("lower")
    assert (
        connection.put(
            "/api/v1/workflows/" + workflow, headers=ORIGIN, json={**workflow_body, "revision": 1}
        ).status_code
        == 409
    )
    counts = connection.portal.call(
        app.state.db.fetch,
        "SELECT (SELECT count(*) FROM model_usage WHERE owner_id=%s) AS model_calls,"
        "(SELECT count(*) FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
        "WHERE w.owner_id=%s) AS workflow_runs,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s) AS work_runs,"
        "(SELECT count(*) FROM work_sandbox_sessions WHERE owner_id=%s) AS sessions",
        (owner,) * 4,
    )[0]
    assert counts == {"model_calls": 0, "workflow_runs": 0, "work_runs": 0, "sessions": 0}
