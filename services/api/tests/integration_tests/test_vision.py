"""Image understanding protocol and access validation."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_api import ORIGIN
from test_api import client as client

from app.config import ModelConfig
from app.vision_api import understand_image


@pytest.mark.asyncio
async def test_vision_protocol(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "一张图片"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.vision_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x",
        name="测试",
        model="vision",
        base_url="https://example.test/v1",
        api_key=SecretStr("secret"),
        kind="vision",
    )
    assert await understand_image(config, "看到了什么", b"image", "image/png") == "一张图片"
    request = requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/v1/chat/completions" and request.headers["authorization"] == "Bearer secret"
    assert body["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64,aW1hZ2U="


def test_vision_model_auth_and_input_validation(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "vision", "http://unused", "", "vision", "openai"),
    )

    async def fake(config, question, data, media_type):
        assert media_type == "image/png" and question == "是什么"
        return "分析完成"

    monkeypatch.setattr("app.vision_api.understand_image", fake)
    assert model_id in {item["id"] for item in connection.get("/api/v1/vision/models").json()["items"]}
    data = {"model_id": model_id, "question": "是什么"}
    assert (
        connection.post("/api/v1/vision", data=data, files={"file": ("a.png", b"bad")}, headers=ORIGIN).status_code
        == 422
    )
    valid = {"file": ("a.png", b"\x89PNG\r\n\x1a\nexample")}
    assert (
        connection.post("/api/v1/vision", data={**data, "model_id": "test"}, files=valid, headers=ORIGIN).status_code
        == 422
    )
    assert connection.post("/api/v1/vision", data=data, files=valid, headers=ORIGIN).json() == {"text": "分析完成"}
    connection.cookies.clear()
    assert connection.post("/api/v1/vision", data=data, files=valid, headers=ORIGIN).status_code == 401
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))


@pytest.mark.asyncio
async def test_generation_protocol_and_url_only_rejected(monkeypatch):
    import base64

    from app.vision_api import generate_image

    png = b"\x89PNG\r\n\x1a\nexample"
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.vision_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x", name="测试", model="image-model", base_url="https://example.test/v1", kind="image_generation"
    )
    assert await generate_image(config, "一只猫") == png
    assert requests[0].url.path == "/v1/images/generations"
    assert json.loads(requests[0].content) == {"model": "image-model", "prompt": "一只猫", "n": 1}

    async def url_handler(request):
        return httpx.Response(200, json={"data": [{"url": "https://example.test/image"}]})

    monkeypatch.setattr(
        "app.vision_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(url_handler))
    )
    with pytest.raises(ValueError, match="base64"):
        await generate_image(config, "一只猫")


def test_generated_image_ownership(client, monkeypatch):
    from test_api import DATABASE, add_user, run_async

    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "image", "http://unused", "", "image_generation", "openai"),
    )

    async def fake(config, prompt):
        return b"\x89PNG\r\n\x1a\nexample"

    monkeypatch.setattr("app.vision_api.generate_image", fake)
    assert model_id in {item["id"] for item in connection.get("/api/v1/vision/generation-models").json()["items"]}
    response = connection.post(
        "/api/v1/vision/generated", json={"model_id": model_id, "prompt": "一只猫"}, headers=ORIGIN
    )
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert connection.get(f"/api/v1/vision/generated/{identifier}/image").content == b"\x89PNG\r\n\x1a\nexample"
    name = f"image-other-{uuid4()}"
    run_async(add_user(DATABASE, name, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"/api/v1/vision/generated/{identifier}/image").status_code == 404
    assert connection.delete(f"/api/v1/vision/generated/{identifier}", headers=ORIGIN).status_code == 404
    connection.portal.call(app.state.db.execute, "DELETE FROM generated_images WHERE id=%s", (identifier,))
    (app.state.settings.data_dir / "images" / f"{identifier}.png").unlink()
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))


@pytest.mark.asyncio
async def test_video_protocol(monkeypatch):
    from app.vision_api import understand_video

    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "视频内容"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.vision_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x", name="测试", model="video", base_url="https://example.test/v1", kind="video", provider="dashscope"
    )
    assert await understand_video(config, "是什么", b"video") == "视频内容"
    request = requests[0]
    assert request.url.path == "/v1/chat/completions"
    assert (
        json.loads(request.content)["messages"][0]["content"][0]["video_url"]["url"] == "data:video/mp4;base64,dmlkZW8="
    )


def test_video_validation_and_auth(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol,provider) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "video", "http://unused", "", "video", "openai", "dashscope"),
    )

    async def fake(config, question, data):
        return "视频分析完成"

    monkeypatch.setattr("app.vision_api.understand_video", fake)
    assert model_id in {item["id"] for item in connection.get("/api/v1/vision/video-models").json()["items"]}
    data = {"model_id": model_id, "question": "是什么"}
    valid = {"file": ("video.mp4", b"0000ftypmp42test")}
    assert (
        connection.post(
            "/api/v1/vision/video", data=data, files={"file": ("bad.mp4", b"bad")}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        connection.post(
            "/api/v1/vision/video", data={**data, "model_id": "test"}, files=valid, headers=ORIGIN
        ).status_code
        == 422
    )
    assert connection.post("/api/v1/vision/video", data=data, files=valid, headers=ORIGIN).json() == {
        "text": "视频分析完成"
    }
    connection.cookies.clear()
    assert connection.post("/api/v1/vision/video", data=data, files=valid, headers=ORIGIN).status_code == 401
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))


@pytest.mark.asyncio
async def test_siliconflow_generation_download_is_restricted_and_without_key(monkeypatch):
    from app.vision_api import generate_image

    requests = []
    url = "https://sf-maas-uat-prod.oss-cn-shanghai.aliyuncs.com/outputs/test.png?signature=test"

    async def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"images": [{"url": url}]})
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nimage")

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.vision_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x",
        name="测试",
        model="image",
        base_url="https://example.test/v1",
        api_key=SecretStr("secret"),
        kind="image_generation",
        provider="siliconflow",
    )
    assert await generate_image(config, "猫") == b"\x89PNG\r\n\x1a\nimage"
    assert json.loads(requests[0].content) == {"model": "image", "prompt": "猫"}
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert "authorization" not in requests[1].headers
    url = "http://127.0.0.1/private"
    with pytest.raises(ValueError, match="host"):
        await generate_image(config, "猫")
    assert len(requests) == 3
