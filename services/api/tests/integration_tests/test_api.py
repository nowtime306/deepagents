"""Real PostgreSQL API integration; model responses are deterministic test doubles."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_openai import ChatOpenAI
from psycopg import AsyncConnection

from app.config import Settings
from app.main import create_app
from app.manage import add_user, migrate
from app.models import CredentialVault

DATABASE = os.getenv("TEST_DATABASE_URL", "")
ORIGIN = {"Origin": "http://localhost:3100"}
TEST_DATA = Path(os.getenv("TEST_DATA_DIR", str(Path(__file__).parents[2] / ".local" / "test")))


class TestModel(FakeListChatModel):
    """Support create_agent's binding API without using a paid provider."""

    __test__ = False

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


def run_async(coroutine):
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(coroutine)


async def seed_model(url: str) -> None:
    vault = CredentialVault(TEST_DATA / "model.key")
    async with await AsyncConnection.connect(url) as connection:
        await connection.execute(
            "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext) VALUES "
            "('test','测试模型','test','http://unused',%s) ON CONFLICT(id) DO UPDATE SET enabled=true",
            (vault.encrypt("test-only"),),
        )


@pytest.fixture
def client():
    if not DATABASE or not DATABASE.rsplit("/", 1)[-1].endswith("_test"):
        pytest.skip("Set TEST_DATABASE_URL to an isolated database ending in _test")
    run_async(migrate(DATABASE))
    username = f"test-{uuid4()}"
    run_async(add_user(DATABASE, username, "integration-password"))
    run_async(seed_model(DATABASE))
    app = create_app(Settings(database_url=DATABASE, data_dir=TEST_DATA))
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with TestClient(app, backend_options={"loop_factory": factory}, client=(username, 50000)) as connection:
        app.state.runtime.model_factory = lambda _: TestModel(responses=["你好，已收到。", "上下文仍然保留。"])
        response = connection.post(
            "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
        )
        assert response.status_code == 200
        yield connection, app


def test_auth_origin_and_resource_isolation(client) -> None:
    connection, _ = client
    conversation = connection.post("/api/v1/conversations", json={"title": "私密对话"}, headers=ORIGIN).json()
    assert connection.post("/api/v1/conversations", json={"title": "伪造来源"}).status_code == 403
    connection.cookies.clear()
    assert connection.get(f"/api/v1/conversations/{conversation['id']}").status_code == 401
    other = f"other-{uuid4()}"
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"/api/v1/conversations/{conversation['id']}").status_code == 404
    assert connection.get(f"/api/v1/conversations/{conversation['id']}/messages").status_code == 404
    assert not connection.get("/api/v1/conversations").json()["items"]


def test_stream_history_replay_and_checkpoint(client) -> None:
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={"title": "测试流"}, headers=ORIGIN).json()
    result = connection.post(
        f"/api/v1/conversations/{conversation['id']}/runs", json={"message": "你好", "model_id": "test"}, headers=ORIGIN
    )
    assert result.status_code == 202
    run_id = result.json()["run_id"]
    response = connection.get(f"/api/v1/runs/{run_id}/events")
    events = [item for line in response.text.splitlines() if line.startswith("data: ")
              if "event_id" in (item := json.loads(line[6:]))]
    assert events[-1]["event"] == "run.completed"
    assert "".join(event["data"]["text"] for event in events if event["event"] == "message.delta") == "你好，已收到。"
    assert [event["event_id"] for event in events] == list(range(1, len(events) + 1))
    replay = connection.get(f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
    assert all(item["event_id"] > 2 for line in replay.text.splitlines() if line.startswith("data: ")
               if "event_id" in (item := json.loads(line[6:])))
    messages = connection.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    second = connection.post(
        f"/api/v1/conversations/{conversation['id']}/runs",
        json={"message": "继续", "model_id": "test"},
        headers=ORIGIN,
    ).json()
    assert "run.completed" in connection.get(f"/api/v1/runs/{second['run_id']}/events").text
    with connection.portal.wrap_async_context_manager(app.state.runtime.saver.from_conn_string(DATABASE)) as saver:
        checkpoint = connection.portal.call(saver.aget_tuple, {"configurable": {"thread_id": conversation["id"]}})
        assert checkpoint is not None
        history = checkpoint.checkpoint["channel_values"]["messages"]
        assert [item.content for item in history if item.type == "human"] == ["你好", "继续"]


def test_cancel_in_flight_and_terminal_state(client) -> None:
    connection, app = client
    app.state.runtime.model_factory = lambda _: TestModel(responses=["很长的测试回答" * 20], sleep=0.1)
    conversation = connection.post("/api/v1/conversations", json={"title": "取消"}, headers=ORIGIN).json()
    run = connection.post(
        f"/api/v1/conversations/{conversation['id']}/runs",
        json={"message": "执行", "model_id": "test"},
        headers=ORIGIN,
    ).json()["run_id"]
    assert connection.delete(f"/api/v1/conversations/{conversation['id']}", headers=ORIGIN).status_code == 409
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        deltas = connection.portal.call(
            app.state.db.fetch,
            "SELECT 1 FROM run_events WHERE run_id=%s AND event='message.delta'",
            (run,),
        )
        if deltas:
            break
        time.sleep(0.02)
    assert deltas, "The model must emit some visible text before cancellation"
    assert connection.post(f"/api/v1/runs/{run}/cancel", headers=ORIGIN).status_code == 202
    events = connection.get(f"/api/v1/runs/{run}/events").text
    assert "run.cancelled" in events
    assert "run.completed" not in events
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "CANCELLED"
    assert connection.post(f"/api/v1/runs/{run}/cancel", headers=ORIGIN).status_code == 409
    messages = connection.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"]["data"]["additional_kwargs"]["interrupted"]


def test_run_and_event_ownership(client) -> None:
    connection, _ = client
    conversation = connection.post("/api/v1/conversations", json={"title": "事件隔离"}, headers=ORIGIN).json()
    run = connection.post(
        f"/api/v1/conversations/{conversation['id']}/runs",
        json={"message": "你好", "model_id": "test"},
        headers=ORIGIN,
    ).json()["run_id"]
    connection.get(f"/api/v1/runs/{run}/events")
    username = f"isolation-{uuid4()}"
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.post(
        "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
    )
    for suffix in ["", "/events"]:
        assert connection.get(f"/api/v1/runs/{run}{suffix}").status_code == 404
    assert connection.post(f"/api/v1/runs/{run}/cancel", headers=ORIGIN).status_code == 404


def test_model_response_excludes_credentials_and_validation_is_chinese(client) -> None:
    connection, _ = client
    assert connection.get("/api/v1/models").json() == {
        "items": [{"id": "test", "name": "测试模型", "supports_images": False, "native_search_mode": None}],
        "vision_models": [],
    }
    response = connection.post("/api/v1/conversations", json={"title": ""}, headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith("输入格式")


def test_openai_wire_protocol_tool_roundtrip(client) -> None:
    connection, app = client
    calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if payload["messages"][-1]["role"] == "tool":
            delta = {"content": "字符数为 2"}
            finish = "stop"
        else:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_length",
                        "type": "function",
                        "function": {
                            "name": "text_length",
                            "arguments": '{"text":"你好"}',
                        },
                    }
                ]
            }
            finish = "tool_calls"
        chunks = [
            {
                "id": f"reply-test-{len(calls)}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "protocol-test",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": f"reply-test-{len(calls)}",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "protocol-test",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)

    transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    model = ChatOpenAI(
        model="protocol-test", api_key="test-only", base_url="http://provider.invalid/v1", http_async_client=transport
    )
    app.state.runtime.model_factory = lambda _: model
    try:
        conversation = connection.post("/api/v1/conversations", json={"title": "协议验证"}, headers=ORIGIN).json()
        run = connection.post(
            f"/api/v1/conversations/{conversation['id']}/runs",
            json={"message": "调用工具计算字符数", "model_id": "test"},
            headers=ORIGIN,
        ).json()["run_id"]
        events = connection.get(f"/api/v1/runs/{run}/events").text
        assert "run.completed" in events
        messages = connection.get(f"/api/v1/conversations/{conversation['id']}/messages").json()["items"]
        assert [item["role"] for item in messages] == ["user", "assistant", "tool", "assistant"]
        assert messages[2]["content"]["data"]["content"] == "2"
        assert messages[-1]["content"]["data"]["content"] == "字符数为 2"
        assert len(calls) == 2
        assert calls[1]["messages"][-1]["role"] == "tool"
    finally:
        connection.portal.call(transport.aclose)


def test_restart_preserves_history_and_resumes_conversation() -> None:
    if not DATABASE or not DATABASE.rsplit("/", 1)[-1].endswith("_test"):
        pytest.skip("Set TEST_DATABASE_URL to an isolated database ending in _test")
    run_async(migrate(DATABASE))
    username = f"restart-{uuid4()}"
    run_async(add_user(DATABASE, username, "integration-password"))
    run_async(seed_model(DATABASE))
    settings = Settings(database_url=DATABASE, data_dir=TEST_DATA)
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    conversation_id = None
    for turn in ["第一轮", "重启后继续"]:
        app = create_app(settings)
        with TestClient(app, backend_options={"loop_factory": factory}) as connection:
            app.state.runtime.model_factory = lambda _: TestModel(responses=["已收到"])
            connection.post(
                "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
            )
            if conversation_id is None:
                conversation_id = connection.post(
                    "/api/v1/conversations", json={"title": "重启恢复"}, headers=ORIGIN
                ).json()["id"]
            result = connection.post(
                f"/api/v1/conversations/{conversation_id}/runs",
                json={"message": turn, "model_id": "test"},
                headers=ORIGIN,
            ).json()
            assert "run.completed" in connection.get(f"/api/v1/runs/{result['run_id']}/events").text
            if turn == "重启后继续":
                history = connection.get(f"/api/v1/conversations/{conversation_id}/messages").json()["items"]
                assert len(history) == 4
                saved = connection.portal.call(
                    app.state.runtime.saver.aget_tuple, {"configurable": {"thread_id": conversation_id}}
                )
                assert [
                    message.content
                    for message in saved.checkpoint["channel_values"]["messages"]
                    if message.type == "human"
                ] == ["第一轮", "重启后继续"]


def test_database_model_management_and_write_only_credentials(client) -> None:
    connection, app = client
    payload = {
        "name": f"配置-{uuid4()}",
        "model": "model-alpha",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "provider-secret-must-not-leak",
        "is_default": True,
        "max_output_tokens": 1024,
        "temperature": 0.4,
    }
    assert connection.post("/api/v1/admin/models", json=payload, headers=ORIGIN).status_code == 403
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    created = connection.post("/api/v1/admin/models", json=payload, headers=ORIGIN)
    assert created.status_code == 201
    identifier = created.json()["id"]
    try:
        response = connection.get("/api/v1/admin/models")
        assert payload["api_key"] not in response.text
        assert "api_key_ciphertext" not in response.text
        row = next(item for item in response.json()["items"] if item["id"] == identifier)
        assert row["api_key_configured"] and row["is_default"]
        assert connection.get("/api/v1/models").json()["items"][0]["id"] == identifier
        snapshot = connection.portal.call(app.state.models.get, identifier)
        assert snapshot.api_key.get_secret_value() == payload["api_key"]
        assert snapshot.max_output_tokens == 1024
        edited = {**payload, "name": payload["name"] + "已编辑", "api_key": "", "revision": row["revision"]}
        assert connection.put(f"/api/v1/admin/models/{identifier}", json=edited, headers=ORIGIN).status_code == 200
        assert connection.portal.call(app.state.models.get, identifier).api_key == snapshot.api_key
        assert connection.put(f"/api/v1/admin/models/{identifier}", json=edited, headers=ORIGIN).status_code == 409
        checked = connection.post(f"/api/v1/admin/models/{identifier}/test", headers=ORIGIN)
        assert checked.json()["success"]
        row = next(item for item in connection.get("/api/v1/admin/models").json()["items"] if item["id"] == identifier)
        assert row["connection_status"] == "available"
        disabled = {**edited, "revision": row["revision"], "enabled": False, "is_default": False}
        assert connection.put(f"/api/v1/admin/models/{identifier}", json=disabled, headers=ORIGIN).status_code == 200
        assert identifier not in [item["id"] for item in connection.get("/api/v1/models").json()["items"]]
        raw = connection.portal.call(
            app.state.db.fetch, "SELECT api_key_ciphertext FROM model_configs WHERE id=%s", (identifier,)
        )
        assert payload["api_key"] not in raw[0]["api_key_ciphertext"]
    finally:
        connection.delete(f"/api/v1/admin/models/{identifier}", headers=ORIGIN)


def test_model_default_uniqueness_and_explicit_key_clear(client) -> None:
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (account["id"],))
    identifiers = []
    try:
        for label in ["A", "B"]:
            body = {
                "name": f"default-{label}-{uuid4()}",
                "model": "model-test",
                "base_url": "http://local.test/v1",
                "api_key": "test-only-secret",
                "is_default": True,
            }
            result = connection.post("/api/v1/admin/models", json=body, headers=ORIGIN)
            assert result.status_code == 201
            identifiers.append(result.json()["id"])
        rows = connection.get("/api/v1/admin/models").json()["items"]
        defaults = [row for row in rows if row["is_default"]]
        assert len(defaults) == 1 and defaults[0]["id"] == identifiers[-1]
        row = defaults[0]
        cleared = {
            "name": row["name"],
            "model": row["model"],
            "base_url": row["base_url"],
            "is_default": True,
            "clear_api_key": True,
            "revision": row["revision"],
        }
        assert connection.put(f"/api/v1/admin/models/{row['id']}", json=cleared, headers=ORIGIN).status_code == 200
        stored = connection.portal.call(app.state.models.get, row["id"])
        assert stored.api_key.get_secret_value() == ""
        rows = connection.get("/api/v1/admin/models").json()["items"]
        assert not next(item for item in rows if item["id"] == row["id"])["api_key_configured"]
    finally:
        for identifier in identifiers:
            connection.delete(f"/api/v1/admin/models/{identifier}", headers=ORIGIN)


def test_work_queue_fenced_claim_and_expired_recovery(client) -> None:
    from uuid import UUID

    from app.work_queue import WorkQueue

    connection, app = client
    created = connection.post(
        "/api/v1/work-tasks", json={"title": "队列测试", "goal": "安全执行"}, headers=ORIGIN
    ).json()
    task_id = UUID(created["id"])
    task = connection.get(f"/api/v1/work-tasks/{task_id}").json()
    owner = connection.portal.call(app.state.db.fetch, "SELECT owner_id FROM work_tasks WHERE id=%s", (task_id,))[0][
        "owner_id"
    ]
    queue = WorkQueue(app.state.db)
    assert connection.portal.call(queue.enqueue, task_id, uuid4(), task["revision"]) is None
    assert connection.portal.call(queue.enqueue, task_id, owner, task["revision"] + 1) is None
    run_id = connection.portal.call(queue.enqueue, task_id, owner, task["revision"])

    async def claim_twice():
        return await asyncio.gather(queue.claim(), queue.claim())

    claimed = connection.portal.call(claim_twice)
    assert sum(item is not None for item in claimed) == 1
    run = next(item for item in claimed if item is not None)
    assert run["id"] == run_id and run["goal"] == "安全执行"
    updated = connection.put(
        f"/api/v1/work-tasks/{task_id}",
        json={"title": "新标题", "goal": "新目标", "revision": task["revision"]},
        headers=ORIGIN,
    )
    assert updated.status_code == 200
    rows = connection.portal.call(app.state.db.fetch, "SELECT goal FROM work_queue_runs WHERE id=%s", (run_id,))
    assert rows[0]["goal"] == "安全执行"
    assert not connection.portal.call(queue.renew, run_id, uuid4())
    assert connection.portal.call(queue.renew, run_id, run["lease_token"])
    connection.portal.call(
        app.state.db.execute, "UPDATE work_queue_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (run_id,)
    )
    assert not connection.portal.call(queue.renew, run_id, run["lease_token"])

    async def finish(lease, success):
        return await queue.finish(lease["id"], lease["lease_token"], success=success)

    assert not connection.portal.call(finish, run, True)
    assert connection.portal.call(queue.mark_expired_for_review) == 1
    assert connection.portal.call(queue.claim) is None
    rows = connection.portal.call(app.state.db.fetch, "SELECT status FROM work_queue_runs WHERE id=%s", (run_id,))
    assert rows[0]["status"] == "NEEDS_REVIEW"
    second = connection.portal.call(queue.enqueue, task_id, owner, task["revision"] + 1)
    second_lease = connection.portal.call(queue.claim)
    assert second_lease["id"] == second
    assert connection.portal.call(finish, second_lease, True)
    assert not connection.portal.call(finish, second_lease, True)
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id IN (%s,%s)", (run_id, second))
    assert connection.delete(f"/api/v1/work-tasks/{task_id}", headers=ORIGIN).status_code == 200
