import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import pytest
from app.ask_worker import AskWorker
from app.resource_runtime import ResourceRun, active_resources
from evaluation_automations import A01_BUDGET, ControlledAskWorker
from evaluation_target import ProfileState, model_factory
from langchain_core.messages import HumanMessage


def call(state, owner, root):
    token = active_resources.set(ResourceRun(None, owner, "ask", root))
    try:
        return state.reserve("AET-A01", "lead", [HumanMessage(content="fixed")])
    finally:
        active_resources.reset(token)


def test_case_budget_covers_both_roots_and_reconstruction_without_reset(tmp_path):
    state, owner, roots = ProfileState(tmp_path / "state.json"), uuid4(), [uuid4(), uuid4()]
    for root in roots:
        for _ in range(4):
            state.complete(call(state, owner, root))
    assert state.case_summary(owner, "AET-A01")["calls"] == 8
    for current in (state, ProfileState(state.path)):
        with pytest.raises(RuntimeError, match="case budget"):
            call(current, owner, uuid4())
        assert current.case_summary(owner, "AET-A01")["tokens"] == 120


def test_pending_or_unknown_poison_other_root_and_no_third_root(tmp_path):
    owner = uuid4()
    state = ProfileState(tmp_path / "state.json")
    key = call(state, owner, uuid4())
    with pytest.raises(RuntimeError, match="case budget"):
        call(state, owner, uuid4())
    restored = ProfileState(state.path)
    with pytest.raises(RuntimeError, match="case budget"):
        call(restored, owner, uuid4())
    state.complete(key)
    state.complete(call(state, owner, uuid4()))
    with pytest.raises(RuntimeError, match="case budget"):
        call(state, owner, uuid4())
    assert state.case_summary(owner, "AET-A01")["calls"] == 2


def test_occurrence_factory_does_not_create_another_automation(tmp_path):
    owner, root = uuid4(), uuid4()
    state = ProfileState(tmp_path / "state.json")
    state.register_model("sealed", "AET-A01", "lead", owner)
    state.bind_a01_phase(owner, root, "occurrence")
    token = active_resources.set(ResourceRun(None, owner, "ask", root))
    try:
        result = model_factory(SimpleNamespace(id="sealed", model="eval::AET-A01::v1"), state).invoke([
            HumanMessage(content="整理虚拟日报")])
    finally:
        active_resources.reset(token)
    assert result.content == "虚拟日报已整理。" and not result.tool_calls
    assert state.case_summary(owner, "AET-A01")["calls"] == 1


@pytest.mark.parametrize("origin, valid", [("interactive", True), ("schedule", True), ("unregistered", False)])
def test_actual_claimed_origin_determines_phase_before_delegating_real_worker(tmp_path, monkeypatch, origin, valid):
    state, owner, root, version = ProfileState(tmp_path / "state.json"), uuid4(), uuid4(), uuid4()
    state.register_model("sealed", "AET-A01", "lead", owner)
    row = {"owner_id": owner, "agent_version_id": version, "origin": origin,
           "automation_occurrence_id": uuid4() if origin == "schedule" else None, "ask_run_id": root,
           "target_owner": owner, "target_version": version, "kind": "once", "mode": "ask", "goal": "整理虚拟日报",
           "budget": A01_BUDGET, "notify_channel_ids": [], "network_domains": []}
    calls = []

    class Database:
        async def fetch(self, sql, params):
            assert "r.lease_token=%s" in sql and "r.lease_until>now()" in sql and params[0] == root
            return [row]

        async def event(self, *args, **kwargs):
            calls.append("rejected")

    async def original(self, run, connection):
        calls.append((str(root), connection))

    monkeypatch.setattr(AskWorker, "execute", original)
    # Constructor dependencies are irrelevant to this phase wrapper. Its superclass remains the production worker.
    worker = object.__new__(ControlledAskWorker)
    worker.database, worker.profile_state = Database(), state
    asyncio.run(worker.execute({"id": root, "model_id": "sealed", "lease_token": uuid4()}, "connection"))
    if valid:
        assert calls == [(str(root), "connection")]
        assert state.a01_phases[f"{owner}:ask:{root}"] == ("draft" if origin == "interactive" else "occurrence")
    else:
        assert calls == ["rejected"] and not state.a01_phases


@pytest.mark.parametrize("change, expected", [(None, 200), ("permission", 403), ("foreign", 403), ("wrong_case", 403),
    ("no_tool", 403), ("wrong_call", 403), ("unknown", 403), ("other_unknown", 409), ("broad_scheduler", 503), ("wrong_goal", 403)])
def test_fixed_scheduler_endpoint_requires_real_source_tool_owner_and_settled_budget(tmp_path, monkeypatch, change, expected):
    import json

    import evaluation_automations as module
    import httpx
    from app.errors import APIError
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    state, owner, root, version = ProfileState(tmp_path / "state.json"), uuid4(), uuid4(), uuid4()
    call_id, scheduled = "ae-2222222222222222", []
    automation = uuid5(root, call_id)
    row = {"id": automation, "agent_version_id": version, "source_version": version, "kind": "once", "mode": "ask",
           "goal": "整理虚拟日报", "budget": A01_BUDGET, "notify_channel_ids": [], "network_domains": [],
           "enabled": False, "next_due_at": None, "completed_at": None, "revision": 1,
           "model_id": "sealed", "target_model": "sealed", "source_status": "SUCCEEDED", "origin": "interactive"}
    state.register_model("sealed", "AET-A02" if change == "wrong_case" else "AET-A01", "lead", uuid4() if change == "foreign" else owner)
    state.bind_a01_phase(owner, root, "draft")
    state.roots[(str(owner), "ask", str(root))] = {"case_id": "AET-A01", "calls": 3, "tokens": 45, "pending": 0, "unknown": change == "unknown"}
    if change == "other_unknown":
        state.roots[(str(owner), "ask", str(uuid4()))] = {"case_id": "AET-A01", "calls": 1, "tokens": 15, "pending": 0, "unknown": True}
    if change == "wrong_goal":
        row["goal"] = "unregistered goal"
    messages = [{"content": {"data": {"name": "create_automation_draft", "tool_call_id": "different" if change == "wrong_call" else call_id,
        "content": json.dumps({"type": "automation_draft", "id": str(automation), "enabled": False})}}}]

    class Database:
        async def fetch(self, sql, params):
            if "FROM automations a JOIN task_runs" in sql:
                assert params == (root, automation, owner) and "a.owner_id=%s" in sql
                return [row]
            if "conversation_messages" in sql:
                return [] if change == "no_tool" else messages
            if "FROM automation_occurrences" in sql:
                return []
            if "FROM task_runs r JOIN conversations" in sql:
                return [{"run_id": root, "status": "SUCCEEDED", "calls": 3, "reported_calls": 3, "unknown_calls": 0,
                    "pending_calls": 0, "input_tokens": 30, "output_tokens": 15}]
            if "active_ask_runs" in sql:
                return [{"active_ask_runs": 0, "work_runs": 0, "unsettled_usage": 0}]
            raise AssertionError(sql)

    async def environment(_):
        return {"owner_id": owner, "environment_id": uuid4(), "sandbox_namespace": uuid4()}

    async def require(db, account, permission):
        assert account == owner
        if change == "permission":
            raise APIError(403, "DENIED", "forbidden")

    async def schedule(self, identifier):
        scheduled.append(identifier)

    monkeypatch.setattr(module, "environment", environment)
    monkeypatch.setattr(module, "require_account", require)
    monkeypatch.setattr(module.AutomationScheduler, "schedule", schedule)
    app = FastAPI()
    app.state.db, app.state.evaluation_profiles = Database(), state
    app.state.settings = SimpleNamespace(automation_enabled=change == "broad_scheduler")

    @app.exception_handler(APIError)
    async def errors(_, error):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code}})

    module.attach_automations(app)

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            return await http.post(f"/api/v1/agent-evaluations/profiles/automations/{automation}/step", json={"source_run_id": str(root)})

    response = asyncio.run(invoke())
    assert response.status_code == expected
    assert scheduled == ([UUID(str(automation))] if expected == 200 else [])
