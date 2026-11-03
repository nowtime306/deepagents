"""Immutable Agent releases remain explicit in Ask and Work and obey live revocation."""

from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client


def test_agent_publication_ask_work_and_revision_guards(client):
    connection, app = client
    body = {"name": "版本助手-" + str(uuid4()), "model_id": "test", "instructions": "v1 固定指令"}
    agent = connection.post("/api/v1/agents", json=body, headers=ORIGIN).json()["id"]
    prefix = f"/api/v1/agents/{agent}"
    published = connection.post(prefix + "/publish", json={"revision": 1}, headers=ORIGIN)
    assert published.status_code == 201
    version = published.json()["id"]
    assert published.json()["version"] == 1
    assert (
        connection.put(prefix, json={**body, "instructions": "v2 草稿指令", "revision": 1}, headers=ORIGIN).status_code
        == 200
    )
    conversation = connection.post(
        "/api/v1/conversations", json={"agent_id": agent, "agent_version_id": version}, headers=ORIGIN
    )
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation_id}/runs", json={"message": "hello", "model_id": "test"}, headers=ORIGIN
    )
    assert run.status_code == 202
    run_id = run.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run_id}/events").text
    row = connection.portal.call(app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (run_id,))[0]
    assert row["agent_snapshot"]["instructions"] == "v1 固定指令"
    assert row["agent_snapshot"]["published_version_id"] == version
    assert connection.get(f"/api/v1/conversations/{conversation_id}/messages").json()["agent_version_id"] == version
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post("/api/v1/work-tasks", json={"title": "版本任务", "goal": "test"}, headers=ORIGIN).json()[
        "id"
    ]
    work = connection.post(
        f"/api/v1/work-tasks/{task}/runs",
        json={"revision": 1, "model_id": "test", "agent_id": agent, "agent_version_id": version},
        headers=ORIGIN,
    )
    assert work.status_code == 202
    work_id = work.json()["run_id"]
    row = connection.portal.call(
        app.state.db.fetch, "SELECT agent_snapshot FROM work_queue_runs WHERE id=%s", (work_id,)
    )[0]
    assert row["agent_snapshot"]["instructions"] == "v1 固定指令"
    assert connection.post(prefix + "/publish", json={"revision": 1}, headers=ORIGIN).status_code == 409
    assert (
        connection.put(prefix + f"/versions/{version}/state", json={"active": False}, headers=ORIGIN).status_code == 200
    )
    assert (
        connection.post(
            "/api/v1/conversations", json={"agent_id": agent, "agent_version_id": version}, headers=ORIGIN
        ).status_code
        == 403
    )
    connection.put(prefix + f"/versions/{version}/state", json={"active": True}, headers=ORIGIN)
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET revision=revision+1 WHERE id='test'")
    assert (
        connection.post(
            "/api/v1/conversations", json={"agent_id": agent, "agent_version_id": version}, headers=ORIGIN
        ).status_code
        == 201
    )
    assert connection.get(f"/api/v1/agents/{uuid4()}/versions").json()["items"] == []
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (work_id,))
    connection.delete(f"/api/v1/work-tasks/{task}", headers=ORIGIN)
    connection.delete(f"/api/v1/conversations/{conversation_id}", headers=ORIGIN)
    connection.delete(prefix, headers=ORIGIN)
