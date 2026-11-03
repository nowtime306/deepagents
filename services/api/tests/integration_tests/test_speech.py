"""Speech protocol and ownership checks using a local protocol double."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client

from app.config import ModelConfig
from app.speech_api import generate_audio


@pytest.mark.asyncio
async def test_speech_protocol(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"ID3test-audio", headers={"content-type": "audio/mpeg"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.speech_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x",
        name="测试",
        model="provider/model",
        base_url="https://example.test/v1",
        api_key=SecretStr("secret"),
        kind="text_to_speech",
    )
    assert await generate_audio(config, "你好", "provider/model:alex") == b"ID3test-audio"
    assert requests[0].url.path == "/v1/audio/speech"
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert json.loads(requests[0].content) == {
        "model": "provider/model",
        "input": "你好",
        "voice": "provider/model:alex",
        "response_format": "mp3",
    }


def test_speech_owner_and_model_validation(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "speech", "http://unused", "", "text_to_speech", "openai"),
    )

    async def fake_audio(config, text, voice):
        return b"ID3sample"

    monkeypatch.setattr("app.speech_api.generate_audio", fake_audio)
    assert model_id in {item["id"] for item in connection.get("/api/v1/speech/models").json()["items"]}
    body = {"model_id": model_id, "text": "你好", "voice": "alex"}
    assert connection.post("/api/v1/speech", json={**body, "model_id": "test"}, headers=ORIGIN).status_code == 422
    result = connection.post("/api/v1/speech", json=body, headers=ORIGIN)
    assert result.status_code == 201
    identifier = result.json()["id"]
    assert connection.get(f"/api/v1/speech/{identifier}/audio").content == b"ID3sample"
    assert connection.get("/api/v1/speech").json()["items"][0]["id"] == identifier
    name = f"speech-other-{uuid4()}"
    run_async(add_user(DATABASE, name, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"/api/v1/speech/{identifier}/audio").status_code == 404
    assert connection.delete(f"/api/v1/speech/{identifier}", headers=ORIGIN).status_code == 404
    assert not connection.get("/api/v1/speech").json()["items"]
    # Clean up only the file and records this test created.
    connection.portal.call(app.state.db.execute, "DELETE FROM speech_outputs WHERE id=%s", (identifier,))
    (app.state.settings.data_dir / "speech" / f"{identifier}.mp3").unlink()
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))


@pytest.mark.asyncio
async def test_speech_rejects_non_audio_response(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={"error": "not an audio response"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.speech_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(id="x", name="测试", model="tts", base_url="https://example.test/v1", kind="text_to_speech")
    with pytest.raises(ValueError, match="content type"):
        await generate_audio(config, "你好", "alex")


def test_speech_failure_hides_provider_error(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "speech", "http://unused", "", "text_to_speech", "openai"),
    )

    async def fail_audio(config, text, voice):
        raise RuntimeError("sensitive-provider-error")

    monkeypatch.setattr("app.speech_api.generate_audio", fail_audio)
    result = connection.post(
        "/api/v1/speech", json={"model_id": model_id, "text": "你好", "voice": "alex"}, headers=ORIGIN
    )
    assert result.status_code == 502 and "sensitive-provider-error" not in result.text
    assert not connection.get("/api/v1/speech").json()["items"]
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))


@pytest.mark.asyncio
async def test_transcription_protocol(monkeypatch):
    from app.speech_api import transcribe_audio

    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "识别成功"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.speech_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x",
        name="测试",
        model="asr-model",
        base_url="https://example.test/v1",
        api_key=SecretStr("secret"),
        kind="speech_to_text",
    )
    assert await transcribe_audio(config, b"audio-data", ".mp3") == "识别成功"
    request = requests[0]
    assert request.url.path == "/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer secret"
    assert b"asr-model" in request.content and b"audio-data" in request.content
    assert b'filename="audio.mp3"' in request.content


def test_transcription_validation_and_auth(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "asr", "http://unused", "", "speech_to_text", "openai"),
    )

    async def fake_transcription(config, data, suffix):
        assert data == b"audio-data" and suffix == ".mp3"
        return "识别成功"

    monkeypatch.setattr("app.speech_api.transcribe_audio", fake_transcription)
    assert model_id in {item["id"] for item in connection.get("/api/v1/speech/transcription-models").json()["items"]}
    url = "/api/v1/speech/transcriptions"
    assert (
        connection.post(url, data={"model_id": model_id}, files={"file": ("bad.exe", b"x")}, headers=ORIGIN).status_code
        == 422
    )
    assert (
        connection.post(
            url, data={"model_id": model_id}, files={"file": ("empty.mp3", b"")}, headers=ORIGIN
        ).status_code
        == 413
    )
    assert (
        connection.post(
            url, data={"model_id": "test"}, files={"file": ("a.mp3", b"audio-data")}, headers=ORIGIN
        ).status_code
        == 422
    )
    response = connection.post(
        url, data={"model_id": model_id}, files={"file": ("a.mp3", b"audio-data")}, headers=ORIGIN
    )
    assert response.json() == {"text": "识别成功"}
    connection.cookies.clear()
    assert (
        connection.post(
            url, data={"model_id": model_id}, files={"file": ("a.mp3", b"audio-data")}, headers=ORIGIN
        ).status_code
        == 401
    )
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))


@pytest.mark.asyncio
async def test_audio_understanding_protocol(monkeypatch):
    from app.speech_api import understand_audio

    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "有人说话"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.speech_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(id="x", name="测试", model="audio", base_url="https://example.test/v1", kind="audio")
    assert await understand_audio(config, b"audio", "wav", "听到什么") == "有人说话"
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert body["messages"][0]["content"][1] == {
        "type": "input_audio",
        "input_audio": {"data": "YXVkaW8=", "format": "wav"},
    }


def test_audio_understanding_input_and_auth(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "audio", "http://unused", "", "audio", "openai"),
    )

    async def fake(config, data, audio_format, question):
        assert audio_format == "wav" and question == "听到什么"
        return "分析完成"

    monkeypatch.setattr("app.speech_api.understand_audio", fake)
    data = {"model_id": model_id, "question": "听到什么"}
    valid = {"file": ("a.wav", b"RIFF0000WAVEtest")}
    assert (
        connection.post(
            "/api/v1/speech/understanding", data=data, files={"file": ("a.wav", b"bad")}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        connection.post(
            "/api/v1/speech/understanding", data={**data, "model_id": "test"}, files=valid, headers=ORIGIN
        ).status_code
        == 422
    )
    assert connection.post("/api/v1/speech/understanding", data=data, files=valid, headers=ORIGIN).json() == {
        "text": "分析完成"
    }
    connection.cookies.clear()
    assert connection.post("/api/v1/speech/understanding", data=data, files=valid, headers=ORIGIN).status_code == 401
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))
