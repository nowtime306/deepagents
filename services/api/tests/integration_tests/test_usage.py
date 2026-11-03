"""Price snapshots, unknown usage, audit redaction and private ledger scope."""

from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from workflow_fixtures import ledger_run

from app.usage import UsageRecorder


def test_usage_price_snapshot_unknown_statistics_and_scope(client):
    connection, app = client
    user = connection.portal.call(app.state.db.fetch, "SELECT id FROM users ORDER BY created_at DESC LIMIT 1")[0]["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user,))
    url = "/api/v1/operations/rates/test"
    assert (
        connection.put(
            url, json={"input_per_million": "2", "output_per_million": "4", "currency": "CNY"}, headers=ORIGIN
        ).status_code
        == 200
    )
    recorder = UsageRecorder(
        app.state.db, user, connection.portal.call(ledger_run, app.state.db, user), "test", "workflow"
    )
    call = uuid4()

    async def begin():
        await recorder.on_chat_model_start({}, [], run_id=call)

    connection.portal.call(begin)
    connection.put(url, json={"input_per_million": "20", "output_per_million": "40", "currency": "USD"}, headers=ORIGIN)
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="test", usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
                    )
                )
            ]
        ]
    )

    async def complete():
        await recorder.on_llm_end(response, run_id=call)

    connection.portal.call(complete)
    rows = connection.get("/api/v1/operations/usage").json()["items"]
    assert rows[0]["estimated_cost"] == "0.000400000" and rows[0]["currency"] == "CNY"

    async def unknown():
        other = uuid4()
        await recorder.on_chat_model_start({}, [], run_id=other)
        await recorder.on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=AIMessage(content="no statistics"))]]), run_id=other
        )

    connection.portal.call(unknown)
    assert connection.get("/api/v1/operations/usage").json()["items"][0]["estimated_cost"] is None
    logs = connection.get("/api/v1/operations/audit").json()["items"]
    assert logs and all("content" not in log and "token" not in log for log in logs)
    other = "ledger-private-" + str(uuid4())
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get("/api/v1/operations/usage").json()["items"] == []
    assert (
        connection.put(
            url, json={"input_per_million": "0", "output_per_million": "0", "currency": "CNY"}, headers=ORIGIN
        ).status_code
        == 403
    )


def test_model_revision_guard_prevents_new_provider_calls(client):
    import pytest

    from app.errors import APIError

    connection, app = client
    user = connection.portal.call(app.state.db.fetch, "SELECT id FROM users ORDER BY created_at DESC LIMIT 1")[0]["id"]
    revision = connection.portal.call(app.state.db.fetch, "SELECT revision FROM model_configs WHERE id='test'")[0][
        "revision"
    ]
    recorder = UsageRecorder(app.state.db, user, uuid4(), "test", "ask", revision=revision)
    call = uuid4()

    async def begin():
        await recorder.on_chat_model_start({}, [], run_id=call)

    connection.portal.call(begin)
    row = connection.portal.call(
        app.state.db.fetch, "SELECT model_revision FROM model_usage WHERE call_id=%s", (call,)
    )[0]
    assert row["model_revision"] == revision
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET revision=revision+1 WHERE id='test'")
    with pytest.raises(APIError):
        connection.portal.call(begin)
    assert recorder.raise_error
