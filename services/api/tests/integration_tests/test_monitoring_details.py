"""Observed process/lock readiness, real run buckets and owner-safe diagnostic links."""
import os
import platform
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from psycopg.types.json import Jsonb
from test_api import client as client

PREFIX = "/api/v1/operations/monitoring"


def test_instance_observations_distinguish_live_ready_stale_and_remote(client):
    c, app = client
    assert c.get(PREFIX+"/instances").status_code == 403
    owner = c.get("/api/v1/auth/me").json()["id"]
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    rows = c.get(PREFIX+"/instances").json()["items"]
    assert any(row["kind"] == "api" and row["live"] is True and row["ready"] is True for row in rows)
    keys = [uuid4(), uuid4()]
    async def seed():
        async with app.state.db.pool.connection() as db:
            for key, node in zip(keys, [platform.node(), "different-node"], strict=True):
                await db.execute("INSERT INTO service_instances(id,kind,process_id,backend_id) VALUES(%s,'work',%s,0)",
                                 (key, os.getpid()))
                await db.execute("INSERT INTO service_instance_metadata VALUES(%s,%s,'local-domain','test',%s)",
                                 (key, node, Jsonb([[987654, False]])))
            await db.execute("UPDATE service_instances SET heartbeat_at=now()-interval '1 minute' WHERE id=%s",
                             (keys[1],))
    c.portal.call(seed)
    try:
        result = {row["id"]: row for row in c.get(PREFIX+"/instances").json()["items"]}
        assert result[str(keys[0])]["live"] is True and result[str(keys[0])]["ready"] is False
        assert result[str(keys[1])]["live"] is None and result[str(keys[1])]["stale"] is True
    finally:
        c.portal.call(app.state.db.execute, "DELETE FROM service_instances WHERE id=ANY(%s::uuid[])", (keys,))


def test_run_trend_and_errors_are_real_bounded_and_do_not_expose_foreign_content(client):
    c, app = client
    owner = c.get("/api/v1/auth/me").json()["id"]
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    foreign = uuid4()
    c.portal.call(app.state.db.execute, "INSERT INTO users(id,username,password_hash) VALUES(%s,%s,'fixture')",
                  (foreign, str(foreign)))
    ids = [uuid4(), uuid4()]
    async def seed():
        async with app.state.db.pool.connection() as db:
            for account, key in zip([owner, foreign], ids, strict=True):
                conversation = uuid4()
                await db.execute("INSERT INTO conversations(id,owner_id,title) VALUES(%s,%s,'private-content')",
                                 (conversation, account))
                await db.execute("INSERT INTO task_runs(id,conversation_id,model_id,status,error_code,finished_at) "
                                 "VALUES(%s,%s,'test','FAILED','private-api-key-or-url',now())", (key, conversation))
    c.portal.call(seed)
    result = c.get(PREFIX+"/errors").json()
    assert "private-" not in str(result) and "owner_id" not in str(result)
    own = next(row for row in result["items"] if row["id"] == str(ids[0]))
    other = next(row for row in result["items"] if row["id"] == str(ids[1]))
    assert own["can_open"] and own["resource_id"] and own["error_code"] == "UNCLASSIFIED"
    assert not other["can_open"] and other["resource_id"] is None
    trend = c.get(PREFIX+"/run-trend?hours=1").json()["items"]
    assert sum(row["failed"] for row in trend) >= 2
    assert len(c.get(PREFIX+"/run-trend?hours=168").json()["items"]) <= 121
    assert c.get(PREFIX+"/run-trend?hours=2").status_code == 422


def test_window_usage_keeps_late_fees_in_original_call_and_half_open_boundaries(client):
    from app.monitoring import window_totals

    c, app = client
    owner = c.get("/api/v1/auth/me").json()["id"]
    end = datetime(2040, 1, 8, tzinfo=UTC)
    calls, price = [uuid4() for _ in range(6)], uuid4()
    async def exercise():
        async with app.state.db.pool.connection() as db:
            await db.execute("INSERT INTO extra_cost_prices VALUES"
                             "(%s,%s,'image','image','CNY',1,'fixture',now(),now())",
                             (price, str(price)))
            # Current bookkeeping dates must not move a call into the wrong selected window.
            for call, age, tokens in zip(calls[:4], [.5, 2, 48, 200], [10, 20, 30, 40], strict=True):
                await db.execute("INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status,input_tokens,"
                    "output_tokens,currency,estimated_cost,created_at) VALUES"
                    "(%s,%s,%s,'ask',%s,'KNOWN',%s,1,'CNY',1,%s)",
                    (call, call, owner, str(price), tokens, end-timedelta(hours=age)))
                await db.execute("INSERT INTO extra_cost_usage"
                    "(id,call_id,owner_id,item,price_id,quantity,amount,source) "
                    "VALUES(%s,%s,%s,'image',%s,1,2,'provider')", (uuid4(), call, owner, price))
            for call, stamp in [(calls[4], end-timedelta(hours=1)), (calls[5], end)]:
                await db.execute("INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status,created_at) "
                    "VALUES(%s,%s,%s,'ask',%s,'UNKNOWN',%s)", (call, call, owner, str(price), stamp))
        for hours, expected_tokens, expected_cost in [(1, 10, 3), (24, 30, 6), (168, 60, 9)]:
            result = await window_totals(app.state.db, end-timedelta(hours=hours), end)
            assert result["usage"]["input_tokens"] == expected_tokens
            assert result["unknown_cost_calls"] == 1  # Exact start included; exact end excluded.
            assert next(row["estimated_cost"] for row in result["costs"] if row["currency"] == "CNY") == expected_cost
        empty = await window_totals(app.state.db, end+timedelta(days=1), end+timedelta(days=2))
        assert empty["usage"]["calls"] == 0 and empty["usage"]["input_tokens"] == 0
    try:
        c.portal.call(exercise)
    finally:
        c.portal.call(app.state.db.execute, "DELETE FROM model_usage WHERE call_id=ANY(%s::uuid[])", (calls,))
        c.portal.call(app.state.db.execute, "DELETE FROM extra_cost_prices WHERE id=%s", (price,))


def test_window_api_preserves_decimal_cost_precision(client):
    c, app = client
    owner = c.get("/api/v1/auth/me").json()["id"]
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    before = c.get(PREFIX).json()["window"]["costs"]
    previous = next((Decimal(row["estimated_cost"] or "0") for row in before if row["currency"] == "USD"), Decimal(0))
    call = uuid4()
    amount = Decimal("1234567890123.123456789")
    c.portal.call(app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status,currency,estimated_cost) "
        "VALUES(%s,%s,%s,'ask','decimal-fixture','KNOWN','USD',%s)", (call, call, owner, amount))
    try:
        costs = c.get(PREFIX).json()["window"]["costs"]
        actual = next(row["estimated_cost"] for row in costs if row["currency"] == "USD")
        assert isinstance(actual, str) and actual == str(previous+amount)
    finally:
        c.portal.call(app.state.db.execute, "DELETE FROM model_usage WHERE call_id=%s", (call,))
