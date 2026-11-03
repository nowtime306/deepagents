"""Recipient consent, actual-model accounting and shared recovery admission."""

from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN, TestModel
from test_api import client as client

from app.model_routing import resolve


@pytest.fixture(autouse=True)
def private_models(client):
    connection, app = client
    app.state.route_test_models = []
    yield
    connection.portal.call(
        app.state.db.execute,
        "UPDATE model_configs SET enabled=false WHERE id=ANY(%s::text[])",
        (app.state.route_test_models,),
    )


def setup(connection, app, *, tools=True):
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    identifiers = []
    for index in range(2):
        result = connection.post(
            "/api/v1/admin/models",
            headers=ORIGIN,
            json={
                "name": "routing-" + uuid4().hex,
                "model": "offline",
                "base_url": f"http://receiver-{index}.invalid/v1",
                "supports_tools": tools,
                "supports_images": index == 1,
            },
        )
        assert result.status_code == 201, result.text
        identifiers.append(result.json()["id"])
        app.state.route_test_models.append(result.json()["id"])
    route = str(uuid4())
    payload = {
        "name": "本地路由验收",
        "primary_model": identifiers[0],
        "candidates": identifiers,
        "failures": 1,
        "cooldown": 10,
    }
    result = connection.put(f"/api/v1/model-routes/{route}", headers=ORIGIN, json=payload)
    assert result.status_code == 200, result.text
    item = next(r for r in connection.get("/api/v1/model-routes").json()["items"] if r["id"] == route)
    return owner, identifiers, item, payload


def consent(connection, item):
    result = connection.post(
        f"/api/v1/model-routes/{item['id']}/consent", headers=ORIGIN, json={"digest": item["digest"]}
    )
    assert result.status_code == 200, result.text


def submit(connection, model):
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN, json={"title": "路由验收"}).json()["id"]
    return connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        headers=ORIGIN,
        json={"message": "本地协议测试", "model_id": model, "model_routing": True, "native_search": False},
    )


def test_no_consent_revision_change_and_revocation_stop_before_provider(client):
    connection, app = client
    _, models, item, payload = setup(connection, app)
    called = []
    app.state.runtime.model_factory = lambda config: called.append(config.id)
    assert submit(connection, models[0]).json()["error"]["code"] == "ROUTE_CONSENT_REQUIRED"
    consent(connection, item)
    assert (
        connection.put(
            f"/api/v1/model-routes/{item['id']}", headers=ORIGIN, json={**payload, "revision": 1, "cooldown": 20}
        ).status_code
        == 200
    )
    assert submit(connection, models[0]).status_code == 403
    assert not called
    assert (
        connection.post(
            f"/api/v1/model-routes/{item['id']}/consent", headers=ORIGIN, json={"digest": item["digest"]}
        ).status_code
        == 409
    )


def test_cold_primary_selects_consented_backup_and_ledger_uses_actual_model(client):
    connection, app = client
    owner, models, item, _ = setup(connection, app)
    consent(connection, item)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,failure_code,finished_at) "
        "VALUES(%s,%s,%s,'ask',%s,1,'UNKNOWN','MODEL_REQUEST_TIMEOUT',now())",
        (uuid4(), uuid4(), owner, models[0]),
    )
    called = []

    def factory(config):
        called.append(config.id)
        return TestModel(responses=["仅本机验证"])

    app.state.runtime.model_factory = factory
    response = submit(connection, models[0])
    assert response.status_code == 202, response.text
    run = response.json()["run_id"]
    connection.get(f"/api/v1/runs/{run}/events")
    assert called == [models[1]]
    rows = connection.portal.call(
        app.state.db.fetch, "SELECT model_id,route_selection_id FROM model_usage WHERE run_id=%s", (run,)
    )
    assert rows and all(r["model_id"] == models[1] and r["route_selection_id"] for r in rows)
    selection = connection.get(f"/api/v1/model-routes/runs/{run}").json()["items"]
    assert len(selection) == 1 and selection[0]["model_id"] == models[1]
    assert selection[0]["reason"][0]["reason"] == "故障冷却中"
    conversation = connection.get(f"/api/v1/runs/{run}").json()["conversation_id"]
    assert connection.get(f"/api/v1/conversations/{conversation}/usage").json()["runs"][0]["routed"] is True


def test_unknown_capabilities_reject_and_no_cross_owner_consent(client):
    from app.manage import add_user

    connection, app = client
    _, models, item, _ = setup(connection, app, tools=False)
    consent(connection, item)
    response = submit(connection, models[0])
    assert response.status_code == 422 and response.json()["error"]["code"] == "ROUTE_NO_CANDIDATE"
    username = "route-other-" + uuid4().hex
    connection.portal.call(add_user, app.state.settings.database_url, username, "integration-password")
    assert (
        connection.post(
            "/api/v1/auth/login", headers=ORIGIN, json={"username": username, "password": "integration-password"}
        ).status_code
        == 200
    )
    assert submit(connection, models[0]).json()["error"]["code"] == "ROUTE_CONSENT_REQUIRED"


def test_expired_circuit_allows_one_probe_across_concurrent_new_runs(client):
    connection, app = client
    owner, models, item, _ = setup(connection, app)
    consent(connection, item)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,"
        "failure_code,created_at,finished_at) VALUES(%s,%s,%s,'ask',%s,1,'UNKNOWN',"
        "'MODEL_REQUEST_FAILED',now()-interval '1 minute',now()-interval '1 minute')",
        (uuid4(), uuid4(), owner, models[0]),
    )

    async def simultaneous():
        import asyncio

        return await asyncio.gather(
            *(resolve(app.state.models, UUID(owner), uuid4(), models[0], enabled=True) for _ in range(4))
        )

    results = connection.portal.call(simultaneous)
    assert sum(config.id == models[0] for config in results) == 1
    assert sum(config.id == models[1] for config in results) == 3


def test_route_recipient_metadata_never_contains_credentials(client):
    connection, app = client
    _, _, item, _ = setup(connection, app)
    assert "ciphertext" not in str(item) and "api_key" not in str(item)
    assert all(receiver["receiver"].endswith(".invalid") for receiver in item["receivers"])


def test_sent_unknown_request_is_not_replayed_to_a_fallback(client):
    import httpx
    from langchain_openai import ChatOpenAI

    connection, app = client
    _, models, item, _ = setup(connection, app)
    consent(connection, item)
    requests = []

    def failure(request):
        requests.append(request.url.host)
        raise httpx.ReadError("Local simulated response loss after request", request=request)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(failure))
    app.state.runtime.model_factory = lambda config: ChatOpenAI(
        model=config.model,
        api_key="local-test-only",
        base_url=config.base_url,
        http_async_client=transport,
        max_retries=0,
    )
    try:
        response = submit(connection, models[0])
        assert response.status_code == 202, response.text
        run = response.json()["run_id"]
        assert "run.failed" in connection.get(f"/api/v1/runs/{run}/events").text
        assert requests == ["receiver-0.invalid"]
        rows = connection.portal.call(
            app.state.db.fetch, "SELECT status,model_id FROM model_usage WHERE run_id=%s", (run,)
        )
        assert len(rows) == 1 and rows[0] == {"status": "UNKNOWN", "model_id": models[0]}
    finally:
        connection.portal.call(transport.aclose)


def test_work_admission_fixes_actual_model_and_workflow_node_uses_same_router(client):

    connection, app = client
    owner, models, item, _ = setup(connection, app)
    consent(connection, item)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,failure_code,finished_at) "
        "VALUES(%s,%s,%s,'ask',%s,1,'UNKNOWN','MODEL_REQUEST_TIMEOUT',now())",
        (uuid4(), uuid4(), owner, models[0]),
    )
    app.state.settings.work_enabled = True
    worker = uuid4()
    # Admission only: no work consumer is started; this proves the persisted selection before any model effect.
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (worker,))
    try:
        task = connection.post(
            "/api/v1/work-tasks", headers=ORIGIN, json={"title": "路由任务", "goal": "只验证入队"}
        ).json()["id"]
        result = connection.post(
            f"/api/v1/work-tasks/{task}/runs",
            headers=ORIGIN,
            json={"model_id": models[0], "model_routing": True, "revision": 1},
        )
        assert result.status_code == 202, result.text
        row = connection.portal.call(
            app.state.db.fetch,
            "SELECT model_id,agent_snapshot FROM work_queue_runs WHERE id=%s",
            (result.json()["run_id"],),
        )[0]
        assert row["model_id"] == models[1] and row["agent_snapshot"]["route_selection_id"]
        # This root is intentionally not run; remove only its queue row after checking admission.
        connection.portal.call(
            app.state.db.execute,
            "DELETE FROM work_queue_runs WHERE id=%s AND owner_id=%s",
            (result.json()["run_id"], owner),
        )
    finally:
        connection.portal.call(app.state.db.execute, "DELETE FROM work_worker_instances WHERE id=%s", (worker,))
    called = []
    app.state.runtime.model_factory = lambda config: called.append(config.id) or TestModel(responses=["路由节点完成"])
    definition = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "answer", "kind": "llm", "parameters": {"model_routing": True}},
            {"id": "end", "kind": "end", "input": "answer"},
        ],
        "edges": [{"source": "start", "target": "answer"}, {"source": "answer", "target": "end"}],
    }
    flow = connection.post(
        "/api/v1/workflows",
        headers=ORIGIN,
        json={"name": "路由工作流", "model_id": models[0], "definition": definition},
    )
    assert flow.status_code == 201, flow.text
    result = connection.post(f"/api/v1/workflows/{flow.json()['id']}/test-runs", headers=ORIGIN, json={"text": "验证"})
    assert result.status_code == 200, result.text
    assert called == [models[1]]


def test_published_team_member_route_attributes_its_actual_model_once(client):
    from langchain_core.messages import AIMessage

    from tests.unit_tests.test_harness import ToolModel

    connection, app = client
    owner, models, item, _ = setup(connection, app)
    consent(connection, item)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,failure_code,finished_at) "
        "VALUES(%s,%s,%s,'ask',%s,1,'UNKNOWN','MODEL_REQUEST_TIMEOUT',now())",
        (uuid4(), uuid4(), owner, models[0]),
    )
    child = connection.post(
        "/api/v1/agents", headers=ORIGIN, json={"name": "有路由的成员", "model_id": models[0], "model_routing": True}
    )
    assert child.status_code == 201, child.text
    version = connection.post(f"/api/v1/agents/{child.json()['id']}/publish", headers=ORIGIN, json={"revision": 1})
    assert version.status_code == 201, version.text
    root = connection.post(
        "/api/v1/agents",
        headers=ORIGIN,
        json={
            "name": "路由团队",
            "model_id": "test",
            "team": [{"name": "helper", "version_id": version.json()["id"], "goal": "只读核对"}],
        },
    )
    assert root.status_code == 201, root.text
    usage = {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}

    def factory(config):
        if config.id == "test":
            return ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "delegate",
                                "name": "task",
                                "args": {"subagent_type": "helper", "description": "核对"},
                            }
                        ],
                        usage_metadata=usage,
                    ),
                    AIMessage(content="完成", usage_metadata=usage),
                ]
            )
        assert config.id == models[1]
        return ToolModel(responses=[AIMessage(content="成员已核对", usage_metadata=usage)])

    app.state.runtime.model_factory = factory
    conversation = connection.post(
        "/api/v1/conversations", headers=ORIGIN, json={"title": "团队路由验证", "agent_id": root.json()["id"]}
    ).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        headers=ORIGIN,
        json={"model_id": "test", "message": "请委派核对", "native_search": False},
    )
    assert run.status_code == 202, run.text
    identifier = run.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{identifier}/events").text
    rows = connection.portal.call(
        app.state.db.fetch,
        "SELECT model_id,route_selection_id FROM model_usage WHERE run_id=%s AND role_name='helper'",
        (identifier,),
    )
    assert len(rows) == 1 and rows[0]["model_id"] == models[1] and rows[0]["route_selection_id"]


def test_removed_fallback_remains_editable_and_actual_selection_history_survives_model_removal(client):
    connection, app = client
    _, models, item, _ = setup(connection, app)
    consent(connection, item)
    result = submit(connection, models[0])
    run = result.json()["run_id"]
    connection.get(f"/api/v1/runs/{run}/events")
    assert connection.delete(f"/api/v1/admin/models/{models[1]}", headers=ORIGIN).status_code == 200
    listing = connection.get("/api/v1/model-routes")
    assert listing.status_code == 200
    broken = next(r for r in listing.json()["items"] if r["id"] == item["id"])
    assert broken["unavailable"] and broken["receivers"][1]["name"] == "模型已删除"
    assert connection.delete(f"/api/v1/admin/models/{models[0]}", headers=ORIGIN).status_code == 200
    history = connection.get(f"/api/v1/model-routes/runs/{run}")
    assert history.status_code == 200 and history.json()["items"][0]["model_id"] == models[0]


def test_chat_only_callers_skip_vision_while_picture_questions_can_use_it(client):
    from app.models import ModelInput

    connection, app = client
    owner, models, item, _ = setup(connection, app)
    row = next(m for m in connection.get("/api/v1/admin/models").json()["items"] if m["id"] == models[0])
    edited = {key: value for key, value in row.items() if key in ModelInput.model_fields}
    edited["kind"] = "vision"
    edited["supports_images"] = True
    assert connection.put(f"/api/v1/admin/models/{models[0]}", headers=ORIGIN, json=edited).status_code == 200
    current = next(r for r in connection.get("/api/v1/model-routes").json()["items"] if r["id"] == item["id"])
    consent(connection, current)

    async def choose():
        chat = await resolve(app.state.models, UUID(owner), uuid4(), models[0], enabled=True, tools=True)
        image = await resolve(
            app.state.models, UUID(owner), uuid4(), models[0], enabled=True, tools=True, allow_vision=True, images=True
        )
        return chat, image

    chat, image = connection.portal.call(choose)
    assert chat.id == models[1] and image.id == models[0]
