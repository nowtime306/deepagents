"""Work can call only pinned granted versions and pays nested model budget."""

import time
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from test_api import ORIGIN
from test_api import client as client
from test_external_workflows import setup_version

from app.work_queue import WorkQueue


def test_work_calls_granted_version_with_parent_budget(client):
    connection, app = client
    _, version = setup_version(connection)
    app.state.runtime.model_factory = lambda _: FakeMessagesListChatModel(
        responses=[
            AIMessage(content="子流程结果", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        ]
    )
    agent = connection.post(
        "/api/v1/agents",
        json={
            "name": "工作流助手",
            "model_id": "test",
            "instructions": "任务专用指令",
            "memory_enabled": True,
            "workflow_version_ids": [version],
        },
        headers=ORIGIN,
    ).json()["id"]
    task = connection.post("/api/v1/work-tasks", json={"title": "子流程预算", "goal": "验证"}, headers=ORIGIN).json()[
        "id"
    ]
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    started = connection.post(
        f"/api/v1/work-tasks/{task}/runs", json={"revision": 1, "model_id": "test", "agent_id": agent}, headers=ORIGIN
    )
    assert started.status_code == 202
    run = connection.portal.call(WorkQueue(app.state.db).claim)
    assert run["agent_snapshot"]["memory_enabled"] and run["instructions"] == "任务专用指令"
    url = f"/api/v1/internal/work-runs/{run['id']}/workflows"
    body = {"token": str(run["lease_token"]), "version_id": version, "text": "请运行"}
    result = connection.post(url, json=body, headers=ORIGIN)
    assert result.status_code == 202
    child = result.json()["id"]
    assert connection.post(url, json=body, headers=ORIGIN).json()["id"] == child
    for _ in range(100):
        response = connection.post(f"{url}/{child}/status", json={"token": body["token"]}, headers=ORIGIN).json()
        if response.get("status") not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.03)
    assert response["status"] == "SUCCEEDED" and response["output"] == "子流程结果"
    row = connection.portal.call(
        app.state.db.fetch, "SELECT model_calls,used_tokens FROM work_queue_runs WHERE id=%s", (run["id"],)
    )[0]
    assert row["model_calls"] == 1 and row["used_tokens"] == 15
    usage = connection.get(f"/api/v1/work-runs/{run['id']}/usage").json()["total"]
    assert usage["calls"] == usage["reported_calls"] == 1
    assert usage["input_tokens"] == 10 and usage["output_tokens"] == 5
    assert usage["unknown_calls"] == 0
    diagnostics = connection.get(f"/api/v1/work-runs/{run['id']}/diagnostics").json()
    assert diagnostics["budget"]["reported_tokens"] == 15
    assert len(diagnostics["calls"]) == 1 and diagnostics["calls"][0]["run_id"] == str(run["id"])
    config = connection.get("/api/v1/agents").json()["items"][0]
    connection.put(
        f"/api/v1/agents/{agent}",
        json={"name": config["name"], "model_id": "test", "revision": config["revision"], "workflow_version_ids": []},
        headers=ORIGIN,
    )
    assert connection.post(url, json=body, headers=ORIGIN).status_code == 403
    assert connection.post(f"{url}/{child}/status", json={"token": body["token"]}, headers=ORIGIN).status_code == 404
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run["id"],))
