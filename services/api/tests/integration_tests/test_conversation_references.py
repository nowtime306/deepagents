"""References remain private, pinned, bounded and outside persistent graph context."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_api import DATABASE, ORIGIN, TestModel, add_user, run_async
from test_api import client as client
from test_conversation_branches import ask

from app.errors import APIError


def create(connection, title="引用目标"):
    return connection.post("/api/v1/conversations", headers=ORIGIN, json={"title": title}).json()["id"]


def update(connection, target, sources, revision=0, **kwargs):
    return connection.put(
        f"/api/v1/conversations/{target}/references", headers=ORIGIN,
        json={"revision": revision, "source_ids": sources, **kwargs},
    )


def test_private_sources_empty_self_limits_and_revision_conflict(client):
    connection, app = client
    source, target = create(connection), create(connection)
    assert update(connection, target, [target]).status_code == 422
    assert update(connection, target, [source, source]).status_code == 422
    assert update(connection, target, [source]).json()["error"]["code"] == "REFERENCE_EMPTY"
    ask(connection, source, "本人可引用资料")
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: update(connection, target, [source]), range(2)))
    assert sorted(row.status_code for row in replies) == [200, 409]
    assert update(connection, target, [source] * 4, 1).status_code == 422
    other = "reference-" + uuid4().hex
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", headers=ORIGIN,
                    json={"username": other, "password": "integration-password"})
    private = create(connection)
    assert connection.get(f"/api/v1/conversations/{target}/references").status_code == 404
    assert update(connection, private, [source]).status_code == 404
    assert connection.portal.call(app.state.db.fetch,
                                 "SELECT 1 FROM conversation_references WHERE conversation_id=%s", (private,)) == []


def test_fixed_messages_refresh_selected_only_no_tools_no_nested_references(client):
    connection, app = client
    sources = [create(connection, "来源" + str(index)) for index in range(3)]
    target = create(connection)
    for index, source in enumerate(sources):
        ask(connection, source, "固定正文-" + str(index))
    assert update(connection, sources[0], [sources[2]]).status_code == 200
    assert update(connection, target, sources[:2]).status_code == 200
    before = connection.portal.call(app.state.db.fetch,
        "SELECT source_id,message_ids FROM conversation_references WHERE conversation_id=%s ORDER BY source_id",
        (target,))
    ask(connection, sources[0], "新消息A")
    ask(connection, sources[1], "新消息B")
    # Adding a source does not silently advance existing sources.
    assert update(connection, target, sources, 1).status_code == 200
    after = connection.portal.call(app.state.db.fetch,
        "SELECT source_id,message_ids FROM conversation_references WHERE conversation_id=%s ORDER BY source_id",
        (target,))
    assert all(row in after for row in before)
    assert update(connection, target, sources, 2, refresh_source=sources[0]).status_code == 200
    prompts = []

    class InspectModel(TestModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk

        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            return "已读取引用"

    app.state.runtime.model_factory = lambda _: InspectModel(responses=["unused"])
    ask(connection, target, "根据资料回答")
    text = str(prompts[-1])
    assert "新消息A" in text and "新消息B" not in text
    # Only the explicitly selected sources appear, never recursively traversed.
    assert text.count("固定正文-2") == 1
    assert "不可信参考资料" in text
    assert all(not getattr(row, "tool_calls", None) for row in prompts[-1])


def test_estimate_diagnostics_checkpoint_and_real_summarization_exclude_reference_text(client, monkeypatch):
    from deepagents.middleware.summarization import SummarizationMiddleware

    connection, app = client
    source, target = create(connection), create(connection)
    secret = "引用隐私标记-" + uuid4().hex
    ask(connection, source, secret)
    baseline = connection.post("/api/v1/usage/estimate", headers=ORIGIN,
        json={"model_id": "test", "conversation_id": target, "message": "问题"}).json()
    assert update(connection, target, [source]).status_code == 200
    estimate = connection.post("/api/v1/usage/estimate", headers=ORIGIN,
        json={"model_id": "test", "conversation_id": target, "message": "问题"}).json()
    assert estimate["reference_input_estimate"] > 0
    assert estimate["input_estimate"] - baseline["input_estimate"] == estimate["reference_input_estimate"]
    captured = []

    class InspectModel(TestModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            captured.append(messages)
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk

        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            captured.append(messages)
            return "普通回答与摘要，不抄录引用"

    monkeypatch.setattr(importlib.import_module("deepagents.graph"), "create_summarization_middleware",
        lambda model, backend: SummarizationMiddleware(model=model, backend=backend,
                                                       trigger=("messages", 4), keep=("messages", 2)))
    app.state.runtime.model_factory = lambda _: InspectModel(responses=["unused"])
    for index in range(3):
        run = ask(connection, target, "继续询问" + str(index))
    with connection.portal.wrap_async_context_manager(app.state.runtime.saver.from_conn_string(DATABASE)) as saver:
        state = connection.portal.call(saver.aget_tuple, {"configurable": {"thread_id": target}})
    values = state.checkpoint["channel_values"]
    assert values.get("_summarization_event"), "Actually exercise compaction, not only the normal request path"
    assert secret not in str(values), "References must not be persisted in messages, summaries or offloaded files"
    assert any(secret in str(prompt) for prompt in captured)
    diagnostic = connection.get(f"/api/v1/runs/{run}/diagnostics").json()
    assert any(event["event"] == "context.references" for event in diagnostic["context"]["events"])
    assert secret not in str(diagnostic)
    # Historical source calls are not copied onto the target ledger.
    assert connection.get(f"/api/v1/conversations/{target}/usage").json()["total"]["calls"] == 4


def test_frozen_binding_removal_deletion_and_failed_messages_stop_without_model_call(client):
    from app.conversation_references import context

    connection, app = client
    source, target = create(connection), create(connection)
    ask(connection, source, "受权资料")
    assert update(connection, target, [source]).status_code == 200
    run = ask(connection, target, "正常问题")

    async def inspect(identifier):
        async with app.state.db.pool.connection() as db:
            return await context(db, target, (await app.state.db.fetch(
                "SELECT owner_id FROM conversations WHERE id=%s", (target,)))[0]["owner_id"], run=identifier)

    assert "受权资料" in connection.portal.call(inspect, run)[0]
    assert update(connection, target, [], 1).status_code == 200
    with pytest.raises(APIError, match="引用"):
        connection.portal.call(inspect, run)
    assert update(connection, target, [source], 2).status_code == 200
    run = ask(connection, target, "另一轮")
    assert connection.delete(f"/api/v1/conversations/{source}", headers=ORIGIN).status_code == 200
    with pytest.raises(APIError, match="引用"):
        connection.portal.call(inspect, run)
    assert connection.get(f"/api/v1/conversations/{target}/references").json()["items"] == []
    # Imported/old API-created runs without an explicit frozen selection get no implicit references.
    async def clear_snapshot():
        await app.state.db.execute("DELETE FROM conversation_reference_runs WHERE run_id=%s", (run,))
    connection.portal.call(clear_snapshot)
    assert connection.portal.call(inspect, run) == ("", 0)


def test_large_source_rejected_without_changing_existing_binding(client):
    connection, _ = client
    source, target = create(connection), create(connection)
    ask(connection, source, "原资料")
    assert update(connection, target, [source]).status_code == 200
    ask(connection, source, "长" * 32000)
    response = update(connection, target, [source], 1, refresh_source=source)
    assert response.status_code == 413, response.text
    assert connection.get(f"/api/v1/conversations/{target}/references").json()["revision"] == 1


@pytest.mark.parametrize("change", ["remove", "delete", "revoke"])
def test_change_after_first_response_stops_tool_and_next_model_call(client, change):
    from langchain_core.messages import AIMessage

    from tests.unit_tests.test_harness import ToolModel

    connection, app = client
    source, target = create(connection), create(connection)
    ask(connection, source, "撤权前的本人资料")
    assert update(connection, target, [source]).status_code == 200
    prompts = []

    class ChangeModel(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            if change == "remove":
                await app.state.db.execute("DELETE FROM conversation_references WHERE conversation_id=%s", (target,))
            elif change == "delete":
                await app.state.db.execute("DELETE FROM conversations WHERE id=%s", (source,))
            else:
                await app.state.db.execute(
                    "UPDATE users SET enabled=false WHERE id=(SELECT owner_id FROM conversations WHERE id=%s)",
                    (target,))
            return result

    model = ChangeModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "text_length", "id": "reference-tool", "args": {"text": "abc"}}],
                  usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}),
        AIMessage(content="不得执行第二次模型"),
    ])
    app.state.runtime.model_factory = lambda _: model
    run = connection.post(f"/api/v1/conversations/{target}/runs", headers=ORIGIN,
                          json={"model_id": "test", "message": "检验边界"}).json()["run_id"]
    # A revoked account cannot inspect even its own run through the public endpoint.
    import time
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row = connection.portal.call(app.state.db.fetch,
                                     "SELECT status,error_code FROM task_runs WHERE id=%s", (run,))[0]
        if row["status"] == "FAILED":
            break
        time.sleep(.02)
    assert row["status"] == "FAILED"
    assert row["error_code"] == ("ACCOUNT_UNAVAILABLE" if change == "revoke" else "REFERENCE_CHANGED")
    assert len(prompts) == 1 and "撤权前的本人资料" in str(prompts[0])
    assert not connection.portal.call(app.state.db.fetch,
        "SELECT 1 FROM run_events WHERE run_id=%s AND event='tool.completed'", (run,))
    ledger = connection.portal.call(app.state.db.fetch,
        "SELECT status,input_tokens,output_tokens FROM model_usage WHERE run_id=%s", (run,))
    assert ledger == [{"status": "KNOWN", "input_tokens": 10, "output_tokens": 3}]


def test_reference_text_reaches_real_local_stream_and_only_target_call_is_charged(client):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.graphs import resolve_model
    from app.models import ModelInput

    connection, app = client
    source, target = create(connection), create(connection)
    marker = "本机引用协议-" + uuid4().hex
    ask(connection, source, marker)
    assert update(connection, target, [source]).status_code == 200
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            chunks = [
                {"id": "reference-wire", "object": "chat.completion.chunk", "created": 1, "model": "local",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": "已读取引用"},
                              "finish_reason": None}]},
                {"id": "reference-wire", "object": "chat.completion.chunk", "created": 1, "model": "local",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"id": "reference-wire", "object": "chat.completion.chunk", "created": 1, "model": "local",
                 "choices": [], "usage": {"prompt_tokens": 37, "completion_tokens": 5, "total_tokens": 42}},
            ]
            body = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    model = None
    try:
        model = connection.portal.call(app.state.models.save, ModelInput(
            name="引用本机协议-" + uuid4().hex, model="local", api_key="local-only", max_output_tokens=128,
            base_url=f"http://127.0.0.1:{server.server_port}/v1"))
        app.state.runtime.model_factory = resolve_model
        result = connection.post(f"/api/v1/conversations/{target}/runs", headers=ORIGIN,
                                 json={"model_id": model, "message": "按照资料回答"})
        assert result.status_code == 202, result.text
        run = result.json()["run_id"]
        assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
        assert len(calls) == 1 and marker in str(calls[0]["messages"])
        assert calls[0]["stream"] and calls[0]["stream_options"]["include_usage"]
        total = connection.get(f"/api/v1/conversations/{target}/usage").json()["total"]
        assert total == {"calls": 1, "reported_calls": 1, "unknown_calls": 0, "pending_calls": 0,
                         "input_tokens": 37, "output_tokens": 5}
        history = connection.get(f"/api/v1/conversations/{target}/messages").json()["items"]
        assert marker not in str(history)
    finally:
        if model:
            connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (model,))
        server.shutdown()
        server.server_close()
        worker.join(3)


def test_binding_loss_immediately_before_hidden_summary_prevents_even_that_request(client, monkeypatch):
    from deepagents.middleware.summarization import SummarizationMiddleware

    connection, app = client
    source, target = create(connection), create(connection)
    ask(connection, source, "被撤销的资料")
    for index in range(2):
        ask(connection, target, "已有历史" + str(index))
    assert update(connection, target, [source]).status_code == 200
    original = SummarizationMiddleware._acreate_summary
    reached = []

    async def lose_binding(middleware, messages):
        await app.state.db.execute("DELETE FROM conversation_references WHERE conversation_id=%s", (target,))
        return await original(middleware, messages)

    monkeypatch.setattr(SummarizationMiddleware, "_acreate_summary", lose_binding)
    monkeypatch.setattr(importlib.import_module("deepagents.graph"), "create_summarization_middleware",
        lambda model, backend: SummarizationMiddleware(model=model, backend=backend,
                                                       trigger=("messages", 4), keep=("messages", 2)))

    class InspectModel(TestModel):
        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            reached.append(messages)
            return "不得发出摘要请求"

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            reached.append(messages)
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk

    app.state.runtime.model_factory = lambda _: InspectModel(responses=["unused"])
    response = connection.post(f"/api/v1/conversations/{target}/runs", headers=ORIGIN,
                              json={"model_id": "test", "message": "触发压缩"})
    run = response.json()["run_id"]
    assert "run.failed" in connection.get(f"/api/v1/runs/{run}/events").text
    assert reached == []
    assert connection.get(f"/api/v1/runs/{run}").json()["error_code"] == "REFERENCE_CHANGED"
    assert not connection.portal.call(app.state.db.fetch, "SELECT 1 FROM model_usage WHERE run_id=%s", (run,))
