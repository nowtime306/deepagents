"""Prepared independent acceptance scenarios; execution awaits root's frozen QA start gate."""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from test_api import ORIGIN
from test_api import client as client
from test_resource_recovery import paused as paused

from app.ask_worker import AskWorker
from app.resource_runtime import release_credentials, resource_run
from app.work_engine import WorkEngine
from app.work_queue import WorkQueue
from app.work_worker import CapacityUnavailable
from tests.unit_tests.test_harness import ToolModel


@pytest.fixture
def acceptance(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    created = []

    def model():
        body = {"name": "independent-" + uuid4().hex, "model": "controlled", "base_url": "http://unused.invalid/v1",
                "supports_tools": True, "api_key": "independent-test-marker"}
        response = connection.post("/api/v1/admin/models", headers=ORIGIN, json=body)
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        created.append(identifier)
        return identifier, body

    yield connection, app, owner, model
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET enabled=false WHERE id=ANY(%s::text[])", (created,))


def create_agent(connection, model, **extra):
    response = connection.post("/api/v1/agents", headers=ORIGIN,
                               json={"name": "acceptance-" + uuid4().hex, "model_id": model, **extra})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    published = connection.post(f"/api/v1/agents/{identifier}/publish", headers=ORIGIN, json={"revision": 1})
    assert published.status_code == 201, published.text
    return identifier, published.json()["id"]


def complete_message(text):
    return AIMessage(content=text, usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})


def test_published_team_model_maintenance_keeps_versions_and_http_multi_model_trace(acceptance):
    connection, app, _, model = acceptance
    lead, _ = model()
    research, research_body = model()
    reviewer, _ = model()
    first_agent, first_version = create_agent(connection, research, instructions="固定研究指令-v1")
    second_agent, second_version = create_agent(connection, reviewer, instructions="固定复核指令-v1")
    parent, parent_version = create_agent(connection, lead, team=[
        {"name": "researcher", "version_id": first_version, "goal": "研究证据"},
        {"name": "reviewer", "version_id": second_version, "goal": "独立复核"},
    ])
    changed = connection.put(f"/api/v1/admin/models/{research}", headers=ORIGIN,
        json={**research_body, "revision": 1, "temperature": 0.72,
              "base_url": "http://updated-member.invalid/v2", "api_key": "rotated-independent-marker"})
    assert changed.status_code == 200, changed.text
    # The model alone changed. No parent/member publish endpoint is called after maintenance.
    actual_calls = []

    def factory(configuration):
        class Observed(ToolModel):
            async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                actual_calls.append({"model": configuration.id, "revision": configuration.revision,
                    "target": configuration.base_url, "prompt": str(messages)})
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if configuration.id == lead:
            return Observed(responses=[AIMessage(content="", tool_calls=[
                {"id": "independent-research", "name": "task",
                 "args": {"subagent_type": "researcher", "description": "提供证据"}},
                {"id": "independent-review", "name": "task",
                 "args": {"subagent_type": "reviewer", "description": "复核证据"}},
            ], usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}),
                complete_message("独立验收汇总")])
        return Observed(responses=[complete_message("受控成员证据")])

    app.state.runtime.model_factory = factory
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
        json={"agent_id": parent, "agent_version_id": parent_version}).json()["id"]
    accepted = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                               json={"model_id": lead, "message": "委派并汇总"})
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()["run_id"]
    events = connection.get(f"/api/v1/runs/{run}/events").text
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "SUCCEEDED", events
    response = connection.get(f"/api/v1/resource-runs/ask/{run}/configurations")
    assert response.status_code == 200, response.text
    trace = response.json()
    assert trace["business_version"]["published_version_id"] == parent_version
    members = {item["role"]: item for item in trace["business_version"]["members"]}
    assert members["researcher"]["version_id"] == first_version
    assert members["reviewer"]["version_id"] == second_version
    models = {item["resource_id"]: item for item in trace["items"] if item["resource_kind"] == "model"}
    assert set(models) == {lead, research, reviewer}
    for identifier, role, revision in [(lead, "lead", 1), (research, "researcher", 2), (reviewer, "reviewer", 1)]:
        item = models[identifier]
        assert item["revision"] == str(revision) and item["state"] == "used" and item["used_at"]
        assert item["calls"] and {call["role_name"] for call in item["calls"]} == {role}
        assert all(call["model_revision"] == revision for call in item["calls"])
    observed = [item for item in actual_calls if item["model"] == research]
    assert observed and all(item["revision"] == 2 and item["target"] == "http://updated-member.invalid/v2"
                            and "固定研究指令-v1" in item["prompt"] for item in observed)
    assert {item["model"] for item in actual_calls} == {lead, research, reviewer}
    for identifier in (first_agent, second_agent, parent):
        assert len(connection.get(f"/api/v1/agents/{identifier}/versions").json()["items"]) == 1
    assert "rotated-independent-marker" not in response.text and "execution_config" not in response.text


def queued_route(acceptance):
    connection, app, owner, model = acceptance
    primary, primary_body = model()
    backup, _ = model()
    identifier = str(uuid4())
    body = {"name": "route-" + uuid4().hex, "primary_model": primary, "candidates": [primary, backup],
            "failures": 1, "cooldown": 60}
    saved = connection.put(f"/api/v1/model-routes/{identifier}", headers=ORIGIN, json=body)
    assert saved.status_code == 200, saved.text
    item = next(row for row in connection.get("/api/v1/model-routes").json()["items"] if row["id"] == identifier)
    assert connection.post(f"/api/v1/model-routes/{identifier}/consent", headers=ORIGIN,
                           json={"digest": item["digest"]}).status_code == 200
    connection.portal.call(app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,failure_code,finished_at) "
        "VALUES(%s,%s,%s,'ask',%s,1,'UNKNOWN','MODEL_REQUEST_TIMEOUT',now())",
        (uuid4(), uuid4(), owner, primary))
    return identifier, body, primary, primary_body, backup


def update_queued_route(connection, identifier, body, primary, primary_body):
    result = connection.put(f"/api/v1/model-routes/{identifier}", headers=ORIGIN,
                            json={**body, "revision": 1, "failures": 2})
    assert result.status_code == 200, result.text
    saved = connection.put(f"/api/v1/admin/models/{primary}", headers=ORIGIN,
                            json={**primary_body, "revision": 1, "temperature": 0.49})
    assert saved.status_code == 200, saved.text


def test_ask_worker_queue_reselects_route_at_actual_consumer_start(acceptance, monkeypatch):
    connection, app, _, _ = acceptance
    route, body, primary, primary_body, backup = queued_route(acceptance)
    app.state.settings.ask_worker_enabled = True
    inline_calls = []
    monkeypatch.setattr(app.state.runtime, "start", lambda *args: inline_calls.append(args))
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN, json={}).json()["id"]
    queued = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                             json={"model_id": primary, "model_routing": True, "native_search": False,
                                   "message": "持久Ask队列"})
    assert queued.status_code == 202, queued.text
    run = queued.json()["run_id"]
    pending = connection.portal.call(app.state.db.fetch, "SELECT status,model_id FROM task_runs WHERE id=%s", (run,))[0]
    assert pending == {"status": "PENDING", "model_id": backup} and not inline_calls
    update_queued_route(connection, route, body, primary, primary_body)
    actual = []

    def factory(configuration):
        actual.append((configuration.id, configuration.revision, configuration.temperature))
        return ToolModel(responses=[complete_message("真实AskWorker替身完成")])
    worker = AskWorker(app.state.db, app.state.models, app.state.settings, factory)
    try:
        assert connection.portal.call(worker.tick)
        assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "SUCCEEDED"
        assert actual == [(primary, 2, 0.49)] and not inline_calls
        trace = connection.get(f"/api/v1/resource-runs/ask/{run}/configurations").json()
        strategy = next(item for item in trace["items"] if item["resource_kind"] == "route")
        assert strategy["revision"] == "2" and strategy["snapshot"]["failures"] == 2
        used = [item for item in trace["items"] if item["resource_kind"] == "model" and item["state"] == "used"]
        assert {item["resource_id"] for item in used} == {primary}
        assert all(call["model_id"] == primary and call["model_revision"] == 2
                   for item in used for call in item["calls"])
    finally:
        connection.portal.call(app.state.db.execute, "DELETE FROM ask_worker_instances WHERE id=%s", (worker.id,))


def test_work_engine_queue_reselects_before_capacity_without_real_model_or_sandbox(acceptance):
    connection, app, _, _ = acceptance
    route, body, primary, primary_body, backup = queued_route(acceptance)
    app.state.settings.work_enabled = True
    worker = uuid4()
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (worker,))
    task = connection.post("/api/v1/work-tasks", headers=ORIGIN,
                           json={"title": "Work启动边界", "goal": "仅验证首次解析"}).json()["id"]
    queued = connection.post(f"/api/v1/work-tasks/{task}/runs", headers=ORIGIN,
                             json={"model_id": primary, "model_routing": True, "revision": 1})
    assert queued.status_code == 202, queued.text
    identifier = queued.json()["run_id"]
    row = connection.portal.call(app.state.db.fetch,
                                  "SELECT status,model_id FROM work_queue_runs WHERE id=%s", (identifier,))[0]
    assert row == {"status": "PENDING", "model_id": backup}
    update_queued_route(connection, route, body, primary, primary_body)
    configurations, sandbox_requests, inference = [], [], []

    def factory(config):
        configurations.append((config.id, config.revision, config.temperature))
        class NoInference(ToolModel):
            async def _agenerate(self, *args, **kwargs):
                inference.append(True)
                raise AssertionError("No model inference is authorized in capacity-boundary acceptance")
        return NoInference(responses=[complete_message("不得被调用")])

    class CapacityOnly:
        async def create(self, *args):
            sandbox_requests.append(args)
            return None

    async def consume():
        run = await WorkQueue(app.state.db).claim()
        assert str(run["id"]) == identifier
        engine = WorkEngine(app.state.db, app.state.models, CapacityOnly(), SimpleNamespace(),
                            app.state.runtime.saver, factory)
        with pytest.raises(CapacityUnavailable):
            await engine.execute(run)
    try:
        connection.portal.call(consume)
        assert configurations == [(primary, 2, 0.49)] and len(sandbox_requests) == 1 and not inference
        response = connection.get(f"/api/v1/resource-runs/work/{identifier}/configurations")
        assert response.status_code == 200, response.text
        trace = response.json()
        route_trace = next(item for item in trace["items"] if item["resource_kind"] == "route")
        assert route_trace["revision"] == "2" and route_trace["snapshot"]["failures"] == 2
        model = next(item for item in trace["items"] if item["resource_kind"] == "model")
        assert model["resource_id"] == primary and model["revision"] == "2"
        assert model["state"] == "resolved" and model["calls"] == [] and model["used_at"] is None
    finally:
        connection.portal.call(app.state.db.execute,
                               "UPDATE work_queue_runs SET status='CANCELLED',lease_token=NULL WHERE id=%s",
                               (identifier,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_worker_instances WHERE id=%s", (worker,))


def test_cold_confirmation_records_distinct_http_phases_and_seals_the_reviewed_port(paused):
    connection, app, identifier, run = paused
    # Simulate a cold process cache, not a real process restart.
    release_credentials(run["owner_id"], "work", identifier)
    connection.portal.call(app.state.db.execute,
        "UPDATE model_configs SET base_url='http://new-approved-target:8443/v2',revision=revision+1 WHERE id='test'")
    endpoint = f"/api/v1/resource-runs/work/{identifier}/configuration-recovery"
    preview = connection.get(endpoint)
    assert preview.status_code == 200 and preview.json()["can_confirm"], preview.text
    before_trace = connection.get(f"/api/v1/resource-runs/work/{identifier}/configurations").json()
    old = next(item for item in before_trace["items"] if item["resource_kind"] == "model")
    confirmed = connection.post(endpoint, headers=ORIGIN,
        json={"expected_phase": preview.json()["phase"], "confirmation_digest": preview.json()["confirmation_digest"]})
    assert confirmed.status_code == 202 and confirmed.json()["phase"] == 2, confirmed.text
    actual = []

    async def resolved():
        async with resource_run(app.state.db, run["owner_id"], "work", UUID(identifier)):
            config = await app.state.models.get("test")
            actual.append(config.base_url)
    # Resolve the sealed phase while its current credential still belongs to the confirmed target.
    connection.portal.call(resolved)
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET base_url='http://new-approved-target:9443/v3',"
                           "revision=revision+1 WHERE id='test'")
    # A subsequent warm read keeps the approved target; a new cold cache would require another confirmation.
    connection.portal.call(resolved)
    assert actual == ["http://new-approved-target:8443/v2"] * 2
    response = connection.get(f"/api/v1/resource-runs/work/{identifier}/configurations")
    assert response.status_code == 200, response.text
    trace = response.json()
    assert [stage["phase"] for stage in trace["stages"]] == [1, 2]
    assert trace["stages"][1]["confirmed_by"] == str(run["owner_id"])
    phases = {item["phase"]: item for item in trace["items"] if item["resource_kind"] == "model"}
    assert phases[1]["snapshot"] == old["snapshot"] and phases[1]["snapshot_id"] == old["snapshot_id"]
    assert phases[2]["snapshot"]["service_target"] == "http://new-approved-target:8443"
    assert phases[2]["current"]["service_target"] == "http://new-approved-target:9443"
    assert "service_target_digest" in phases[2]["differences"] and "service_target" in phases[2]["differences"]
    assert phases[2]["calls"] == [] and phases[2]["state"] == "resolved"
    assert "execution_config" not in response.text and "test-only" not in response.text
