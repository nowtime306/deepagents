"""Private memory scope, explicit grants and revocation across runtime tools."""

import json
from uuid import UUID, uuid4

import pytest
from test_api import DATABASE, ORIGIN, run_async
from test_api import client as client

from app.errors import APIError
from app.manage import add_user


def test_memory_scope_and_immediate_revocation(client):
    connection, app = client
    binding = connection.get("/api/v1/capabilities/bindings").json()
    assert binding["memory_enabled"] is False
    identifier = connection.post(
        "/api/v1/memories", json={"title": "工作偏好", "content": "文档使用中文"}, headers=ORIGIN
    ).json()["id"]
    agent = connection.post(
        "/api/v1/agents", json={"name": "记忆助手", "model_id": "test", "memory_enabled": True}, headers=ORIGIN
    ).json()["id"]
    snapshot = next(row for row in connection.get("/api/v1/agents").json()["items"] if row["id"] == agent)
    owner = connection.portal.call(app.state.db.fetch, "SELECT owner_id FROM agents WHERE id=%s", (agent,))[0][
        "owner_id"
    ]
    tools, _ = connection.portal.call(app.state.capabilities.for_owner, owner)
    assert not any(tool.name == "search_memories" for tool in tools)
    tools, _ = connection.portal.call(app.state.capabilities.for_owner, owner, snapshot)
    tool = next(tool for tool in tools if tool.name == "search_memories")
    result = json.loads(connection.portal.call(tool.ainvoke, {"query": "中文"}))
    assert result[0]["content"] == "文档使用中文"
    assert (
        connection.put(
            f"/api/v1/memories/{identifier}", json={"title": "偏好", "content": "英文", "revision": 2}, headers=ORIGIN
        ).status_code
        == 409
    )
    assert (
        connection.put(
            f"/api/v1/memories/{identifier}",
            json={"title": "偏好", "content": "中文", "revision": 1, "enabled": False},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert json.loads(connection.portal.call(tool.ainvoke, {"query": "中文"})) == []
    conversation = connection.post(
        "/api/v1/conversations", json={"title": "记忆快照", "agent_id": agent}, headers=ORIGIN
    ).json()["id"]
    started = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"model_id": "test", "message": "验证记忆授权"},
        headers=ORIGIN,
    )
    assert started.status_code == 202
    connection.get(f"/api/v1/runs/{started.json()['run_id']}/events")
    saved = connection.portal.call(
        app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (started.json()["run_id"],)
    )[0]
    assert saved["agent_snapshot"]["memory_enabled"] is True
    snapshot["memory_enabled"] = False
    assert (
        connection.put(
            f"/api/v1/agents/{agent}",
            json={key: snapshot[key] for key in ("name", "model_id", "revision", "memory_enabled")},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    with pytest.raises(APIError, match="长期记忆授权已撤销"):
        connection.portal.call(tool.ainvoke, {"query": "中文"})
    # Old clients omit the new flag: their unrelated selection update must preserve it.
    connection.put("/api/v1/capabilities/bindings", json={**binding, "memory_enabled": True}, headers=ORIGIN)
    connection.put(
        "/api/v1/capabilities/bindings",
        json={key: binding[key] for key in ("knowledge_ids", "skill_ids", "mcp_ids")},
        headers=ORIGIN,
    )
    assert connection.get("/api/v1/capabilities/bindings").json()["memory_enabled"]
    other = "other-memory-" + str(uuid4())
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get("/api/v1/memories").json()["items"] == []
    assert connection.delete(f"/api/v1/memories/{identifier}", headers=ORIGIN).status_code == 404
    assert (
        connection.put(
            f"/api/v1/memories/{identifier}",
            json={"title": "改写", "content": "不可越权", "revision": 2},
            headers=ORIGIN,
        ).status_code
        == 409
    )
    tools, _ = connection.portal.call(app.state.capabilities.for_owner, UUID(str(uuid4())))
    assert not any(tool.name == "search_memories" for tool in tools)
