"""Purpose/provider persistence and executable-model isolation using PostgreSQL."""

from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client


def test_model_types_roundtrip_and_only_compatible_models_are_exposed(client) -> None:
    connection, app = client
    assert connection.get("/api/v1/admin/models/catalog").status_code == 403
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    catalog = connection.get("/api/v1/admin/models/catalog").json()
    created = {}
    try:
        for index, kind in enumerate(catalog["kinds"]):
            body = {
                "name": f"用途-{kind['id']}-{uuid4()}",
                "model": "protocol-test",
                "kind": kind["id"],
                "provider": "siliconflow",
                "protocol": "openai",
                "base_url": "http://127.0.0.1:8793/v1",
                "api_key": f"private-type-key-{index}",
            }
            response = connection.post("/api/v1/admin/models", json=body, headers=ORIGIN)
            assert response.status_code == 201, response.text
            identifier = response.json()["id"]
            created[kind["id"]] = identifier
            listing = connection.get("/api/v1/admin/models")
            assert body["api_key"] not in listing.text
            row = next(item for item in listing.json()["items"] if item["id"] == identifier)
            assert row["kind"] == kind["id"] and row["provider"] == "siliconflow"
            edited = {**body, "name": body["name"] + "修改", "api_key": "", "revision": row["revision"]}
            assert connection.put(f"/api/v1/admin/models/{identifier}", json=edited, headers=ORIGIN).status_code == 200
            stored = connection.portal.call(app.state.models.get, identifier)
            assert stored.kind == kind["id"] and stored.api_key.get_secret_value() == body["api_key"]
            if kind["id"] not in {"chat", "embedding", "rerank"}:
                assert row["probe_hint"]
                probe = connection.post(f"/api/v1/admin/models/{identifier}/test", headers=ORIGIN)
                assert probe.status_code == 422 and probe.json()["error"]["code"] == "MODEL_PROBE_UNSUPPORTED"
                status = connection.portal.call(
                    app.state.db.fetch,
                    "SELECT connection_status,checked_at FROM model_configs WHERE id=%s",
                    (identifier,),
                )[0]
                assert status["connection_status"] == "not_tested" and status["checked_at"] is None
        chat_ids = {item["id"] for item in connection.get("/api/v1/models").json()["items"]}
        embedding_ids = {item["id"] for item in connection.get("/api/v1/capabilities/embedding-models").json()["items"]}
        assert chat_ids.intersection(created.values()) == {created["chat"]}
        assert embedding_ids.intersection(created.values()) == {created["embedding"]}
        conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
        for kind, identifier in created.items():
            if kind == "vision":
                choices = connection.get("/api/v1/models").json()["vision_models"]
                assert any(item["id"] == identifier and item["supports_images"] for item in choices)
                result = connection.post(
                    f"/api/v1/conversations/{conversation}/runs",
                    json={"model_id": identifier, "message": "视觉模型也支持文字提问"},
                    headers=ORIGIN,
                )
                assert result.status_code == 202
                assert "run.completed" in connection.get("/api/v1/runs/" + result.json()["run_id"] + "/events").text
            elif kind != "chat":
                assert (
                    connection.post(
                        f"/api/v1/conversations/{conversation}/runs",
                        json={"model_id": identifier, "message": "应拒绝非对话模型"},
                        headers=ORIGIN,
                    ).status_code
                    == 422
                )
            if kind != "embedding":
                assert (
                    connection.post(
                        "/api/v1/capabilities/knowledge",
                        json={"name": "错误向量类型", "embedding_model_id": identifier},
                        headers=ORIGIN,
                    ).status_code
                    == 422
                )
    finally:
        for identifier in created.values():
            connection.delete(f"/api/v1/admin/models/{identifier}", headers=ORIGIN)


def test_provider_protocol_persistence_and_unadapted_chat_is_not_callable(client) -> None:
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    body = {
        "name": f"原生接口-{uuid4()}",
        "model": "protocol-test",
        "kind": "chat",
        "provider": "modelengine",
        "protocol": "custom",
        "base_url": "http://127.0.0.1:8793/native",
    }
    result = connection.post("/api/v1/admin/models", json=body, headers=ORIGIN)
    assert result.status_code == 201
    identifier = result.json()["id"]
    try:
        assert identifier not in {item["id"] for item in connection.get("/api/v1/models").json()["items"]}
        assert connection.post(f"/api/v1/admin/models/{identifier}/test", headers=ORIGIN).status_code == 422
        row = next(item for item in connection.get("/api/v1/admin/models").json()["items"] if item["id"] == identifier)
        assert row["provider"] == "modelengine" and row["protocol"] == "custom"
        change = {**body, "provider": "anthropic", "protocol": "anthropic", "revision": row["revision"]}
        assert connection.put(f"/api/v1/admin/models/{identifier}", json=change, headers=ORIGIN).status_code == 200
        assert identifier in {item["id"] for item in connection.get("/api/v1/models").json()["items"]}
        stored = connection.portal.call(app.state.models.get, identifier)
        assert stored.provider == "anthropic" and stored.protocol == "anthropic"
        invalid_default = {**body, "is_default": True, "name": f"无效默认-{uuid4()}"}
        assert connection.post("/api/v1/admin/models", json=invalid_default, headers=ORIGIN).status_code == 422
    finally:
        connection.delete(f"/api/v1/admin/models/{identifier}", headers=ORIGIN)
