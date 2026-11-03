"""Agent ownership, saved configuration and run snapshot."""

from uuid import uuid4

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_agent_configuration_is_owned_and_snapshotted(client) -> None:
    connection, app = client
    name = f"审查助手-{uuid4()}"
    body = {"name": name, "model_id": "test", "instructions": "始终先列出验证结果"}
    created = connection.post("/api/v1/agents", json=body, headers=ORIGIN)
    assert created.status_code == 201
    agent = created.json()["id"]
    conversation = connection.post("/api/v1/conversations", json={"title": "审查", "agent_id": agent}, headers=ORIGIN)
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]
    assert connection.get(f"/api/v1/conversations/{conversation_id}/messages").json()["agent_id"] == agent
    started = connection.post(
        f"/api/v1/conversations/{conversation_id}/runs", json={"message": "请审查", "model_id": "test"}, headers=ORIGIN
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run_id}/events").text
    saved = connection.portal.call(app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (run_id,))[
        0
    ]["agent_snapshot"]
    assert saved["id"] == agent and saved["instructions"] == body["instructions"] and saved["revision"] == 1
    updated = {**body, "instructions": "新的指令", "revision": 1}
    assert connection.put(f"/api/v1/agents/{agent}", json=updated, headers=ORIGIN).status_code == 200
    assert connection.put(f"/api/v1/agents/{agent}", json=updated, headers=ORIGIN).status_code == 409
    assert (
        connection.portal.call(app.state.db.fetch, "SELECT agent_snapshot FROM task_runs WHERE id=%s", (run_id,))[0][
            "agent_snapshot"
        ]
        == saved
    )
    other = f"other-agent-{uuid4()}"
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert agent not in {item["id"] for item in connection.get("/api/v1/agents").json()["items"]}
    assert (
        connection.post("/api/v1/conversations", json={"title": "越权", "agent_id": agent}, headers=ORIGIN).status_code
        == 404
    )
    assert connection.put(f"/api/v1/agents/{agent}", json=updated, headers=ORIGIN).status_code == 404


def test_search_settings_require_admin_and_never_return_key(client) -> None:
    connection, app = client
    assert connection.get("/api/v1/search-settings").status_code == 403
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    try:
        saved = connection.put(
            "/api/v1/search-settings", json={"api_key": "test-search-secret", "enabled": True}, headers=ORIGIN
        )
        assert saved.status_code == 200
        listing = connection.get("/api/v1/search-settings")
        assert listing.json() == {"enabled": True, "key_configured": True}
        assert "test-search-secret" not in listing.text
        assert connection.put("/api/v1/search-settings", json={"enabled": True}, headers=ORIGIN).status_code == 200
        encrypted = connection.portal.call(app.state.db.fetch, "SELECT api_key_ciphertext FROM search_settings")[0][
            "api_key_ciphertext"
        ]
        assert "test-search-secret" not in encrypted
        assert app.state.models.vault.decrypt(encrypted).get_secret_value() == "test-search-secret"
    finally:
        connection.portal.call(app.state.db.execute, "DELETE FROM search_settings WHERE singleton=true")


def test_agent_graph_calls_web_search_and_persists_source(client, monkeypatch) -> None:
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    assert (
        connection.put(
            "/api/v1/search-settings", json={"api_key": "test-secret", "enabled": True}, headers=ORIGIN
        ).status_code
        == 200
    )
    factory = httpx.AsyncClient

    def response(request):
        assert request.url.path == "/search"
        return httpx.Response(
            200, json={"results": [{"title": "网页", "url": "https://example.com/source", "content": "公开资料"}]}
        )

    monkeypatch.setattr(
        "app.web_search.httpx.AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(response))
    )

    class SearchModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    app.state.runtime.model_factory = lambda _: SearchModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "search_web", "args": {"query": "最新消息"}, "id": "search-1"}]),
            AIMessage(content="已经查到来源。"),
        ]
    )
    try:
        agent = connection.post(
            "/api/v1/agents",
            json={"name": f"联网测试-{uuid4()}", "model_id": "test", "web_enabled": True},
            headers=ORIGIN,
        ).json()["id"]
        conversation = connection.post(
            "/api/v1/conversations", json={"title": "联网", "agent_id": agent}, headers=ORIGIN
        ).json()["id"]
        run = connection.post(
            f"/api/v1/conversations/{conversation}/runs",
            json={"message": "查询最新消息", "model_id": "test"},
            headers=ORIGIN,
        ).json()["run_id"]
        assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
        messages = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
        assert any(item["role"] == "tool" and "https://example.com/source" in str(item["content"]) for item in messages)
    finally:
        connection.portal.call(app.state.db.execute, "DELETE FROM search_settings WHERE singleton=true")
