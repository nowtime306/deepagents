import asyncio
from types import SimpleNamespace
from uuid import uuid4

import evaluation_observers
import httpx
import pytest
from app.errors import APIError
from app.resource_runtime import ResourceRun, active_resources
from evaluation_target import LIMITS, ProfileState, model_factory
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage


@pytest.mark.parametrize("case_id", ["AET-F02", "AET-F04"])
def test_readonly_cases_have_hard_zero_provider_caps_across_roots_and_reconstruction(tmp_path, case_id):
    path = tmp_path / "provider.json"
    state, owner = ProfileState(path), uuid4()
    state.register_model("fixed", case_id, "lead", owner)
    config = SimpleNamespace(id="fixed", model=f"eval::{case_id}::v1")
    assert LIMITS[case_id] == (0, 0)
    for current in (state, ProfileState(path)):
        for _ in range(2):
            token = active_resources.set(ResourceRun(None, owner, "workflow", uuid4()))
            try:
                with pytest.raises(RuntimeError, match="budget"):
                    model_factory(config, current).invoke([HumanMessage(content="Unexpected model node")])
            finally:
                active_resources.reset(token)
        assert not current.calls
        assert all(value["calls"] == value["tokens"] == value["pending"] == 0 for value in current.roots.values())


def observer_app(monkeypatch, *, binding_case="AET-F04", other_owner=False, missing=False, authorized=True, work=False):
    owner, identifier, namespace = uuid4(), uuid4(), uuid4()
    state = ProfileState()
    state.register_model("sealed", binding_case, "lead", uuid4() if other_owner else owner)
    row = {"model_id": "sealed", "namespace": namespace, "run_ids": [str(uuid4())],
           **dict.fromkeys(evaluation_observers.WORKFLOW_COUNTS, 0), "raw_log": "private-canary"}
    row.update(workflow_runs=1, workflow_events=4)
    calls = []

    class Database:
        async def fetch(self, sql, params):
            calls.append((sql, params))
            assert params == (identifier, owner)  # Owner comes from live attestation, never from query parameters.
            return [] if missing else [row]

    async def environment(_):
        if not authorized:
            raise APIError(403, "DENIED", "forbidden")
        return {"environment_id": uuid4(), "owner_id": owner}

    monkeypatch.setattr(evaluation_observers, "environment", environment)
    app = FastAPI()

    @app.exception_handler(APIError)
    async def errors(_, error):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code}})

    app.state.db, app.state.evaluation_profiles = Database(), state
    app.state.settings = SimpleNamespace(work_enabled=work)
    evaluation_observers.attach_observers(app)

    def get():
        async def invoke():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.get(f"/api/v1/agent-evaluations/profiles/workflows/{identifier}/observations")
        return asyncio.run(invoke())

    return get, state, owner, calls


def test_formal_target_only_observer_reports_complete_registered_zero_not_missing_root_default(monkeypatch):
    get, state, owner, calls = observer_app(monkeypatch)
    # A foreign owner's provider calls must not enter the authenticated owner's counters.
    state.roots[(str(uuid4()), "workflow", str(uuid4()))] = {"case_id": "AET-F04", "calls": 9, "tokens": 100,
                                                          "pending": 0, "unknown": False}
    response = get()
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == {"observed": True, "complete": True, "calls": 0, "pending": 0, "unknown_roots": 0}
    assert body["sandbox"] == {"observed": True, "complete": True, "attempts": 0}
    assert len(calls) == 1
    assert body["counts"]["workflow_runs"] == 1 and body["counts"]["workflow_events"] == 4
    assert body["owner_id"] == str(owner) and "private-canary" not in response.text


@pytest.mark.parametrize("options, status", [({"other_owner": True}, 403), ({"binding_case": "AET-A02"}, 403),
                                             ({"missing": True}, 404), ({"authorized": False}, 403)])
def test_observer_never_exports_foreign_unsealed_or_unauthorized_workflow_counts(monkeypatch, options, status):
    get, _, _, calls = observer_app(monkeypatch, **options)
    assert get().status_code == status
    if options.get("authorized") is False:
        assert calls == []


@pytest.mark.parametrize("failure", ["provider_unknown", "provider_pending", "provider_storage", "missing_sandbox"])
def test_unknown_or_unavailable_effect_observer_is_not_reported_as_complete_zero(monkeypatch, failure):
    get, state, owner, _ = observer_app(monkeypatch, work=failure == "missing_sandbox")
    if failure in {"provider_unknown", "provider_pending"}:
        state.roots[(str(owner), "workflow", str(uuid4()))] = {"case_id": "AET-F04", "calls": 0, "tokens": 0,
            "pending": int(failure == "provider_pending"), "unknown": failure == "provider_unknown"}
    elif failure == "provider_storage":
        state.storage_failed = True
    body = get().json()
    assert body["sandbox" if failure == "missing_sandbox" else "provider"]["complete"] is False
