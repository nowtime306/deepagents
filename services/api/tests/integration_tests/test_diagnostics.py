"""Private diagnostics, immutable call completion, timing and unknown provider fields."""

from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from workflow_fixtures import ledger_run

from app.usage import UsageRecorder
from tests.unit_tests.test_harness import ToolModel


def response(incoming=100, outgoing=20):
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="answer",
                        usage_metadata={
                            "input_tokens": incoming,
                            "output_tokens": outgoing,
                            "total_tokens": incoming + outgoing,
                            "input_token_details": {"cache_read": 40, "cache_creation": 0},
                            "output_token_details": {"reasoning": 10},
                        },
                    )
                )
            ]
        ]
    )


def test_timing_details_and_duplicate_callbacks_do_not_rewrite_ledger(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    recorder = UsageRecorder(
        app.state.db, owner, connection.portal.call(ledger_run, app.state.db, owner), "test", "workflow"
    )
    call = uuid4()

    async def exercise():
        await recorder.on_chat_model_start({}, [[HumanMessage(content="你好")]], run_id=call)
        await recorder.on_llm_new_token("", run_id=call)
        await recorder.on_llm_new_token("答", run_id=call)
        await recorder.on_llm_end(response(), run_id=call)

    connection.portal.call(exercise)
    first = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE call_id=%s", (call,))[0]
    assert first["cache_read_tokens"] == 40 and first["cache_creation_tokens"] == 0
    assert first["reasoning_tokens"] == 10
    assert first["input_tokens"] == 100 and first["output_tokens"] == 20
    assert first["created_at"] <= first["first_token_at"] <= first["finished_at"]

    async def duplicate():
        await recorder.on_chat_model_start({}, [[HumanMessage(content="重复回调")]], run_id=call)
        await recorder.on_llm_new_token("late", run_id=call)
        await recorder.on_llm_end(response(1000, 200), run_id=call)
        await recorder.on_llm_error(TimeoutError("private-provider-message"), run_id=call)

    connection.portal.call(duplicate)
    second = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE call_id=%s", (call,))[0]
    assert second["finished_at"] == first["finished_at"] and second["first_token_at"] == first["first_token_at"]
    assert second["input_tokens"] == 100 and second["failure_code"] is None


def test_ask_diagnostics_budget_privacy_and_legacy_snapshot(client):
    connection, app = client
    marker = "private-instructions-must-not-be-in-diagnostics"
    agent = connection.post(
        "/api/v1/agents",
        json={
            "name": "诊断专用",
            "model_id": "test",
            "instructions": marker,
        },
        headers=ORIGIN,
    ).json()["id"]
    app.state.runtime.model_factory = lambda _: ToolModel(
        responses=[
            AIMessage(
                content="完成",
                usage_metadata={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "input_token_details": {"cache_read": 30},
                    "output_token_details": {"reasoning": 5},
                },
            ),
        ]
    )
    conversation = connection.post("/api/v1/conversations", json={"agent_id": agent}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={
            "model_id": "test",
            "message": "检查",
        },
        headers=ORIGIN,
    ).json()["run_id"]
    assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
    url = f"/api/v1/runs/{run}/diagnostics"
    raw = connection.get(url)
    assert raw.status_code == 200 and marker not in raw.text and "api_key" not in raw.text
    data = raw.json()
    assert data["budget"]["token_limit"] == 64000 and data["budget"]["model_call_limit"] == 12
    assert data["budget"]["reported_tokens"] == 120
    assert data["search"]["configuration_known"] and not data["search"]["enabled"]
    assert data["calls"][0]["cache_read_tokens"] == 30 and data["calls"][0]["cache_creation_tokens"] is None
    assert data["calls"][0]["duration_ms"] >= 0
    assert connection.get("/api/v1/harness/policies").json()["work"]["model_calls"] == 40
    connection.portal.call(app.state.db.execute, "UPDATE task_runs SET harness_policy=NULL WHERE id=%s", (run,))
    assert not connection.get(url).json()["budget"]["snapshot_available"]

    other = "diagnostic-private-" + str(uuid4())
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(url).status_code == 404
    connection.cookies.clear()
    assert connection.get(url).status_code == 401


def test_failed_request_has_safe_classification_and_no_fabricated_usage(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    recorder = UsageRecorder(
        app.state.db, owner, connection.portal.call(ledger_run, app.state.db, owner), "test", "workflow"
    )
    call = uuid4()

    async def failed():
        await recorder.on_chat_model_start({}, [[HumanMessage(content="timeout")]], run_id=call)
        await recorder.on_llm_error(TimeoutError("private-provider-message"), run_id=call)

    connection.portal.call(failed)
    row = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE call_id=%s", (call,))[0]
    assert row["failure_code"] == "MODEL_REQUEST_TIMEOUT" and row["status"] == "UNKNOWN"
    assert row["input_tokens"] is None and row["reasoning_tokens"] is None
    assert row["finished_at"] is not None and "private-provider-message" not in str(row)
