"""Budget contention, immutable price snapshots and explicit unknown cost handling."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from test_api import ORIGIN
from test_api import client as client
from workflow_fixtures import ledger_run

from app.errors import APIError
from app.usage import UsageRecorder


@pytest.fixture(autouse=True)
def private_models(client):
    connection, app = client
    app.state.money_test_models = []
    yield
    connection.portal.call(
        app.state.db.execute,
        "UPDATE model_configs SET enabled=false WHERE id=ANY(%s::text[])",
        (app.state.money_test_models,),
    )


def setup(connection, app, *, amount="1", scheme="uniform"):
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    model = connection.post(
        "/api/v1/admin/models",
        headers=ORIGIN,
        json={"name": "money-" + uuid4().hex, "model": "offline", "base_url": "http://unused.invalid/v1"},
    ).json()["id"]
    app.state.money_test_models.append(model)
    price = {
        "model_id": model,
        "currency": "CNY",
        "scheme": scheme,
        "input_per_million": "10",
        "output_per_million": "20",
        "source": "本机测试价格，不是服务商真实报价",
    }
    if scheme == "split":
        price.update(cache_read_per_million="2", cache_write_per_million="15")
    added = connection.post("/api/v1/billing/prices", headers=ORIGIN, json=price)
    assert added.status_code == 201, added.text
    result = connection.put("/api/v1/billing/budgets/CNY", headers=ORIGIN, json={"amount": amount, "period_days": 30})
    assert result.status_code == 200, result.text
    root = connection.portal.call(ledger_run, app.state.db, owner)
    recorder = UsageRecorder(app.state.db, owner, root, model, "workflow", reserve=100, input_estimate=100)
    return owner, model, price, added.json()["id"], recorder


def response(*, details=True, incoming=100, outgoing=50):
    usage = {"input_tokens": incoming, "output_tokens": outgoing, "total_tokens": incoming + outgoing}
    if details:
        usage["input_token_details"] = {"cache_read": 40, "cache_creation": 10}
        usage["output_token_details"] = {"reasoning": 20}
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="offline", usage_metadata=usage))]])


def test_price_version_cache_subsets_duplicate_callbacks_and_release(client):
    connection, app = client
    _, model, price, version, recorder = setup(connection, app, scheme="split")
    call = uuid4()

    async def begin():
        await recorder.on_chat_model_start({}, [], run_id=call)
        await recorder.on_chat_model_start({}, [], run_id=call)

    connection.portal.call(begin)
    assert (
        connection.post(
            "/api/v1/billing/prices", headers=ORIGIN, json={**price, "input_per_million": "1000"}
        ).status_code
        == 201
    )

    async def finish():
        await recorder.on_llm_end(response(), run_id=call)
        await recorder.on_llm_end(response(incoming=200), run_id=call)
        await recorder.on_llm_error(TimeoutError(), run_id=call)

    connection.portal.call(finish)
    row = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE call_id=%s", (call,))[0]
    assert str(row["price_version_id"]) == version and row["input_tokens"] == 100
    # 50 ordinary + 40 cache read + 10 cache write, reasoning included in 50 output.
    assert row["estimated_cost"] == Decimal("0.001730000") and row["status"] == "KNOWN"
    holds = connection.get("/api/v1/billing/holds").json()["items"]
    assert len(holds) == 1 and holds[0]["actual"] == "0.001730000"
    assert connection.get("/api/v1/billing/calibration").json()["items"][0]["samples"] == 1
    # Removing a configured model never destroys its historic price version.
    assert connection.delete(f"/api/v1/admin/models/{model}", headers=ORIGIN).status_code == 200
    assert any(p["id"] == version for p in connection.get("/api/v1/billing/prices").json()["items"])


def test_unknown_usage_retains_money_until_single_use_invoice_reconciliation(client):
    connection, app = client
    _, _, _, _, recorder = setup(connection, app)
    call = uuid4()

    async def unknown():
        await recorder.on_chat_model_start({}, [], run_id=call)
        await recorder.on_llm_error(TimeoutError(), run_id=call)

    connection.portal.call(unknown)
    item = connection.get("/api/v1/billing/budgets").json()["items"][0]
    assert Decimal(item["frozen"]) == Decimal("0.003")
    receipt = {"actual": "0.001", "note": "本机替身账单核对，只核对金额，不补Token"}
    assert connection.post(f"/api/v1/billing/holds/{call}/reconcile", json=receipt, headers=ORIGIN).status_code == 200
    assert connection.post(f"/api/v1/billing/holds/{call}/reconcile", json=receipt, headers=ORIGIN).status_code == 409
    row = connection.portal.call(
        app.state.db.fetch, "SELECT status,input_tokens,cost_status FROM model_usage WHERE call_id=%s", (call,)
    )[0]
    assert row == {"status": "UNKNOWN", "input_tokens": None, "cost_status": "UNKNOWN"}


def test_concurrent_budget_reservations_have_only_one_winner(client):
    connection, app = client
    owner, model, _, _, _ = setup(connection, app, amount="0.004")

    async def compete():
        roots = await asyncio.gather(*(ledger_run(app.state.db, owner) for _ in range(4)))
        recorders = [
            UsageRecorder(app.state.db, owner, root, model, "workflow", reserve=100, input_estimate=100)
            for root in roots
        ]
        results = await asyncio.gather(
            *(recorder.on_chat_model_start({}, [], run_id=uuid4()) for recorder in recorders), return_exceptions=True
        )
        assert sum(result is None for result in results) == 1
        assert all(
            result is None or isinstance(result, APIError) and result.code == "MONEY_BUDGET_EXCEEDED"
            for result in results
        )

    connection.portal.call(compete)
    assert len(connection.get("/api/v1/billing/holds").json()["items"]) == 1


def test_missing_cache_detail_freezes_and_actual_overrun_blocks_next_call(client):
    connection, app = client
    _, _, _, _, recorder = setup(connection, app, amount="0.005", scheme="split")

    async def missing():
        first = uuid4()
        await recorder.on_chat_model_start({}, [], run_id=first)
        await recorder.on_llm_end(response(details=False), run_id=first)
        with pytest.raises(APIError, match="金额预算不足"):
            await recorder.on_chat_model_start({}, [], run_id=uuid4())

    connection.portal.call(missing)
    assert connection.get("/api/v1/billing/holds").json()["items"][0]["actual"] is None


def test_budget_enabled_unpriced_model_rejects_without_ledger_entry(client):
    connection, app = client
    owner, _, _, _, _ = setup(connection, app)
    root = connection.portal.call(ledger_run, app.state.db, owner)
    recorder = UsageRecorder(app.state.db, owner, root, "test", "workflow")

    async def reject():
        with pytest.raises(APIError, match="缺少生效的价格版本"):
            await recorder.on_chat_model_start({}, [], run_id=uuid4())

    connection.portal.call(reject)
    assert not connection.portal.call(app.state.db.fetch, "SELECT 1 FROM model_usage WHERE run_id=%s", (root,))


def test_reported_actual_overrun_is_kept_and_blocks_the_next_request(client):
    connection, app = client
    _, _, _, _, recorder = setup(connection, app, amount="0.005")
    call = uuid4()

    async def overrun():
        await recorder.on_chat_model_start({}, [], run_id=call)
        await recorder.on_llm_end(response(incoming=1000, outgoing=500), run_id=call)
        with pytest.raises(APIError, match="金额预算不足"):
            await recorder.on_chat_model_start({}, [], run_id=uuid4())

    connection.portal.call(overrun)
    hold = connection.get("/api/v1/billing/holds").json()["items"][0]
    assert hold["actual"] == "0.020000000" and hold["reserved"] == "0.003000000"


def test_late_reported_usage_settles_original_hold_without_a_new_request(client):
    connection, app = client
    _, _, _, _, recorder = setup(connection, app)
    call = uuid4()

    async def late():
        await recorder.on_chat_model_start({}, [], run_id=call)
        await recorder.on_llm_error(TimeoutError(), run_id=call)
        await recorder.on_llm_end(response(), run_id=call)
        await recorder.on_llm_end(response(incoming=200), run_id=call)

    connection.portal.call(late)
    item = connection.get("/api/v1/billing/holds").json()["items"][0]
    assert item["actual"] == "0.002000000" and item["status"] == "KNOWN"
    assert len(connection.get("/api/v1/billing/holds").json()["items"]) == 1
