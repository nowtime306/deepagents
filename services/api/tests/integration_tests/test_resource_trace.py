"""Public run evidence contains actual configurations without credentials or other owners."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_actual_run_configuration_and_current_difference_are_separate(client):
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    response = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                               json={"message": "trace", "model_id": "test"})
    run = response.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
    endpoint = f"/api/v1/resource-runs/ask/{run}/configurations"
    result = connection.get(endpoint)
    assert result.status_code == 200, result.text
    assert "execution_config" not in result.text and "test-only" not in result.text
    recorded = next(item for item in result.json()["items"] if item["resource_kind"] == "model")
    assert recorded["state"] == "used" and recorded["calls"] and recorded["used_at"]
    revision = recorded["revision"]
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET temperature=0.83,revision=revision+1 WHERE id='test'")
    changed = connection.get(endpoint).json()["items"][0]
    assert changed["revision"] == revision and changed["snapshot"]["temperature"] != 0.83
    assert changed["current"]["temperature"] == 0.83 and "temperature" in changed["differences"]
    assert connection.get(f"/api/v1/resource-runs/ask/{uuid4()}/configurations").status_code == 404
    # Even an administrator cannot use another owner's run as an evidence oracle.
    other = "trace-other-" + uuid4().hex
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.portal.call(app.state.db.execute,
                           "UPDATE conversations SET owner_id=(SELECT id FROM users WHERE username=%s) WHERE id=%s",
                           (other, conversation))
    assert connection.get(endpoint).status_code == 404


def test_legacy_run_has_no_fabricated_configuration(client):
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    run = uuid4()
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES(%s,%s,'test','SUCCEEDED')",
                           (run, conversation))
    response = connection.get(f"/api/v1/resource-runs/ask/{run}/configurations")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [] and response.json()["stages"] == []


def test_queued_route_uses_policy_at_execution_start(client):
    from test_api import TestModel
    from test_model_routing import consent, setup, submit

    connection, app = client
    app.state.route_test_models = []
    owner, models, item, payload = setup(connection, app)
    consent(connection, item)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,failure_code,finished_at) "
        "VALUES(%s,%s,%s,'ask',%s,1,'UNKNOWN','MODEL_REQUEST_TIMEOUT',now())",
        (uuid4(), uuid4(), owner, models[0]),
    )
    waiting, called = [], []
    original = app.state.runtime.start
    app.state.runtime.start = lambda *arguments: waiting.append(arguments)
    app.state.runtime.model_factory = lambda config: called.append(config.id) or TestModel(responses=["started"])
    try:
        accepted = submit(connection, models[0])
        assert accepted.status_code == 202, accepted.text
        result = connection.put(f"/api/v1/model-routes/{item['id']}", headers=ORIGIN,
                                json={**payload, "revision": 1, "failures": 2})
        assert result.status_code == 200, result.text
        assert len(waiting) == 1
        connection.portal.call(app.state.runtime.run, *waiting[0])
        assert called == [models[0]]
        evidence = connection.get(
            f"/api/v1/resource-runs/ask/{accepted.json()['run_id']}/configurations",
        ).json()
        route = next(row for row in evidence["items"] if row["resource_kind"] == "route")
        assert route["revision"] == "2" and route["snapshot"]["failures"] == 2
    finally:
        app.state.runtime.start = original
        connection.portal.call(app.state.db.execute,
                               "UPDATE model_configs SET enabled=false WHERE id=ANY(%s::text[])", (models,))


def test_model_already_used_in_run_can_later_enter_route_after_maintenance(client):
    from test_model_routing import consent, setup

    from app.model_routing import resolve
    from app.resource_runtime import resource_run

    connection, app = client
    app.state.route_test_models = []
    owner, models, item, _ = setup(connection, app)
    consent(connection, item)

    async def execute():
        run = uuid4()
        async with resource_run(app.state.db, owner, "ask", run):
            first = await app.state.models.get(models[0])
            await app.state.db.execute("UPDATE model_configs SET revision=revision+1,temperature=0.75 WHERE id=%s",
                                       (models[0],))
            routed = await resolve(app.state.models, owner, run, models[0], enabled=True)
            assert routed.revision == first.revision and routed.temperature == first.temperature
    try:
        connection.portal.call(execute)
    finally:
        connection.portal.call(app.state.db.execute,
                               "UPDATE model_configs SET enabled=false WHERE id=ANY(%s::text[])", (models,))


def test_capability_only_save_records_real_revisions_and_links_actual_run(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    payload = {"name": "capability-history-" + uuid4().hex, "model": "offline", "base_url": "http://unused",
               "supports_tools": True}
    created = connection.post("/api/v1/admin/models", headers=ORIGIN, json=payload)
    model = created.json()["id"]
    saved = connection.put(f"/api/v1/admin/models/{model}", headers=ORIGIN,
                           json={**payload, "revision": 1, "supports_tools": False})
    assert saved.status_code == 200, saved.text
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    accepted = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                              json={"message": "trace capability", "model_id": model})
    run = accepted.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
    history = connection.get(f"/api/v1/resource-configurations/model/{model}/changes")
    assert history.status_code == 200, history.text
    change = next(row for row in history.json()["items"]
                  if row["changes"].get("supports_tools", {}).get("before") is True)
    assert (change["before_revision"], change["after_revision"]) == (1, 2)
    assert change["resource_name"] == payload["name"]
    assert any(item["run_id"] == run for item in change["related_runs"])


def test_child_workflow_trace_identifies_shared_parent_scope(client):
    from psycopg.types.json import Jsonb

    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    accepted = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                              json={"message": "parent", "model_id": "test"})
    parent = accepted.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{parent}/events").text
    owner = connection.get("/api/v1/auth/me").json()["id"]
    workflow, child = uuid4(), uuid4()
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO workflows(id,owner_id,name,model_id) VALUES(%s,%s,%s,'test')",
                           (workflow, owner, "child-" + uuid4().hex))
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) "
                           "VALUES(%s,%s,'SUCCEEDED','',%s)",
                           (child, workflow, Jsonb({"parent_ask_run": parent, "published_version": 1})))
    response = connection.get(f"/api/v1/resource-runs/workflow/{child}/configurations")
    assert response.status_code == 200, response.text
    assert response.json()["configuration_scope"] == {"mode": "ask", "run_id": parent}
    assert response.json()["items"][0]["used_at"]
    assert response.json()["business_version"]["published_version"] == 1
