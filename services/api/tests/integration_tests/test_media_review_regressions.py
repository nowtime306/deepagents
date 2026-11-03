"""Regression evidence for independent review R-MED-005 through 008 and maintenance/path fencing."""

import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from test_api import ORIGIN
from test_api import client as client
from test_expansion_media_costs import prepare

from app.errors import APIError
from app.extra_cost_api import Reconcile, reconcile, record_charge
from app.extra_cost_budget import reserve, settle
from app.media_retrieval_video import directory, index_frame, private_path, recover


def model(c, app):
    key = str(uuid4())
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES(%s,%s,'mm','http://unused','','multimodal_embedding','dashscope_multimodal')",
        (key, key),
    )
    return key


@pytest.mark.parametrize("other", ["different", "same", "provider", "manual"])
def test_settle_waiters_reread_confirmed_state(client, monkeypatch, other):
    c, app, owner = prepare(client)
    key = model(c, app)
    price = c.post(
        "/api/v1/operations/extra-costs/prices",
        headers=ORIGIN,
        json={
            "model_id": key,
            "item": "seconds",
            "unit": "audio_second",
            "currency": "CNY",
            "unit_price": "1",
            "source": "review fixture",
            "effective_at": "2020-01-01T00:00:00Z",
        },
    ).json()["id"]
    call = uuid4()
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) "
        "VALUES(%s,%s,%s,'resource',%s,'UNKNOWN')",
        (call, call, owner, key),
    )

    async def reserve_once():
        async with app.state.db.pool.connection() as connection:
            await reserve(connection, owner, call, key, "audio_second", Decimal(10))

    c.portal.call(reserve_once)
    usage = c.portal.call(app.state.db.fetch, "SELECT id FROM extra_cost_usage WHERE call_id=%s", (call,))[0]["id"]
    original = AsyncConnection.execute
    arrivals, event = [], None

    async def admin(request):
        return {"id": UUID(owner)}

    monkeypatch.setattr("app.extra_cost_api.administrator", admin)

    async def race():
        nonlocal event
        event = asyncio.Event()

        async def execute(connection, query, *args, **kwargs):
            if "pg_advisory_xact_lock(710032" in str(query):
                arrivals.append(id(connection))
                if len(arrivals) == 2:
                    event.set()
                await asyncio.wait_for(event.wait(), 3)
            return await original(connection, query, *args, **kwargs)

        monkeypatch.setattr(AsyncConnection, "execute", execute)

        async def first():
            async with app.state.db.pool.connection() as connection:
                await settle(connection, call, "audio_second", Decimal(1))

        async def second():
            if other == "provider":
                await record_charge(app.state.db, UUID(owner), call, UUID(price), Decimal(2))
            elif other == "manual":
                await reconcile(usage, Reconcile(quantity=2, note="independent invoice"), SimpleNamespace(app=app))
            else:
                async with app.state.db.pool.connection() as connection:
                    await settle(connection, call, "audio_second", Decimal(1 if other == "same" else 2))

        return await asyncio.gather(first(), second(), return_exceptions=True)

    results = c.portal.call(race)
    assert len(arrivals) == 2
    assert sum(not isinstance(r, Exception) for r in results) == (2 if other == "same" else 1), results
    if other != "same":
        assert sum(isinstance(r, APIError) and r.status == 409 for r in results) == 1
    rows = c.portal.call(
        app.state.db.fetch,
        "SELECT u.quantity,u.amount,h.actual FROM extra_cost_usage u JOIN extra_cost_holds h "
        "USING(call_id,item) WHERE u.call_id=%s",
        (call,),
    )
    assert rows[0]["amount"] == rows[0]["actual"] == rows[0]["quantity"]


@pytest.mark.parametrize("change", ["revoke", "disable", "revision", "cancel", "delete"])
def test_video_response_rechecks_rights_model_and_job(client, monkeypatch, change):
    c, app, owner = prepare(client)
    key = model(c, app)
    kb = c.post("/api/v1/capabilities/knowledge", json={"name": uuid4().hex}, headers=ORIGIN).json()["id"]
    assert (
        c.post(f"/api/v1/capabilities/knowledge/{kb}/image-index", json={"model_id": key}, headers=ORIGIN).status_code
        == 201
    )
    job_id = uuid4()
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO knowledge_video_jobs(id,knowledge_id,owner_id,model_id,model_revision,name,size,"
        "interval_seconds,max_frames,status) VALUES(%s,%s,%s,%s,1,'fixture',1,1,1,'RUNNING')",
        (job_id, kb, owner, key),
    )
    job = c.portal.call(app.state.db.fetch, "SELECT * FROM knowledge_video_jobs WHERE id=%s", (job_id,))[0]
    folder = directory(app.state.settings, job_id)
    folder.mkdir(parents=True)
    path = folder / "frame.png"
    path.write_bytes(b"png fixture")

    async def embedding(config, content):
        if change == "revoke":
            await app.state.db.execute("UPDATE users SET is_admin=false,role_id='viewer' WHERE id=%s", (owner,))
        elif change == "disable":
            await app.state.db.execute("UPDATE model_configs SET enabled=false WHERE id=%s", (key,))
        elif change == "revision":
            await app.state.db.execute("UPDATE model_configs SET revision=revision+1 WHERE id=%s", (key,))
        elif change == "cancel":
            await app.state.db.execute("UPDATE knowledge_video_jobs SET cancel_requested=true WHERE id=%s", (job_id,))
        else:
            await app.state.db.execute("DELETE FROM knowledge_bases WHERE id=%s", (kb,))
        return [1.0, 0.0]

    monkeypatch.setattr("app.media_retrieval_video.vectorize", embedding)
    with pytest.raises(RuntimeError, match="VIDEO_"):
        c.portal.call(index_frame, app.state, job, path, 0)
    assert not c.portal.call(app.state.db.fetch, "SELECT * FROM knowledge_video_frames WHERE job_id=%s", (job_id,))
    assert not c.portal.call(app.state.db.fetch, "SELECT * FROM knowledge_images WHERE knowledge_id=%s", (kb,))
    assert len(c.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (job_id,))) == 1


def test_media_models_and_late_charge_use_call_occurrence_period(client):
    c, app, owner = prepare(client)
    key = model(c, app)
    path = "/api/v1/operations/extra-costs"
    available = c.get(path + "/models").json()["items"]
    assert any(row["id"] == key and row["kind"] == "multimodal_embedding" for row in available)
    assert all(set(row) == {"id", "name", "kind", "enabled"} for row in available)
    price = c.post(
        path + "/prices",
        headers=ORIGIN,
        json={
            "model_id": key,
            "item": "late",
            "unit": "image",
            "currency": "CNY",
            "unit_price": "1",
            "source": "fixture",
            "effective_at": "2020-01-01T00:00:00Z",
        },
    ).json()["id"]
    calls = [uuid4(), uuid4(), uuid4()]
    start = datetime.fromisoformat("2026-10-01T00:00:00+08:00")
    end = datetime.fromisoformat("2026-11-01T00:00:00+08:00")
    for call, when in zip(calls, [start, end - timedelta(microseconds=1), end], strict=True):
        c.portal.call(
            app.state.db.execute,
            "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status,created_at) "
            "VALUES(%s,%s,%s,'resource',%s,'UNKNOWN',%s)",
            (call, call, owner, key, when),
        )
        c.portal.call(record_charge, app.state.db, UUID(owner), call, UUID(price), Decimal(1))
    body = {
        "rows": [
            {"call_id": str(uuid4()), "item": "late", "currency": "CNY", "amount": "1", "billed_at": start.isoformat()}
        ],
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "model_ids": [key],
        "currency": "CNY",
    }
    response = c.post(path + "/bills/preview", headers=ORIGIN, json=body)
    assert response.status_code == 200, response.text
    missing = {row["call_id"] for row in response.json()["items"] if row["status"] == "provider_missing"}
    assert missing == {str(calls[0]), str(calls[1])}
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false WHERE id=%s", (owner,))
    visible = c.get(path + "/models").json()["items"]
    assert {row["id"] for row in visible} == {key}


def test_unknown_cleanup_respects_maintenance_and_service_fence(client, monkeypatch):
    c, app, owner = prepare(client)
    key = model(c, app)
    job = uuid4()
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO knowledge_video_jobs(id,owner_id,model_id,model_revision,job_kind,name,size,"
        "interval_seconds,max_frames,status,holds_memory,create_attempted) "
        "VALUES(%s,%s,%s,1,'probe','fixture',1,1,1,'UNKNOWN',true,true)",
        (job, owner, key),
    )
    calls = []

    async def stop(value):
        calls.append(value)

    monkeypatch.setattr("app.media_retrieval_video.stop", stop)
    c.portal.call(app.state.db.execute, "UPDATE platform_maintenance SET active=true WHERE singleton")
    try:
        assert c.portal.call(partial(recover, app.state, startup=True)) is False
        assert calls == []
    finally:
        c.portal.call(app.state.db.execute, "UPDATE platform_maintenance SET active=false WHERE singleton")

    class LostFence:
        async def check(self):
            raise RuntimeError("FENCE_LOST")

    previous = getattr(app.state.db, "service_fence", None)
    app.state.db.service_fence = LostFence()
    try:
        with pytest.raises(RuntimeError, match="FENCE_LOST"):
            c.portal.call(recover, app.state)
        assert calls == []
    finally:
        app.state.db.service_fence = previous

    # Hold the exclusive maintenance GATE: recovery must not call stop until released.
    async def gated():
        async with app.state.db.pool.connection() as lock:
            await lock.execute("SELECT pg_advisory_xact_lock(710035)")
            task = asyncio.create_task(recover(app.state))
            await asyncio.sleep(0.05)
            assert not task.done() and calls == []
            await lock.commit()
        assert await asyncio.wait_for(task, 3)

    c.portal.call(gated)
    assert len(calls) == 1

    async def hold_through_stop():
        entered, release, exclusive = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow_stop(value):
            entered.set()
            await release.wait()

        monkeypatch.setattr("app.media_retrieval_video.stop", slow_stop)
        await app.state.db.execute(
            "UPDATE knowledge_video_jobs SET status='UNKNOWN',holds_memory=true WHERE id=%s", (job,)
        )
        task = asyncio.create_task(recover(app.state))
        await asyncio.wait_for(entered.wait(), 3)

        async def maintenance_lock():
            async with app.state.db.pool.connection() as lock:
                await lock.execute("SELECT pg_advisory_xact_lock(710035)")
                exclusive.set()

        maintenance = asyncio.create_task(maintenance_lock())
        try:
            await asyncio.sleep(0.05)
            assert not exclusive.is_set()
        finally:
            release.set()
            await asyncio.wait_for(task, 3)
            await asyncio.wait_for(maintenance, 3)
        assert exclusive.is_set()

    c.portal.call(hold_through_stop)


@pytest.mark.parametrize("part", ["knowledge-videos", "knowledge-images", "data-parent"])
def test_media_path_rejects_ancestor_junction(tmp_path, part):
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("unchanged")
    base = tmp_path / "data"
    base.mkdir()
    link = tmp_path / "redirect" if part == "data-parent" else base / part
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    assert result.returncode == 0
    try:
        settings = SimpleNamespace(data_dir=link / "nested" if part == "data-parent" else base)
        with pytest.raises(APIError) as error:
            if part == "knowledge-images":
                private_path(settings, "knowledge-images", str(uuid4()))
            else:
                directory(settings, uuid4())
        assert error.value.code == "MEDIA_PATH_UNSAFE"
        assert sentinel.read_text() == "unchanged" and len(list(outside.iterdir())) == 1
    finally:
        link.rmdir()


def test_zero_price_cannot_accept_different_reported_quantity(client):
    c, app, owner = prepare(client)
    key = model(c, app)
    c.post(
        "/api/v1/operations/extra-costs/prices",
        headers=ORIGIN,
        json={
            "model_id": key,
            "item": "free",
            "unit": "image",
            "currency": "CNY",
            "unit_price": "0",
            "source": "fixture",
            "effective_at": "2020-01-01T00:00:00Z",
        },
    )
    call = uuid4()

    async def run():
        async with app.state.db.pool.connection() as connection:
            await connection.execute(
                "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) "
                "VALUES(%s,%s,%s,'resource',%s,'UNKNOWN')",
                (call, call, owner, key),
            )
            await reserve(connection, owner, call, key, "image", Decimal(2))
            await settle(connection, call, "image", Decimal(1))
        async with app.state.db.pool.connection() as connection:
            with pytest.raises(APIError) as error:
                await settle(connection, call, "image", Decimal(2))
            assert error.value.code == "EXTRA_CHARGE_CONFLICT"

    c.portal.call(run)


@pytest.mark.parametrize(
    "case",
    [
        "start",
        "end_minus",
        "wrong_model",
        "before",
        "end",
        "currency",
        "foreign",
        "unknown",
        "no_fee_outside",
        "no_fee_inside",
    ],
)
def test_declared_period_rejects_known_scope_mismatch_and_preserves_private_ids(client, case):
    c, app, owner = prepare(client)
    base = "/api/v1/operations/extra-costs"
    key, other_model = model(c, app), model(c, app)
    start = datetime.fromisoformat("2026-10-30T00:00:00+08:00")
    end = start + timedelta(days=1)
    call, call_owner = uuid4(), UUID(owner)
    actual_model = other_model if case == "wrong_model" else key
    when = (
        start - timedelta(microseconds=1)
        if case in {"before", "no_fee_outside"}
        else end
        if case == "end"
        else end - timedelta(microseconds=1)
        if case == "end_minus"
        else start
    )
    if case == "foreign":
        call_owner = uuid4()
        c.portal.call(
            app.state.db.execute,
            "INSERT INTO users(id,username,password_hash) SELECT %s,%s,password_hash FROM users WHERE id=%s",
            (call_owner, uuid4().hex, owner),
        )
        actual_model, when = other_model, start - timedelta(days=20)
    if case != "unknown":
        c.portal.call(
            app.state.db.execute,
            "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status,created_at) "
            "VALUES(%s,%s,%s,'resource',%s,'UNKNOWN',%s)",
            (call, call, call_owner, actual_model, when),
        )
        if not case.startswith("no_fee"):
            price = c.post(
                base + "/prices",
                headers=ORIGIN,
                json={
                    "model_id": actual_model,
                    "item": "scope",
                    "unit": "image",
                    "currency": "USD" if case == "currency" else "CNY",
                    "unit_price": "1",
                    "source": "review scope fixture",
                    "effective_at": "2020-01-01T00:00:00Z",
                },
            ).json()["id"]
            c.portal.call(record_charge, app.state.db, call_owner, call, UUID(price), Decimal(1))
    row = {"call_id": str(call), "item": "scope", "currency": "CNY", "amount": "1", "billed_at": start.isoformat()}
    # UTC period and +08 provider time describe the same half-open instant interval.
    body = {
        "rows": [row],
        "model_ids": [key],
        "currency": "CNY",
        "period_start": start.astimezone(UTC).isoformat(),
        "period_end": end.astimezone(UTC).isoformat(),
    }
    preview = c.post(base + "/bills/preview", headers=ORIGIN, json=body)
    outside = case in {"wrong_model", "before", "end", "currency", "no_fee_outside"}
    if outside:
        assert preview.status_code == 422, preview.text
        assert preview.json()["error"]["code"] == "BILL_SCOPE_MISMATCH"
        assert c.post(base + "/bills/import", headers=ORIGIN, json=body).status_code == 422
    else:
        assert preview.status_code == 200, preview.text
        expected = "match" if case in {"start", "end_minus"} else "platform_missing"
        assert preview.json()["items"][0]["status"] == expected
    # Explicit ID-only mode retains its prior semantics, including known calls outside a named period/model.
    explicit = c.post(base + "/bills/preview", headers=ORIGIN, json={"rows": [row]})
    assert explicit.status_code == 200, explicit.text
    expected = (
        "platform_missing"
        if case in {"foreign", "unknown", "no_fee_inside", "no_fee_outside"}
        else "currency_mismatch"
        if case == "currency"
        else "match"
    )
    assert explicit.json()["items"][0]["status"] == expected
