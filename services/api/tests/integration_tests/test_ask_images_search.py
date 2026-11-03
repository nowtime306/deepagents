"""Images and provider-owned search reach Ask without exposing credentials to the browser."""

import base64
import io
import json
from uuid import uuid4

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from PIL import Image
from test_api import ORIGIN
from test_api import client as client


def png():
    stream = io.BytesIO()
    with Image.new("RGB", (4, 4), "white") as image:
        image.save(stream, "PNG")
    return stream.getvalue()


def model(connection, app, *, images=True, deepseek=False):
    identifier = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,supports_images) "
        "VALUES(%s,%s,%s,%s,%s,%s)",
        (
            identifier,
            identifier,
            "deepseek-flash" if deepseek else "test",
            "https://api.deepseek.com/v1" if deepseek else "http://unused",
            app.state.models.vault.encrypt("provider-test-key"),
            None if deepseek else images,
        ),
    )
    return identifier


def test_image_upload_real_content_history_and_model_selection(client):
    connection, app = client
    selected = model(connection, app)
    conversation = connection.post("/api/v1/conversations", json={"title": "图片问答"}, headers=ORIGIN).json()["id"]
    raw = png()
    uploaded = connection.post(
        f"/api/v1/conversations/{conversation}/attachments?name=screen.png", content=raw, headers=ORIGIN
    )
    assert uploaded.status_code == 201
    attachment = uploaded.json()["id"]
    assert uploaded.json()["media_type"] == "image/png"
    preview = connection.get(f"/api/v1/attachments/{attachment}/download?inline=true")
    assert preview.headers["content-type"] == "image/png" and preview.headers["content-disposition"].startswith(
        "inline;"
    )
    assert preview.content == raw
    assert (
        connection.post(
            f"/api/v1/conversations/{conversation}/runs",
            json={"message": "看图", "model_id": "test", "attachment_ids": [attachment]},
            headers=ORIGIN,
        ).status_code
        == 422
    )
    response = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"message": "描述图片", "model_id": selected, "attachment_ids": [attachment], "native_search": False},
        headers=ORIGIN,
    )
    assert response.status_code == 202
    assert "run.completed" in connection.get("/api/v1/runs/" + response.json()["run_id"] + "/events").text
    checkpoint = connection.portal.call(
        app.state.runtime.saver.aget_tuple, {"configurable": {"thread_id": conversation}}
    )
    human = next(item for item in checkpoint.checkpoint["channel_values"]["messages"] if item.type == "human")
    assert human.content[1]["type"] == "image_url"
    assert human.content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(raw).decode()
    history = connection.get(f"/api/v1/conversations/{conversation}/messages").json()
    assert history["items"][0]["content"]["attachments"][0]["media_type"] == "image/png"
    assert not history["native_search"]
    assert (
        connection.post(
            f"/api/v1/conversations/{conversation}/runs", json={"message": "继续", "model_id": "test"}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        connection.post(
            f"/api/v1/conversations/{conversation}/attachments?name=bad.png", content=b"not-image", headers=ORIGIN
        ).status_code
        == 422
    )
    connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN)
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (selected,))


def test_flash_provider_search_defaults_on_and_sends_provider_request(client, monkeypatch):
    connection, app = client
    selected = model(connection, app, deepseek=True)
    metadata = next(item for item in connection.get("/api/v1/models").json()["items"] if item["id"] == selected)
    assert metadata["supports_images"] and metadata["native_search_mode"] == "deepseek_search"
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer provider-test-key"
        return httpx.Response(
            200, json={"results": [{"title": "实时来源", "url": "https://example.org/news", "summary": "检索证据"}]}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.native_search.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )

    class Tools(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    app.state.runtime.model_factory = lambda _: Tools(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "search", "name": "search_web", "args": {"query": "今天新闻"}}]),
            AIMessage(content="已搜索，来源 https://example.org/news"),
        ]
    )
    conversation = connection.post("/api/v1/conversations", json={"title": "官方搜索"}, headers=ORIGIN).json()["id"]
    response = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"message": "今天新闻", "model_id": selected}, headers=ORIGIN
    )
    assert response.status_code == 202
    assert "run.completed" in connection.get("/api/v1/runs/" + response.json()["run_id"] + "/events").text
    assert len(requests) == 1 and str(requests[0].url) == "https://api.deepseek.com/v1/tools/search"
    assert json.loads(requests[0].content) == {"query": "今天新闻", "count": 5, "need_content": False}
    assert "provider-test-key" not in connection.get(f"/api/v1/conversations/{conversation}/messages").text
    requests.clear()
    app.state.runtime.model_factory = lambda _: Tools(responses=[AIMessage(content="已关闭联网")])
    response = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"message": "关闭", "model_id": selected, "native_search": False},
        headers=ORIGIN,
    )
    assert "run.completed" in connection.get("/api/v1/runs/" + response.json()["run_id"] + "/events").text
    assert requests == []
    connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN)
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (selected,))


def test_image_capability_override_survives_older_client_edits(client):
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    body = {"name": "视觉配置-" + str(uuid4()), "model": "test", "base_url": "http://unused", "supports_images": True}
    created = connection.post("/api/v1/admin/models", json=body, headers=ORIGIN)
    assert created.status_code == 201
    identifier = created.json()["id"]
    try:
        row = next(item for item in connection.get("/api/v1/admin/models").json()["items"] if item["id"] == identifier)
        assert row["supports_images"] is True
        body.pop("supports_images")
        assert (
            connection.put(
                "/api/v1/admin/models/" + identifier, json={**body, "revision": row["revision"]}, headers=ORIGIN
            ).status_code
            == 200
        )
        assert connection.portal.call(app.state.models.get, identifier).supports_images is True
    finally:
        connection.delete("/api/v1/admin/models/" + identifier, headers=ORIGIN)
