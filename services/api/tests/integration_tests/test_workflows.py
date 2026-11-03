"""Workflow execution, persisted snapshots, conflicts and ownership."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_workflow_graph_persists_result_and_is_owner_scoped(client) -> None:
    connection, _ = client
    body = {"name": f"摘要-{uuid4()}", "instructions": "生成摘要", "model_id": "test"}
    result = connection.post("/api/v1/workflows", json=body, headers=ORIGIN)
    assert result.status_code == 201
    identifier = result.json()["id"]
    run = connection.post(f"/api/v1/workflows/{identifier}/test-runs", json={"text": "整理输入"}, headers=ORIGIN)
    assert run.status_code == 200 and run.json()["output"] == "你好，已收到。"
    history = connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"]
    assert history[0]["status"] == "SUCCEEDED" and history[0]["snapshot"]["instructions"] == "生成摘要"
    assert (
        connection.put(
            f"/api/v1/workflows/{identifier}", json={**body, "instructions": "新指令", "revision": 1}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert (
        connection.put(f"/api/v1/workflows/{identifier}", json={**body, "revision": 1}, headers=ORIGIN).status_code
        == 409
    )
    assert (
        connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"][0]["snapshot"]["instructions"]
        == "生成摘要"
    )
    name = f"other-workflow-{uuid4()}"
    run_async(add_user(DATABASE, name, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN)
    assert identifier not in {row["id"] for row in connection.get("/api/v1/workflows").json()["items"]}
    assert connection.get(f"/api/v1/workflows/{identifier}/runs").status_code == 404
    assert (
        connection.post(f"/api/v1/workflows/{identifier}/test-runs", json={"text": "越权"}, headers=ORIGIN).status_code
        == 404
    )


def test_workflow_failure_is_recorded_without_leaking_error(client) -> None:
    connection, app = client
    identifier = connection.post(
        "/api/v1/workflows", json={"name": f"失败-{uuid4()}", "model_id": "test"}, headers=ORIGIN
    ).json()["id"]

    def failed(_):
        raise RuntimeError("sensitive-provider-error")

    app.state.runtime.model_factory = failed
    response = connection.post(f"/api/v1/workflows/{identifier}/test-runs", json={"text": "测试"}, headers=ORIGIN)
    assert response.status_code == 502 and "sensitive-provider-error" not in response.text
    history = connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"]
    assert history[0]["status"] == "FAILED" and history[0]["output"] is None


def test_publish_version_is_immutable_and_rejects_changed_model(client):
    connection, app = client
    model_id = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext) VALUES (%s,%s,%s,%s,%s)",
        (model_id, model_id, "model", "http://unused", ""),
    )
    body = {"name": f"发布-{uuid4()}", "instructions": "原始指令", "model_id": model_id}
    workflow = connection.post("/api/v1/workflows", json=body, headers=ORIGIN).json()["id"]
    prefix = f"/api/v1/workflows/{workflow}"
    assert connection.post(f"{prefix}/publish", json={"revision": 9}, headers=ORIGIN).status_code == 409
    assert connection.post(f"{prefix}/publish", json={"revision": 1}, headers=ORIGIN).status_code == 409
    assert connection.post(f"{prefix}/test-runs", json={"text": "测试发布门禁"}, headers=ORIGIN).status_code == 200
    assert connection.post(f"{prefix}/publish", json={"revision": 1}, headers=ORIGIN).json()["version"] == 1
    assert connection.post(f"{prefix}/publish", json={"revision": 1}, headers=ORIGIN).json()["version"] == 1
    assert (
        connection.put(prefix, json={**body, "instructions": "修改后的草稿", "revision": 1}, headers=ORIGIN).status_code
        == 200
    )
    result = connection.post(f"{prefix}/versions/1/runs", json={"text": "测试"}, headers=ORIGIN)
    assert result.status_code == 200 and result.json()["output"] == "你好，已收到。"
    history = connection.get(f"{prefix}/runs").json()["items"]
    assert history[0]["snapshot"]["published_version"] == 1
    assert history[0]["snapshot"]["instructions"] == "原始指令"
    connection.portal.call(
        app.state.db.execute, "UPDATE model_configs SET revision=revision+1 WHERE id=%s", (model_id,)
    )
    assert connection.post(f"{prefix}/versions/1/runs", json={"text": "测试"}, headers=ORIGIN).status_code == 409
    assert connection.post(f"{prefix}/test-runs", json={"text": "重新验证模型配置"}, headers=ORIGIN).status_code == 200
    assert connection.post(f"{prefix}/publish", json={"revision": 2}, headers=ORIGIN).json()["version"] == 2
    name = f"other-published-{uuid4()}"
    old_cookies = dict(connection.cookies)
    run_async(add_user(DATABASE, name, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"{prefix}/versions").status_code == 404
    assert connection.post(f"{prefix}/versions/1/runs", json={"text": "越权"}, headers=ORIGIN).status_code == 404
    connection.cookies.clear()
    connection.cookies.update(old_cookies)
    assert connection.delete(prefix, headers=ORIGIN).status_code == 200
    connection.portal.call(app.state.db.execute, "DELETE FROM model_configs WHERE id=%s", (model_id,))
