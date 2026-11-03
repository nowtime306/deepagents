"""Actual private image queries and immutable extra-charge reconciliation with local vectors."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from test_api import ORIGIN
from test_api import client as client

from app.errors import APIError
from app.extra_cost_api import router as costs_router
from app.media_retrieval_api import router as media_router


def prepare(client):
    connection, app = client
    app.include_router(costs_router)
    app.include_router(media_router)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    return connection, app, owner


def test_extra_price_charge_unknown_and_bill_idempotency(client):
    c, app, owner = prepare(client)
    p = "/api/v1/operations/extra-costs"
    data = {
        "model_id": "test",
        "item": "search",
        "unit": "call",
        "currency": "CNY",
        "unit_price": "0.3",
        "source": "local fixture",
        "effective_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    result = c.post(p + "/prices", json=data, headers=ORIGIN)
    assert result.status_code == 201
    assert c.post(p + "/prices", json={**data, "unit": "image"}, headers=ORIGIN).status_code == 422
    assert c.post(p + "/prices", json={**data, "currency": "USD"}, headers=ORIGIN).status_code == 422
    price = result.json()["id"]
    call = str(uuid4())
    connection = app.state.db
    c.portal.call(
        connection.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) VALUES(%s,%s,%s,'ask','test','UNKNOWN')",
        (call, call, owner),
    )
    charge = {"call_id": call, "price_id": price, "quantity": "2"}
    assert c.post(p + "/usage", json=charge, headers=ORIGIN).status_code == 201
    assert Decimal(str(c.post(p + "/usage", json=charge, headers=ORIGIN).json()["amount"])) == Decimal(".6")
    assert c.post(p + "/usage", json={**charge, "quantity": "3"}, headers=ORIGIN).status_code == 409
    bill = {"rows": [{"call_id": call, "item": "search", "currency": "CNY", "amount": "0.6"}]}
    assert c.post(p + "/bills/preview", json=bill, headers=ORIGIN).json()["items"][0]["status"] == "match"
    first = c.post(p + "/bills/import", json=bill, headers=ORIGIN).json()
    again = c.post(p + "/bills/import", json=bill, headers=ORIGIN).json()
    assert first["id"] == again["id"]
    assert "match" in c.get(p + f"/bills/{first['id']}/download").text
    assert c.post(p + "/bills/preview", json={"rows": bill["rows"] * 2}, headers=ORIGIN).status_code == 422
    assert c.post(p + "/bills/parse", headers=ORIGIN, files={"file": ("x.csv", b"bad")}).status_code == 422
    unknown_call = str(uuid4())
    c.portal.call(
        connection.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) VALUES(%s,%s,%s,'ask','test','UNKNOWN')",
        (unknown_call, unknown_call, owner),
    )
    pending = c.post(p + "/usage", headers=ORIGIN, json={"call_id": unknown_call, "price_id": price, "quantity": None})
    assert pending.status_code == 201
    assert (
        c.post(
            p + "/usage", headers=ORIGIN, json={"call_id": unknown_call, "price_id": price, "quantity": "1"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            p + f"/usage/{pending.json()['id']}/reconcile",
            headers=ORIGIN,
            json={"quantity": "1", "note": "explicit manual evidence"},
        ).status_code
        == 200
    )


def test_image_query_sends_pixels_and_rejects_bad_dimension(client, monkeypatch):
    c, app, owner = prepare(client)
    model = str(uuid4())
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES(%s,%s,'mm','http://unused','', 'multimodal_embedding','dashscope_multimodal')",
        (model, model),
    )
    seen = []

    async def embed(config, content):
        seen.append(content)
        return [1.0, 0.0]

    monkeypatch.setattr("app.multimodal_api.vectorize", embed)
    monkeypatch.setattr("app.media_retrieval_api.vectorize", embed)
    kb = c.post("/api/v1/capabilities/knowledge", json={"name": "image-query-" + uuid4().hex}, headers=ORIGIN).json()[
        "id"
    ]
    path = f"/api/v1/capabilities/knowledge/{kb}"
    assert c.post(path + "/image-index", json={"model_id": model}, headers=ORIGIN).status_code == 201
    png = b"\x89PNG\r\n\x1a\nlocal"
    pic = c.post(path + "/images", headers=ORIGIN, files={"file": ("a.png", png)}).json()["id"]
    result = c.post(path + "/image-query/search", headers=ORIGIN, files={"file": ("b.png", png)})
    assert result.status_code == 200 and result.json()["items"][0]["id"] == pic
    assert "image" in seen[-1] and "text" not in seen[-1]
    assert c.post(path + "/image-query/search", headers=ORIGIN, files={"file": ("a.png", b"bad")}).status_code == 422

    async def bad(config, content):
        return [1, 0, 0]

    monkeypatch.setattr("app.media_retrieval_api.vectorize", bad)
    assert c.post(path + "/image-query/search", headers=ORIGIN, files={"file": ("a.png", png)}).status_code == 409

    async def change_index(config, content):
        await app.state.db.execute("UPDATE knowledge_image_indexes SET dimensions=3 WHERE knowledge_id=%s", (kb,))
        return [1, 0]

    monkeypatch.setattr("app.media_retrieval_api.vectorize", change_index)
    assert c.post(path + "/image-query/search", headers=ORIGIN, files={"file": ("a.png", png)}).status_code == 409


def test_joint_token_and_per_call_budget_has_one_concurrent_winner(client):
    from test_money import setup

    c, app, owner = prepare(client)
    app.state.money_test_models = []
    owner, model, _, _, recorder = setup(c, app, amount="0.5")
    added = c.post(
        "/api/v1/operations/extra-costs/prices",
        headers=ORIGIN,
        json={
            "model_id": model,
            "item": "request_fee",
            "unit": "call",
            "currency": "CNY",
            "unit_price": "0.4",
            "source": "local concurrency contract",
            "effective_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert added.status_code == 201

    async def race():
        return await asyncio.gather(
            recorder.on_chat_model_start({}, [], run_id=uuid4()),
            recorder.on_chat_model_start({}, [], run_id=uuid4()),
            return_exceptions=True,
        )

    results = c.portal.call(race)
    assert sum(r is None for r in results) == 1
    errors = [r for r in results if isinstance(r, APIError)]
    assert len(errors) == 1 and errors[0].code == "MONEY_BUDGET_EXCEEDED"
    rows = c.portal.call(app.state.db.fetch, "SELECT * FROM extra_cost_holds WHERE owner_id=%s", (owner,))
    assert len(rows) == 1 and rows[0]["reserved"] == Decimal(".4") and rows[0]["actual"] is None
    budgets = c.get("/api/v1/billing/budgets").json()["items"]
    assert Decimal(str(budgets[0]["frozen"])) > Decimal(".4")
    c.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (model,))


def test_extra_settlement_is_scoped_to_unit_and_unknown_can_be_reconciled(client):
    from app.extra_cost_budget import reserve, settle

    c, app, owner = prepare(client)
    base = "/api/v1/operations/extra-costs"
    prices = []
    for item, unit in (("once", "call"), ("duration", "audio_second")):
        prices.append(
            c.post(
                base + "/prices",
                headers=ORIGIN,
                json={
                    "model_id": "test",
                    "item": item,
                    "unit": unit,
                    "currency": "CNY",
                    "unit_price": "0.2",
                    "source": "local",
                    "effective_at": "2020-01-01T00:00:00Z",
                },
            ).json()
        )
    call = uuid4()
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) VALUES(%s,%s,%s,'ask','test','UNKNOWN')",
        (call, call, owner),
    )

    async def charges():
        async with app.state.db.pool.connection() as connection:
            await reserve(connection, owner, call, "test", "call", Decimal(1))
            await reserve(connection, owner, call, "test", "audio_second", Decimal(10))
            await settle(connection, call, "call", Decimal(1))

    c.portal.call(charges)
    rows = c.portal.call(app.state.db.fetch, "SELECT * FROM extra_cost_usage WHERE call_id=%s", (call,))
    audio = next(row for row in rows if row["item"] == "duration")
    assert audio["quantity"] is None and audio["amount"] is None
    reconciled = c.post(
        base + f"/usage/{audio['id']}/reconcile",
        headers=ORIGIN,
        json={"quantity": "3", "note": "verified local invoice"},
    )
    assert reconciled.status_code == 200
    assert Decimal(str(reconciled.json()["amount"])) == Decimal(".6")
    assert reconciled.json()["confirmed_by"] == owner
    assert (
        c.post(
            base + f"/usage/{audio['id']}/reconcile", headers=ORIGIN, json={"quantity": "4", "note": "duplicate"}
        ).status_code
        == 409
    )


def test_extra_provider_late_usage_and_price_page_are_reachable(client):
    from app.extra_cost_api import record_charge

    c, app, owner = prepare(client)
    call = uuid4()
    price = c.post(
        "/api/v1/operations/extra-costs/prices",
        headers=ORIGIN,
        json={
            "model_id": "test",
            "item": "late",
            "unit": "call",
            "currency": "CNY",
            "unit_price": "0.2",
            "source": "local",
            "effective_at": "2020-01-01T00:00:00Z",
        },
    ).json()["id"]
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) VALUES(%s,%s,%s,'ask','test','UNKNOWN')",
        (call, call, owner),
    )
    c.portal.call(record_charge, app.state.db, owner, call, UUID(price), None)
    updated = c.portal.call(record_charge, app.state.db, owner, call, UUID(price), Decimal(2))
    assert updated["amount"] == Decimal(".4")
    assert c.portal.call(record_charge, app.state.db, owner, call, UUID(price), Decimal(2))["id"] == updated["id"]
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO extra_cost_prices(id,model_id,item,unit,currency,unit_price,source,effective_at) "
        "SELECT gen_random_uuid(),'test','paging-'||n,'call','CNY',1,'local',now() FROM generate_series(1,105) n",
    )
    first = c.get("/api/v1/operations/extra-costs/prices").json()
    second = c.get("/api/v1/operations/extra-costs/prices", params={"before": first["next_cursor"]}).json()
    assert len(first["items"]) == 100 and second["items"]
    assert not ({x["id"] for x in first["items"]} & {x["id"] for x in second["items"]})


def test_bill_column_mapping_and_explicit_period_detects_missing_calls(client):
    c, app, owner = prepare(client)
    base = "/api/v1/operations/extra-costs"
    price = c.post(
        base + "/prices",
        headers=ORIGIN,
        json={
            "model_id": "test",
            "item": "period",
            "unit": "call",
            "currency": "CNY",
            "unit_price": "1",
            "source": "local",
            "effective_at": "2020-01-01T00:00:00Z",
        },
    ).json()["id"]
    calls = [str(uuid4()), str(uuid4())]
    for call in calls:
        c.portal.call(
            app.state.db.execute,
            "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,status) "
            "VALUES(%s,%s,%s,'ask','test','KNOWN')",
            (call, call, owner),
        )
        assert (
            c.post(
                base + "/usage", headers=ORIGIN, json={"call_id": call, "price_id": price, "quantity": "1"}
            ).status_code
            == 201
        )
    now = datetime.now(UTC)
    csv = f"request,service,money,ccy,stamp\nexternal-1,period,1,CNY,{now.isoformat()}\n".encode()
    parsed = c.post(base + "/bills/parse", headers=ORIGIN, files={"file": ("invoice.csv", csv)}).json()
    assert parsed["needs_mapping"] is True
    import json

    mapped = c.post(
        base + "/bills/parse",
        headers=ORIGIN,
        files={"file": ("invoice.csv", csv)},
        data={
            "mapping": json.dumps(
                {"call_id": "request", "item": "service", "amount": "money", "currency": "ccy", "billed_at": "stamp"}
            ),
            "request_ids": json.dumps({"external-1": calls[0]}),
        },
    )
    assert mapped.status_code == 200 and mapped.json()["rows"][0]["call_id"] == calls[0]
    body = {
        "rows": mapped.json()["rows"],
        "period_start": (now - timedelta(days=1)).isoformat(),
        "period_end": (now + timedelta(days=1)).isoformat(),
        "model_ids": ["test"],
        "currency": "CNY",
    }
    preview = c.post(base + "/bills/preview", headers=ORIGIN, json=body)
    assert preview.status_code == 200
    assert {row["status"] for row in preview.json()["items"]} == {"match", "provider_missing"}
    assert c.post(base + "/bills/preview", headers=ORIGIN, json={**body, "model_ids": []}).status_code == 422
    assert (
        c.post(
            base + "/bills/preview", headers=ORIGIN, json={**body, "period_end": (now + timedelta(days=40)).isoformat()}
        ).status_code
        == 422
    )
