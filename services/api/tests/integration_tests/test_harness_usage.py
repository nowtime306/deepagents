"""Per-turn accounting and private draft estimates using an isolated database."""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from test_api import DATABASE, ORIGIN, TestModel, add_user, run_async
from test_api import client as client

from app.errors import APIError
from app.harness import ASK_POLICY
from app.usage import UsageRecorder
from tests.unit_tests.test_harness import ToolModel


def test_tool_rounds_are_counted_in_one_turn_and_estimates_are_private(client):
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={"title": "Harness 用量"}, headers=ORIGIN).json()["id"]
    app.state.runtime.model_factory = lambda _: ToolModel(responses=[
        AIMessage(
            content="", tool_calls=[{"id": "count", "name": "text_length", "args": {"text": "abc"}}],
            usage_metadata={"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
        ),
        AIMessage(content="三个字符", usage_metadata={"input_tokens": 200, "output_tokens": 8, "total_tokens": 208}),
    ])
    estimate = connection.post("/api/v1/usage/estimate", json={
        "conversation_id": conversation, "model_id": "test", "message": "多少字符",
    }, headers=ORIGIN)
    assert estimate.status_code == 200
    assert estimate.json()["output_reserve"] == 4096
    assert estimate.json()["input_estimate"] > 0
    assert "api_key" not in estimate.text and "instructions" not in estimate.json()
    run = connection.post(f"/api/v1/conversations/{conversation}/runs", json={
        "model_id": "test", "message": "统计 abc",
    }, headers=ORIGIN).json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
    usage = connection.get(f"/api/v1/conversations/{conversation}/usage").json()
    assert usage["total"] == {
        "calls": 2, "reported_calls": 2, "unknown_calls": 0, "pending_calls": 0,
        "input_tokens": 300, "output_tokens": 13,
    }
    assert usage["runs"][0]["run_id"] == run
    history = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
    assert all(item["run_id"] == run for item in history)

    other = "usage-other-" + str(uuid4())
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"/api/v1/conversations/{conversation}/usage").status_code == 404
    assert connection.post("/api/v1/usage/estimate", json={
        "conversation_id": conversation, "model_id": "test", "message": "不能读取",
    }, headers=ORIGIN).status_code == 404


def test_missing_provider_usage_is_not_zero(client):
    connection, app = client
    app.state.runtime.model_factory = lambda _: TestModel(responses=["没有用量元数据"])
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    run = connection.post(f"/api/v1/conversations/{conversation}/runs", json={
        "model_id": "test", "message": "你好",
    }, headers=ORIGIN).json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
    total = connection.get(f"/api/v1/conversations/{conversation}/usage").json()["total"]
    assert total["input_tokens"] is None and total["output_tokens"] is None
    assert total["unknown_calls"] == 1 and total["reported_calls"] == 0


def test_ask_budget_stops_provider_call_when_statistics_missing(client):
    import pytest

    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    recorder = UsageRecorder(app.state.db, owner, uuid4(), "test", "ask", reserve=1)
    unknown = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="unknown"))]])

    async def exercise():
        for _ in range(ASK_POLICY.model_calls):
            call = uuid4()
            await recorder.on_chat_model_start({}, [[HumanMessage(content="a")]], run_id=call)
            await recorder.on_llm_end(unknown, run_id=call)
        with pytest.raises(APIError) as rejected:
            await recorder.on_chat_model_start({}, [[HumanMessage(content="a")]], run_id=uuid4())
        assert rejected.value.code == "ASK_BUDGET_EXCEEDED"
    connection.portal.call(exercise)


def test_ask_estimated_token_budget_blocks_oversized_request(client):
    import pytest

    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    recorder = UsageRecorder(app.state.db, owner, uuid4(), "test", "ask")
    async def blocked():
        with pytest.raises(APIError) as rejected:
            await recorder.on_chat_model_start(
                {}, [[HumanMessage(content="中" * ASK_POLICY.token_limit)]], run_id=uuid4(),
            )
        assert rejected.value.code == "ASK_BUDGET_EXCEEDED"
    connection.portal.call(blocked)


def test_native_harness_call_limit_has_chinese_budget_error(client):
    connection, app = client
    model = ToolModel(responses=[
        AIMessage(
            content="", tool_calls=[{"id": f"loop-{index}", "name": "text_length",
                                    "args": {"text": "x" * (index + 1)}}],
            usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        ) for index in range(ASK_POLICY.model_calls + 1)
    ])
    app.state.runtime.model_factory = lambda _: model
    original = connection.portal.call(
        app.state.db.fetch, "SELECT max_output_tokens FROM model_configs WHERE id='test'",
    )[0]["max_output_tokens"]
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET max_output_tokens=1 WHERE id='test'")
    try:
        conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
        run = connection.post(f"/api/v1/conversations/{conversation}/runs", json={
            "model_id": "test", "message": "循环调用字符工具",
        }, headers=ORIGIN).json()["run_id"]
        events = connection.get(f"/api/v1/runs/{run}/events").text
        assert "run.failed" in events and "本轮已达到模型调用上限" in events
        row = connection.portal.call(
            app.state.db.fetch, "SELECT error_code FROM task_runs WHERE id=%s", (run,),
        )[0]
        assert row["error_code"] == "ASK_BUDGET_EXCEEDED"
        total = connection.get(f"/api/v1/conversations/{conversation}/usage").json()["total"]
        assert total["calls"] == ASK_POLICY.model_calls
        assert model.i == ASK_POLICY.model_calls
    finally:
        connection.portal.call(
            app.state.db.execute, "UPDATE model_configs SET max_output_tokens=%s WHERE id='test'", (original,),
        )
