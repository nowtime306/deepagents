"""Durable Ask claims, fences, cancellation, checkpoint locks and safe collection."""

import asyncio
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, OperationalError
from psycopg.rows import dict_row
from test_api import ORIGIN, TestModel
from test_api import client as client

from app.ask_queue import AskQueue, conversation_lock
from app.ask_worker import AskWorker
from app.errors import APIError
from app.graphs import build_ask
from tests.unit_tests.test_harness import ToolModel


def queued(client):
    connection, app = client
    app.state.settings.ask_worker_enabled = True
    conversation = connection.post("/api/v1/conversations", json={"title": "持久队列"}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "检查后台"}, headers=ORIGIN
    ).json()["run_id"]
    return UUID(conversation), UUID(run)


def test_worker_claim_and_context_events_preserve_history(client):
    connection, app = client
    conversation, run = queued(client)
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "PENDING"
    worker = AskWorker(
        app.state.db, app.state.models, app.state.settings, lambda _: TestModel(responses=["独立执行完成"])
    )
    assert connection.portal.call(worker.tick)
    state = connection.get(f"/api/v1/runs/{run}").json()
    assert state["status"] == "SUCCEEDED" and state["worker_id"]
    assert "lease_token" not in state
    history = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
    assert history[-1]["content"]["data"]["content"] == "独立执行完成"
    diagnostics = connection.get(f"/api/v1/runs/{run}/diagnostics").json()
    assert diagnostics["execution"]["durable"]
    assert diagnostics["context"]["events"][0]["event"] == "context.observed"
    assert "检查后台" not in str(diagnostics)


def test_claim_race_expiration_and_stale_message_fence(client):
    connection, app = client
    conversation, run = queued(client)
    queue = AskQueue(app.state.db)

    async def race():
        claims = await asyncio.gather(queue.claim(run, uuid4()), queue.claim(run, uuid4()))
        assert sum(item is not None for item in claims) == 1
        claimed = next(item for item in claims if item)
        assert not await queue.renew(run, uuid4())
        await app.state.db.execute("UPDATE task_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (run,))
        await queue.reconcile()
        assert not await queue.claim(run, uuid4())
        with pytest.raises(APIError) as failure:
            await app.state.db.event(
                run,
                "message.completed",
                {},
                lease_token=claimed["lease_token"],
                message={
                    "id": uuid4(),
                    "conversation_id": conversation,
                    "role": "assistant",
                    "content": {"text": "不能写入"},
                },
            )
        assert failure.value.code == "ASK_LEASE_LOST"

    connection.portal.call(race)
    assert connection.get(f"/api/v1/runs/{run}").json()["error_code"] == "ASK_RESULT_UNKNOWN"
    assert len(connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]) == 1


def test_queued_cancel_does_not_need_a_live_worker(client):
    connection, _ = client
    _, run = queued(client)
    assert connection.post(f"/api/v1/runs/{run}/cancel", headers=ORIGIN).status_code == 202
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "CANCELLED"
    assert connection.post(f"/api/v1/runs/{run}/cancel", headers=ORIGIN).status_code == 409


def test_checkpoint_connection_owns_conversation_lock_and_never_reconnects(client):
    connection, app = client
    conversation, run = queued(client)
    worker = AskWorker(
        app.state.db, app.state.models, app.state.settings, lambda _: TestModel(responses=["锁释放后执行"])
    )

    async def exercise():
        guard = await AsyncConnection.connect(app.state.settings.database_url, autocommit=True, row_factory=dict_row)
        try:
            await guard.execute("SELECT pg_advisory_lock(710020,%s)", (conversation_lock(conversation),))
            assert not await worker.tick()
            saver = AsyncPostgresSaver(guard)
            await guard.close()
            with pytest.raises(OperationalError):
                await saver.aget_tuple({"configurable": {"thread_id": str(conversation)}})
            assert await worker.tick()
        finally:
            await guard.close()

    connection.portal.call(exercise)
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "SUCCEEDED"


def test_role_revocation_before_worker_call_fails_without_request(client):
    connection, app = client
    _, run = queued(client)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id='viewer' WHERE id=%s", (owner,))
    requests = []

    def model(_):
        requests.append(True)
        return TestModel(responses=["不应执行"])

    worker = AskWorker(app.state.db, app.state.models, app.state.settings, model)
    connection.portal.call(worker.tick)
    assert requests == []
    assert connection.get(f"/api/v1/runs/{run}").json()["error_code"] == "PERMISSION_REQUIRED"


def test_new_round_closes_dangling_checkpoint_without_replaying_tool(client):
    connection, app = client
    conversation, run = queued(client)
    config = {"configurable": {"thread_id": str(conversation)}}
    graph = build_ask(TestModel(responses=["读取未知执行证据后完成"]), app.state.runtime.saver)

    async def seed():
        await graph.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content="上次已中断的提问"),
                    AIMessage(
                        content="", tool_calls=[{"id": "unresolved", "name": "text_length", "args": {"text": "old"}}]
                    ),
                ]
            },
            as_node="model",
        )

    connection.portal.call(seed)
    worker = AskWorker(
        app.state.db, app.state.models, app.state.settings, lambda _: TestModel(responses=["读取未知执行证据后完成"])
    )
    assert connection.portal.call(worker.tick)
    state = connection.portal.call(graph.aget_state, config)
    results = [item for item in state.values["messages"] if isinstance(item, ToolMessage)]
    assert len(results) == 1 and results[0].tool_call_id == "unresolved" and "结果未知" in results[0].content
    events = connection.portal.call(app.state.db.fetch, "SELECT event FROM run_events WHERE run_id=%s", (run,))
    assert "checkpoint.repaired" in [item["event"] for item in events]
    assert "tool.started" not in [item["event"] for item in events]
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "SUCCEEDED"


def test_role_revoked_during_model_response_prevents_tool_and_next_model(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    _, run = queued(client)
    calls = []

    class RevokingModel(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(True)
            await app.state.db.execute("UPDATE users SET role_id='viewer' WHERE id=%s", (owner,))
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = RevokingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "after-revocation", "name": "text_length", "args": {"text": "denied"}}],
                usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
            )
        ]
    )
    worker = AskWorker(app.state.db, app.state.models, app.state.settings, lambda _: model)
    assert connection.portal.call(worker.tick)
    assert calls == [True]
    state = connection.get(f"/api/v1/runs/{run}").json()
    assert state["status"] == "FAILED" and state["error_code"] == "PERMISSION_REQUIRED"
    events = connection.portal.call(app.state.db.fetch, "SELECT event FROM run_events WHERE run_id=%s", (run,))
    assert not {"tool.started", "tool.completed"} & {item["event"] for item in events}
