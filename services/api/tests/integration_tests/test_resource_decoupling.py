"""Published business versions survive model maintenance in an isolated database."""

from test_api import ORIGIN, TestModel
from test_api import client as client
from test_automations import schedule, version
from test_workflow_version_control import seeded


def test_automation_old_published_agent_uses_current_model_and_live_disablement(client):
    connection, app = client
    published = version(connection)
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET revision=revision+1,temperature=0.7 WHERE id='test'")
    identifier = schedule(connection, agent_version_id=published)
    response = connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id='test'")
    response = connection.post("/api/v1/batches", headers=ORIGIN,
                               json={"name": "停用反例", "agent_version_id": published, "goals": ["must fail"]})
    assert response.status_code == 422, response.text
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": False}, headers=ORIGIN)


def test_workflow_v1_restores_original_logic_after_model_maintenance(client):
    connection, app = client
    _, prefix, _, _ = seeded(connection)
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET revision=revision+1,temperature=0.8 WHERE id='test'")
    restored = connection.post(prefix + "/versions/1/set-current", headers=ORIGIN, json={"release_revision": 3})
    assert restored.status_code == 200, restored.text
    first = connection.post(prefix + "/versions/1/runs", headers=ORIGIN, json={"text": "Hello"})
    second = connection.post(prefix + "/versions/2/runs", headers=ORIGIN, json={"text": "Hello"})
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.json()["output"] == "HELLO" and second.json()["output"] == "hello"


def test_two_model_calls_keep_actual_snapshot_during_update_and_next_run_uses_latest(client):
    connection, app = client
    old = connection.portal.call(
        app.state.db.fetch, "SELECT revision FROM model_configs WHERE id='test'"
    )[0]["revision"]
    configured, updated = [], []

    class UpdatingModel(TestModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            if not updated:
                updated.append(True)
                await app.state.db.execute(
                    "UPDATE model_configs SET revision=revision+1,temperature=0.9,base_url='http://new-target' "
                    "WHERE id='test'"
                )
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def factory(config):
        configured.append((config.revision, config.base_url))
        return UpdatingModel(responses=["controlled answer"])

    app.state.runtime.model_factory = factory
    definition = {
        "nodes": [{"id": "start", "kind": "start"}, {"id": "first", "kind": "llm"},
                  {"id": "second", "kind": "llm", "input": "first"},
                  {"id": "end", "kind": "end", "input": "second"}],
        "edges": [{"source": "start", "target": "first"}, {"source": "first", "target": "second"},
                  {"source": "second", "target": "end"}],
    }
    created = connection.post("/api/v1/workflows", headers=ORIGIN,
                              json={"name": "temporal", "model_id": "test", "definition": definition})
    assert created.status_code == 201, created.text
    endpoint = f"/api/v1/workflows/{created.json()['id']}/test-runs"
    first = connection.post(endpoint, headers=ORIGIN, json={"text": "test"})
    assert first.status_code == 200, first.text
    assert [item[0] for item in configured] == [old, old]
    assert configured[0][1] == configured[1][1]
    second = connection.post(endpoint, headers=ORIGIN, json={"text": "next"})
    assert second.status_code == 200, second.text
    assert configured[2:] == [(old + 1, "http://new-target")] * 2
    usage = connection.portal.call(app.state.db.fetch,
        "SELECT u.model_revision,s.revision,s.used_at,s.snapshot_id FROM model_usage u "
        "JOIN run_resource_snapshots s ON s.snapshot_id=u.resource_snapshot_id WHERE u.run_id=%s",
        (first.json()["id"],))
    assert len(usage) == 2
    assert all(row["model_revision"] == old and row["revision"] == str(old) and row["used_at"] for row in usage)
    assert len({row["snapshot_id"] for row in usage}) == 1


def test_unknown_tool_capability_keeps_plain_chat_available(client):
    connection, app = client
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_capability_declarations SET supports_tools=NULL WHERE model_id='test'")
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    response = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                               json={"message": "plain chat", "model_id": "test"})
    assert response.status_code == 202, response.text
    events = connection.get(f"/api/v1/runs/{response.json()['run_id']}/events").text
    assert "run.completed" in events and "MODEL_TOOLS_UNVERIFIED" not in events
    knowledge = connection.post("/api/v1/capabilities/knowledge", headers=ORIGIN,
                                json={"name": "needs tools"}).json()["id"]
    agent = connection.post("/api/v1/agents", headers=ORIGIN,
                            json={"name": "needs knowledge", "model_id": "test", "knowledge_ids": [knowledge]})
    assert agent.status_code == 201, agent.text
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
                                   json={"agent_id": agent.json()["id"]}).json()["id"]
    response = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                               json={"message": "must verify tools", "model_id": "test"})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert "run.failed" in connection.get(f"/api/v1/runs/{run_id}/events").text
    stored = connection.portal.call(app.state.db.fetch,
        "SELECT error_code,(SELECT count(*) FROM model_usage WHERE run_id=%s) AS calls "
        "FROM task_runs WHERE id=%s", (run_id, run_id))[0]
    assert stored["error_code"] == "MODEL_TOOLS_UNVERIFIED" and stored["calls"] == 0
