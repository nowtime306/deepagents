"""Work Agent snapshots and fenced retrieval reuse the API's existing index."""

from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client

from app.work_queue import WorkQueue


def test_work_agent_resources_are_pinned_and_rechecked(client):
    connection, app = client
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    kb = connection.post(
        "/api/v1/capabilities/knowledge", json={"name": "任务知识-" + str(uuid4())}, headers=ORIGIN
    ).json()["id"]
    connection.post(
        f"/api/v1/capabilities/knowledge/{kb}/documents",
        files={"file": ("warranty.txt", "产品保修三年".encode())},
        headers=ORIGIN,
    )
    agent = connection.post(
        "/api/v1/agents",
        json={
            "name": "任务智能体-" + str(uuid4()),
            "model_id": "test",
            "instructions": "根据授权知识回答",
            "knowledge_ids": [kb],
        },
        headers=ORIGIN,
    ).json()["id"]
    task = connection.post(
        "/api/v1/work-tasks", json={"title": "资源授权", "goal": "查询保修期"}, headers=ORIGIN
    ).json()["id"]
    result = connection.post(
        f"/api/v1/work-tasks/{task}/runs",
        json={"revision": 1, "model_id": "not-a-model", "agent_id": agent},
        headers=ORIGIN,
    )
    assert result.status_code == 202
    run_id = result.json()["run_id"]
    run = connection.portal.call(WorkQueue(app.state.db).claim)
    assert str(run["id"]) == run_id and run["model_id"] == "test"
    assert run["agent_snapshot"]["knowledge_ids"] == [kb] and run["instructions"] == "根据授权知识回答"
    url = f"/api/v1/internal/work-runs/{run_id}/knowledge"
    body = {"token": str(run["lease_token"]), "knowledge_id": kb, "query": "保修"}
    assert connection.post(url, json={**body, "token": str(uuid4())}, headers=ORIGIN).status_code == 403
    result = connection.post(url, json=body, headers=ORIGIN)
    assert result.status_code == 200 and "保修" in result.text
    config = next(item for item in connection.get("/api/v1/agents").json()["items"] if item["id"] == agent)
    assert (
        connection.put(
            f"/api/v1/agents/{agent}",
            json={"name": config["name"], "model_id": "test", "revision": config["revision"], "knowledge_ids": []},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert connection.post(url, json=body, headers=ORIGIN).status_code == 403
    assert connection.post(url, json={**body, "knowledge_id": str(uuid4())}, headers=ORIGIN).status_code == 403
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run_id,))
    connection.delete(f"/api/v1/work-tasks/{task}", headers=ORIGIN)
