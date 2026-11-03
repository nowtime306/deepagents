"""DashScope image embeddings and private persistent retrieval."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client

from app.config import ModelConfig
from app.multimodal_api import vectorize


@pytest.mark.asyncio
async def test_multimodal_protocol(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"output": {"embeddings": [{"index": 0, "embedding": [0.5, 0.25]}]}})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.multimodal_api.httpx.AsyncClient", lambda **_: original(transport=httpx.MockTransport(handler))
    )
    config = ModelConfig(
        id="x",
        name="测试",
        model="multimodal-embedding-v1",
        base_url="https://example.test/api/v1",
        api_key=SecretStr("secret"),
        kind="multimodal_embedding",
        protocol="dashscope_multimodal",
    )
    assert await vectorize(config, {"text": "猫"}) == [0.5, 0.25]
    request = requests[0]
    assert request.url.path == "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
    assert request.headers["authorization"] == "Bearer secret"
    assert json.loads(request.content) == {"model": "multimodal-embedding-v1", "input": {"contents": [{"text": "猫"}]}}


def test_image_index_persistence_owner_and_model_change(client, monkeypatch):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (model_id, model_id, "mm", "http://unused/api/v1", "", "multimodal_embedding", "dashscope_multimodal"),
    )

    async def fake(config, content):
        return [1.0, 0.0] if "image" in content or content.get("text") == "猫" else [0.0, 1.0]

    monkeypatch.setattr("app.multimodal_api.vectorize", fake)
    kb = connection.post("/api/v1/capabilities/knowledge", json={"name": f"图片-{uuid4()}"}, headers=ORIGIN).json()[
        "id"
    ]
    prefix = f"/api/v1/capabilities/knowledge/{kb}"
    assert connection.post(f"{prefix}/image-index", json={"model_id": "test"}, headers=ORIGIN).status_code == 422
    assert connection.post(f"{prefix}/image-index", json={"model_id": model_id}, headers=ORIGIN).status_code == 201
    image = connection.post(f"{prefix}/images", files={"file": ("cat.png", b"\x89PNG\r\n\x1a\ncat")}, headers=ORIGIN)
    assert image.status_code == 201
    image_id = image.json()["id"]
    ledger = connection.portal.call(app.state.db.fetch,
        "SELECT model_revision,status,input_tokens,resource_snapshot_id FROM model_usage WHERE run_id=%s",
        (image.json()["operation_id"],))
    assert ledger[0]["model_revision"] == 1 and ledger[0]["status"] == "UNKNOWN"
    assert ledger[0]["input_tokens"] is None and ledger[0]["resource_snapshot_id"] is not None
    assert connection.get(f"{prefix}/image-index").json()["index"]["dimensions"] == 2
    assert (
        connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).json()["items"][0]["id"]
        == image_id
    )
    assert connection.get(f"{prefix}/images/{image_id}").content.endswith(b"cat")

    connection.portal.call(app.state.db.execute,
        "UPDATE model_configs SET name=name||'-maintained',revision=revision+1 WHERE id=%s", (model_id,))
    assert connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).status_code == 200
    connection.portal.call(app.state.db.execute,
        "UPDATE model_configs SET base_url='http://moved/api/v1',revision=revision+1 WHERE id=%s", (model_id,))
    assert connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).status_code == 409
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    stale = connection.post(f"/api/v1/capabilities/embedding-compatibility/{model_id}/verify", headers=ORIGIN,
        json={"expected_revision": 2, "expected_base_url": "http://unused/api/v1",
              "previous_base_url": "http://unused/api/v1", "confirmed_model": "mm", "dimensions": 2,
              "evidence": "Administrator saw the previous target; this stale confirmation must be rejected."})
    assert stale.status_code == 409, stale.text
    attested = connection.post(f"/api/v1/capabilities/embedding-compatibility/{model_id}/verify", headers=ORIGIN,
        json={"expected_revision": 3, "expected_base_url": "http://moved/api/v1",
              "previous_base_url": "http://unused/api/v1", "confirmed_model": "mm", "dimensions": 2,
              "evidence": "Test administrator confirms equal model version, preprocessing and output; test double."})
    assert attested.status_code == 200, attested.text
    assert connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).status_code == 200
    async def wrong_dimension(config, content):
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr("app.multimodal_api.vectorize", wrong_dimension)
    assert connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).status_code == 409
    mismatch = connection.post(
        f"{prefix}/images", files={"file": ("wrong.png", b"\x89PNG\r\n\x1a\nwrong")}, headers=ORIGIN
    )
    assert mismatch.status_code == 409
    assert len(connection.get(f"{prefix}/images").json()["items"]) == 1
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET model='changed' WHERE id=%s", (model_id,))
    assert connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).status_code == 409
    name = f"mm-other-{uuid4()}"
    run_async(add_user(DATABASE, name, "integration-password"))
    old_cookies = dict(connection.cookies)
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"{prefix}/images/{image_id}").status_code == 404
    assert connection.post(f"{prefix}/image-search", json={"query": "猫"}, headers=ORIGIN).status_code == 404
    connection.cookies.clear()
    connection.cookies.update(old_cookies)
    assert connection.delete(f"{prefix}", headers=ORIGIN).status_code == 200
    assert not (app.state.settings.data_dir / "knowledge-images" / image_id).exists()
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))
