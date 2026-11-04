"""Actual formal HTTP router return contract; only identity/storage are substituted."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app import model_api, workflow_api
from app.errors import APIError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from evaluation_runner import fixed_versioned_workflow as facts


@pytest.fixture
def router(monkeypatch):
    owner, workflow, model = uuid4(), uuid4(), str(uuid4())
    current = {"id": workflow, "owner_id": owner, "name": "F01", "model_id": model, "revision": 1,
               "instructions": "", "layout": {}, "definition": facts.graph("upper")}
    model_state = {"id": model, "revision": 1, "temperature": None}
    writes = []

    class Database:
        async def fetch(self, sql, args):
            if sql.startswith("SELECT * FROM workflows"):
                assert args == (workflow, owner)
                return [copy.deepcopy(current)]
            assert sql.startswith("UPDATE workflows SET") and "RETURNING id" in sql
            writes.append((sql, args))
            assert args[7:9] == (workflow, owner)
            if args[-1] != current["revision"]:
                return []
            current.update(name=args[0], instructions=args[1], model_id=args[2], definition=args[3].obj, revision=2)
            return [{"id": workflow}]

    class Models:
        async def save(self, body, identifier):
            assert identifier == model
            if body.revision != model_state["revision"]:
                raise APIError(409, "MODEL_REVISION_CONFLICT", "stale")
            writes.append((identifier, body.model_dump()))
            model_state.update(revision=2, temperature=body.temperature)
            return identifier

    async def identity(request):
        return {"id": owner, "is_admin": True}

    async def validate_edit(request, body, definition, requested_owner):
        assert requested_owner == owner and definition.model_dump() == facts.graph("lower")
    monkeypatch.setattr(workflow_api, "identity", identity)
    monkeypatch.setattr(model_api, "administrator", identity)
    monkeypatch.setattr(workflow_api, "validate_edit", validate_edit)
    app = FastAPI()
    app.state.db, app.state.models = Database(), Models()
    app.include_router(workflow_api.router)
    app.include_router(model_api.router)

    @app.exception_handler(APIError)
    async def error(request: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}})
    return SimpleNamespace(app=app, workflow=workflow, model=model, current=current, model_state=model_state, writes=writes)


async def test_actual_workflow_put_router_returns_id_only_and_preserves_request_cas(router):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url="http://formal-router.invalid") as client:
        body = {"name": "F01", "model_id": router.model, "revision": 1, "definition": facts.graph("lower")}
        response = await client.put(f"/api/v1/workflows/{router.workflow}", json=body)
        assert response.status_code == 200 and response.json() == {"id": str(router.workflow)}
        assert router.current["revision"] == 2 and router.current["definition"] == facts.graph("lower")
        stale = await client.put(f"/api/v1/workflows/{router.workflow}", json=body)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "WORKFLOW_CONFLICT"
        assert len(router.writes) == 2 and router.writes[0][1][-1] == 1


async def test_actual_model_put_router_returns_id_only_and_does_not_promise_revision(router):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router.app), base_url="http://formal-router.invalid") as client:
        body = {"name": "F01", "model": "eval::AET-F01::v1", "base_url": "http://controlled.invalid", "revision": 1,
                "kind": "chat", "provider": "custom", "protocol": "openai", "temperature": 0.4, "max_output_tokens": 4096}
        response = await client.put(f"/api/v1/admin/models/{router.model}", json=body)
        assert response.status_code == 200 and response.json() == {"id": router.model}
        assert router.model_state == {"id": router.model, "revision": 2, "temperature": 0.4}
        stale = await client.put(f"/api/v1/admin/models/{router.model}", json=body)
        assert stale.status_code == 409 and len(router.writes) == 1
