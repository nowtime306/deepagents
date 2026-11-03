"""Price snapshots, unknown usage, audit redaction and private ledger scope."""

from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from workflow_fixtures import ledger_run

from app.errors import APIError
from app.resource_runtime import resource_run
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


def test_frozen_model_maintenance_continues_but_live_disable_blocks_provider_and_usage(client):
    connection, app = client
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])
    provider_calls = []
    call_ids = [uuid4() for _ in range(3)]

    async def scenario():
        run = await ledger_run(app.state.db, user)
        async with resource_run(app.state.db, user, "workflow", run):
            original = await app.state.models.get("test")
            recorder = UsageRecorder(app.state.db, user, run, "test", "workflow", revision=original.revision)

            def provider(configuration):
                class Observed(FakeMessagesListChatModel):
                    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
                        provider_calls.append((configuration.revision, configuration.temperature))
                        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

                return Observed(responses=[AIMessage(content="controlled", usage_metadata={
                    "input_tokens": 2, "output_tokens": 1, "total_tokens": 3})])

            async def call(identifier):
                configuration = await app.state.models.get("test")
                return await provider(configuration).ainvoke(
                    "controlled request", config={"callbacks": [recorder], "run_id": identifier})

            try:
                await call(call_ids[0])
                await app.state.db.execute(
                    "UPDATE model_configs SET revision=revision+1,temperature=%s WHERE id='test'", (0.314159,))
                await call(call_ids[1])
                assert provider_calls == [(original.revision, original.temperature)] * 2
                rows = await app.state.db.fetch(
                    "SELECT call_id,model_revision,status FROM model_usage WHERE run_id=%s ORDER BY call_id", (run,))
                assert {row["call_id"] for row in rows} == set(call_ids[:2])
                assert all(row["model_revision"] == original.revision and row["status"] == "KNOWN" for row in rows)
                await app.state.db.execute("UPDATE model_configs SET enabled=false WHERE id='test'")
                # Keep the already-built provider: the live callback must stop it even without another registry read.
                with pytest.raises(APIError) as denied:
                    await provider(original).ainvoke(
                        "blocked request", config={"callbacks": [recorder], "run_id": call_ids[2]})
                assert denied.value.code == "MODEL_NOT_FOUND"
                assert provider_calls == [(original.revision, original.temperature)] * 2
                after = await app.state.db.fetch(
                    "SELECT call_id,model_revision,status FROM model_usage WHERE run_id=%s ORDER BY call_id", (run,))
                assert after == rows
            finally:
                await app.state.db.execute(
                    "UPDATE model_configs SET enabled=true,temperature=%s WHERE id='test'", (original.temperature,))

    connection.portal.call(scenario)
