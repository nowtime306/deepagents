"""Real PG/API serialization of A01's two distinct revision contracts; no automation task."""

from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from test_api import ORIGIN
from test_api import client as client


class KnownModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "a01-revision-contract-controlled"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="固定契约响应", usage_metadata={
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}))])


def test_maintained_model_snapshot_reference_is_text_and_actual_usage_revision_is_integer(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    config = {"name": "A01 projection " + uuid4().hex, "model": "offline-a01-contract", "base_url": "http://unused",
              "protocol": "openai", "kind": "chat", "supports_tools": True, "temperature": 0.1}
    created = connection.post("/api/v1/admin/models", headers=ORIGIN, json=config)
    assert created.status_code == 201, created.text
    model = created.json()["id"]
    created_agent = connection.post("/api/v1/agents", headers=ORIGIN, json={
        "name": "A01 original publication", "model_id": model, "instructions": "只返回固定契约响应。",
        "knowledge_ids": [], "mcp_ids": [], "skill_ids": [], "workflow_version_ids": [], "team": [],
        "memory_enabled": False, "web_enabled": False})
    assert created_agent.status_code == 201, created_agent.text
    agent = created_agent.json()["id"]
    published = connection.post(f"/api/v1/agents/{agent}/publish", headers=ORIGIN, json={"revision": 1})
    assert published.status_code == 201, published.text
    version = published.json()["id"]
    maintained = connection.put(f"/api/v1/admin/models/{model}", headers=ORIGIN,
                                json={**config, "temperature": 0.4, "revision": 1})
    assert maintained.status_code == 200, maintained.text
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
        json={"agent_id": agent, "agent_version_id": version}).json()["id"]
    app.state.runtime.model_factory = lambda _: KnownModel()
    submitted = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
                               json={"message": "验证修订投影", "model_id": model})
    assert submitted.status_code == 202, submitted.text
    run = submitted.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
    response = connection.get(f"/api/v1/resource-runs/ask/{run}/configurations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_version"]["published_version_id"] == version
    assert body["business_version"]["published_version"] == 1
    snapshot = next(row for row in body["items"] if row["resource_kind"] == "model" and row["resource_id"] == model)
    assert type(snapshot["revision"]) is str and snapshot["revision"] == "2"
    assert snapshot["used_at"] and snapshot["snapshot"]["temperature"] == 0.4
    assert len(snapshot["calls"]) == 1
    call = snapshot["calls"][0]
    assert type(call["model_revision"]) is int and call["model_revision"] == 2
    assert call["status"] == "KNOWN" and (call["input_tokens"], call["output_tokens"]) == (10, 5)
    database = connection.portal.call(app.state.db.fetch,
        "SELECT pg_typeof(revision)::text AS storage_type,revision FROM run_resource_snapshots WHERE snapshot_id=%s",
        (snapshot["snapshot_id"],))[0]
    assert database == {"storage_type": "text", "revision": "2"}
