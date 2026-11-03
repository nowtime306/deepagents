"""Real database and local signed-wire regressions for independent review R-ID-001..005."""

import asyncio
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from test_api import ORIGIN
from test_api import client as client
from test_identity_wire import local_server as local_server
from test_identity_wire import prepare

from app.errors import APIError
from app.identity_api import totp
from app.identity_delivery import deliver_one, start_delivery, stop_delivery
from app.maintenance import GATE
from app.models import CredentialVault
from app.security import COOKIE, token_digest


def provider(c, state):
    identifier = str(uuid4())
    body = {
        "name": "审查本机来源",
        "issuer": state["origin"],
        "client_id": "local-client",
        "enabled": True,
        "allow_registration": True,
    }
    assert c.put(f"/api/v1/admin/identity/providers/{identifier}", headers=ORIGIN, json=body).status_code == 200
    return identifier, body


def begin(c, state, identifier, *, bind=False):
    result = c.post(
        f"/api/v1/auth/identity/{identifier}/begin",
        headers=ORIGIN,
        json={"bind": bind, "password": "integration-password"} if bind else {},
    )
    assert result.status_code == 200
    query = parse_qs(urlsplit(result.json()["url"]).query)
    state.update(nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    return query["state"][0]


def finish(c, state_token):
    return c.get(
        "/api/v1/auth/identity/callback", params={"state": state_token, "code": "local-only"}, follow_redirects=False
    )


def sms(c, state):
    assert (
        c.put(
            "/api/v1/admin/identity/channels/sms",
            headers=ORIGIN,
            json={
                "name": "审查签名短信",
                "enabled": True,
                "configuration": json.dumps({"url": state["origin"] + "/sms", "secret": "local-signature"}),
            },
        ).status_code
        == 200
    )


def contact(c, state, address):
    response = c.post(
        "/api/v1/auth/security/contacts",
        headers=ORIGIN,
        json={"kind": "sms", "address": address, "password": "integration-password"},
    )
    assert response.status_code == 200
    return state["messages"][-1]["token"]


def verify(c, token):
    return c.post("/api/v1/auth/security/contacts/verify", headers=ORIGIN, json={"token": token})


def recovery(c, username):
    return c.post("/api/v1/auth/identity/recovery/request", headers=ORIGIN, json={"username": username, "kind": "sms"})


def test_issuer_immutable_and_same_subject_separate_source(client, local_server):
    c, app, owner = prepare(client)
    identifier, body = provider(c, local_server)
    assert finish(c, begin(c, local_server, identifier, bind=True)).status_code == 303
    body["issuer"] += "/tenant-b"
    reply = c.put(f"/api/v1/admin/identity/providers/{identifier}", headers=ORIGIN, json=body)
    assert reply.status_code == 409 and reply.json()["error"]["code"] == "OIDC_ISSUER_IMMUTABLE"
    local_server["origin"] += "/tenant-b"
    other, _ = provider(c, local_server)
    assert finish(c, begin(c, local_server, other)).status_code == 303
    assert c.get("/api/v1/auth/me").json()["id"] != owner
    assert c.portal.call(
        app.state.db.fetch, "SELECT owner_id FROM identity_bindings WHERE provider_id=%s", (identifier,)
    )[0]["owner_id"] == __import__("uuid").UUID(owner)


@pytest.mark.parametrize("revocation", ["session", "password"])
def test_bind_intent_cannot_outlive_session_or_password(client, local_server, revocation):
    c, app, owner = prepare(client)
    identifier, _ = provider(c, local_server)
    pending = begin(c, local_server, identifier, bind=True)
    old_cookie = c.cookies.get(COOKIE)
    if revocation == "session":
        current = next(i for i in c.get("/api/v1/auth/sessions").json()["items"] if i["current"])
        assert c.delete(f"/api/v1/auth/sessions/{current['id']}", headers=ORIGIN).status_code == 200
    else:
        assert (
            c.post(
                "/api/v1/auth/password",
                headers=ORIGIN,
                json={"current_password": "integration-password", "new_password": "new-review-password-123"},
            ).status_code
            == 200
        )
        # Retain the stale browser/session cookies, as an attacker could do.
        c.cookies.set(COOKIE, old_cookie)
    assert finish(c, pending).status_code == 401
    assert not c.portal.call(app.state.db.fetch, "SELECT 1 FROM identity_bindings WHERE provider_id=%s", (identifier,))


def test_live_bind_intent_uses_browser_state_without_strict_session_cookie(client, local_server):
    c, app, owner = prepare(client)
    identifier, _ = provider(c, local_server)
    pending = begin(c, local_server, identifier, bind=True)
    # An external IdP redirect sends Lax identity_browser, but not the Strict auth cookie.
    browser = c.cookies.get("identity_browser")
    c.cookies.clear()
    c.cookies.set("identity_browser", browser)
    assert finish(c, pending).status_code == 303
    rows = c.portal.call(
        app.state.db.fetch, "SELECT owner_id FROM identity_bindings WHERE provider_id=%s", (identifier,)
    )
    assert str(rows[0]["owner_id"]) == owner


def test_oidc_callback_maintenance_preserves_intent_and_does_not_bind(client, local_server):
    c, app, owner = prepare(client)
    identifier, _ = provider(c, local_server)
    pending = begin(c, local_server, identifier, bind=True)
    c.portal.call(app.state.db.execute, "UPDATE platform_maintenance SET active=true WHERE singleton")
    try:
        reply = finish(c, pending)
        assert reply.status_code == 503 and reply.json()["error"]["code"] == "PLATFORM_MAINTENANCE"
        assert not c.portal.call(
            app.state.db.fetch, "SELECT 1 FROM identity_bindings WHERE provider_id=%s", (identifier,)
        )
        assert c.portal.call(
            app.state.db.fetch, "SELECT 1 FROM identity_oidc_states WHERE token_hash=%s", (token_digest(pending),)
        )
    finally:
        c.portal.call(app.state.db.execute, "UPDATE platform_maintenance SET active=false WHERE singleton")
    assert finish(c, pending).status_code == 303
    bound = c.portal.call(
        app.state.db.fetch, "SELECT owner_id FROM identity_bindings WHERE provider_id=%s", (identifier,)
    )
    assert str(bound[0]["owner_id"]) == owner


def test_oidc_callback_rechecks_fence_after_external_idp_exchange(client, local_server):
    c, app, _ = prepare(client)
    identifier, _ = provider(c, local_server)
    pending = begin(c, local_server, identifier, bind=True)
    original = app.state.service_fence

    class LostDuringExchange:
        async def check(self):
            if local_server.get("token_requested"):
                raise APIError(503, "SERVICE_RESTARTING", "旧实例失效")
            await original.check()

    app.state.service_fence = LostDuringExchange()
    try:
        reply = finish(c, pending)
        assert reply.status_code == 503 and reply.json()["error"]["code"] == "SERVICE_RESTARTING"
        assert not c.portal.call(
            app.state.db.fetch, "SELECT 1 FROM identity_bindings WHERE provider_id=%s", (identifier,)
        )
    finally:
        app.state.service_fence = original


def test_pending_validates_live_challenge_and_cancels_local_or_cookie_flow(client):
    c, app, owner = prepare(client)
    user = c.get("/api/v1/auth/me").json()
    proof = {"password": "integration-password"}
    secret = c.post("/api/v1/auth/security/mfa/enroll", headers=ORIGIN, json=proof).json()["secret"]
    proof["code"] = totp(secret)
    assert c.post("/api/v1/auth/security/mfa/confirm", headers=ORIGIN, json=proof).status_code == 200
    c.cookies.clear()
    login = {"username": user["username"], "password": "integration-password"}
    reply = c.post("/api/v1/auth/login", headers=ORIGIN, json=login)
    assert reply.status_code == 202 and c.get("/api/v1/auth/me").status_code == 401
    token = reply.json()["challenge"]
    c.cookies.set("identity_challenge", token)
    assert c.get("/api/v1/auth/security/pending").json() == {"challenge": token, "mfa_required": True}
    c.portal.call(
        app.state.db.execute,
        "UPDATE identity_challenges SET expires_at=now()-interval '1 second' WHERE owner_id=%s",
        (owner,),
    )
    invalid = c.get("/api/v1/auth/security/pending")
    assert invalid.json() == {"challenge": "", "mfa_required": False}
    assert "Max-Age=0" in invalid.headers["set-cookie"]
    c.cookies.clear()
    token = c.post("/api/v1/auth/login", headers=ORIGIN, json=login).json()["challenge"]
    assert c.post("/api/v1/auth/security/pending/cancel", headers=ORIGIN, json={"challenge": token}).status_code == 200
    assert not c.portal.call(
        app.state.db.fetch, "SELECT 1 FROM identity_challenges WHERE token_hash=%s", (token_digest(token),)
    )
    c.cookies.set("identity_challenge", token)
    assert c.get("/api/v1/auth/security/pending").json() == {"challenge": "", "mfa_required": False}
    c.cookies.clear()
    token = c.post("/api/v1/auth/login", headers=ORIGIN, json=login).json()["challenge"]
    c.cookies.set("identity_challenge", token)
    assert c.post("/api/v1/auth/security/pending/cancel", headers=ORIGIN, json={}).status_code == 200
    assert not c.portal.call(
        app.state.db.fetch, "SELECT 1 FROM identity_challenges WHERE token_hash=%s", (token_digest(token),)
    )


def test_new_verified_address_revokes_old_token_and_pins_new_tokens(client, local_server):
    c, app, owner = prepare(client)
    sms(c, local_server)
    first = "+86138" + uuid4().hex[:12]
    second = "+86139" + uuid4().hex[:12]
    assert verify(c, contact(c, local_server, first)).status_code == 200
    new_proof = contact(c, local_server, second)
    username = c.get("/api/v1/auth/me").json()["username"]
    assert recovery(c, username).status_code == 200
    c.portal.call(deliver_one, app)
    old_token = local_server["messages"][-1]["token"]
    assert local_server["messages"][-1]["recipient"] == first
    assert verify(c, new_proof).status_code == 200
    assert (
        c.post(
            "/api/v1/auth/identity/recovery/reset",
            headers=ORIGIN,
            json={"token": old_token, "password": "replacement-review-password"},
        ).status_code
        == 401
    )
    assert recovery(c, username).status_code == 200
    tokens = c.portal.call(
        app.state.db.fetch,
        "SELECT address_hash FROM identity_recovery_tokens WHERE owner_id=%s AND purpose='reset'",
        (owner,),
    )
    assert tokens[0]["address_hash"] == token_digest(second.lower())
    c.portal.call(deliver_one, app)
    assert local_server["messages"][-1]["recipient"] == second


def test_contact_confirmation_and_request_reset_concurrently_preserve_current_address(client, local_server):
    c, app, owner = prepare(client)
    sms(c, local_server)
    assert verify(c, contact(c, local_server, "+86138" + uuid4().hex[:12])).status_code == 200
    new_address = "+86139" + uuid4().hex[:12]
    proof = contact(c, local_server, new_address)
    username = c.get("/api/v1/auth/me").json()["username"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation = pool.submit(verify, c, proof)
        requested = pool.submit(recovery, c, username)
        assert confirmation.result().status_code == requested.result().status_code == 200
    tokens = c.portal.call(
        app.state.db.fetch,
        "SELECT address_hash FROM identity_recovery_tokens WHERE owner_id=%s AND purpose='reset'",
        (owner,),
    )
    assert all(i["address_hash"] == token_digest(new_address.lower()) for i in tokens)
    c.portal.call(app.state.db.execute, "DELETE FROM identity_recovery_tokens WHERE owner_id=%s", (owner,))


@pytest.mark.parametrize("kind", ["email", "sms"])
def test_channel_empty_credential_preserve_replace_and_explicit_clear(client, kind):
    c, app, _ = prepare(client)
    key = "password" if kind == "email" else "secret"
    config = (
        {"host": "smtp.example.test", "sender": "a@example.test", "username": "a", "password": "old-secret"}
        if kind == "email"
        else {"url": "https://sms.example.test/send", "secret": "old-secret"}
    )
    path = "/api/v1/admin/identity/channels/" + kind

    def save(parameters=None, **kwargs):
        return c.put(
            path,
            headers=ORIGIN,
            json={
                "name": "审查渠道",
                "enabled": True,
                **({"configuration": json.dumps(parameters)} if parameters is not None else {}),
                **kwargs,
            },
        )

    assert save(config).status_code == 200
    config[key] = ""
    assert save(config).status_code == 200

    def decrypted():
        row = c.portal.call(
            app.state.db.fetch, "SELECT config_ciphertext FROM identity_channels WHERE kind=%s", (kind,)
        )[0]
        return json.loads(
            CredentialVault(app.state.settings.data_dir / "model.key", create=False)
            .decrypt(row["config_ciphertext"])
            .get_secret_value()
        )

    assert decrypted()[key] == "old-secret"
    assert "old-secret" not in c.get("/api/v1/admin/identity/channels").text
    config[key] = "replacement-secret"
    assert save(config).status_code == 200 and decrypted()[key] == "replacement-secret"
    assert save({key: 123}).status_code == 422
    assert decrypted()[key] == "replacement-secret"
    assert save(clear_credential=True).status_code == 422
    assert decrypted()[key] == "replacement-secret"
    assert save(clear_credential=True, enabled=False).status_code == 200
    assert key not in decrypted()
    assert save().status_code == 422


def test_recovery_is_async_durable_bounded_and_does_not_enumerate_slow_provider(client, local_server):
    c, app, owner = prepare(client)
    sms(c, local_server)
    assert verify(c, contact(c, local_server, "+86138" + uuid4().hex[:12])).status_code == 200
    username = c.get("/api/v1/auth/me").json()["username"]
    local_server["sms_delay"] = 1.1
    started = time.monotonic()
    known = recovery(c, username)
    known_time = time.monotonic() - started
    started = time.monotonic()
    absent = recovery(c, "missing-" + uuid4().hex)
    absent_time = time.monotonic() - started
    assert known.status_code == absent.status_code == 200 and known.json() == absent.json()
    assert known_time < 0.65 and absent_time < 0.65, (known_time, absent_time)
    assert len(local_server["messages"]) == 1, "Submission must not wait for or send to the provider"
    rows = c.portal.call(
        app.state.db.fetch,
        "SELECT d.* FROM identity_deliveries d JOIN identity_recovery_tokens t ON t.token_hash=d.token_hash "
        "WHERE t.owner_id=%s",
        (owner,),
    )
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert "+86138" not in rows[0]["payload_ciphertext"]

    # Starting a fresh lifecycle drains a persisted submission made before worker startup.
    async def lifecycle():
        start_delivery(app)
        deadline = time.monotonic() + 4
        try:
            while time.monotonic() < deadline:
                status = await app.state.db.fetch(
                    "SELECT status FROM identity_deliveries WHERE id=%s", (rows[0]["id"],)
                )
                if status and status[0]["status"] == "sent":
                    break
                await asyncio.sleep(0.04)
            else:
                pytest.fail("Persistent recovery delivery did not drain")
        finally:
            await stop_delivery(app)

    c.portal.call(lifecycle)
    assert len(local_server["messages"]) == 2 and local_server["signature_valid"]
    local_server.update(sms_delay=0, sms_fail=True)
    assert recovery(c, username).status_code == 200
    assert c.portal.call(deliver_one, app)
    unknown = c.portal.call(
        app.state.db.fetch,
        "SELECT d.status,d.error_code FROM identity_deliveries d JOIN identity_recovery_tokens t "
        "ON t.token_hash=d.token_hash WHERE t.owner_id=%s",
        (owner,),
    )
    assert unknown == [{"status": "unknown", "error_code": "DELIVERY_RESULT_UNKNOWN"}]
    assert not c.portal.call(deliver_one, app), "A possibly accepted HTTP delivery must never replay"
    # Only a definite connection-before-send failure can retry.
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        closed_port = unused.getsockname()[1]
    assert (
        c.put(
            "/api/v1/admin/identity/channels/sms",
            headers=ORIGIN,
            json={
                "name": "本机未监听接收端",
                "enabled": True,
                "configuration": json.dumps({"url": f"http://127.0.0.1:{closed_port}/sms"}),
            },
        ).status_code
        == 200
    )
    assert recovery(c, username).status_code == 200
    for _ in range(3):
        c.portal.call(
            app.state.db.execute,
            "UPDATE identity_deliveries SET available_at=now()-interval '1 second' WHERE status='pending'",
        )
        assert c.portal.call(deliver_one, app)
    failed = c.portal.call(
        app.state.db.fetch,
        "SELECT d.status,d.attempts,d.payload_ciphertext FROM identity_deliveries d "
        "JOIN identity_recovery_tokens t ON t.token_hash=d.token_hash WHERE t.owner_id=%s",
        (owner,),
    )
    assert failed == [{"status": "failed", "attempts": 3, "payload_ciphertext": ""}]
    assert not c.portal.call(deliver_one, app)


def test_delivery_maintenance_fence_gate_and_cancelled_result_never_replay(client, local_server):
    c, app, owner = prepare(client)
    sms(c, local_server)
    assert verify(c, contact(c, local_server, "+86138" + uuid4().hex[:12])).status_code == 200
    username = c.get("/api/v1/auth/me").json()["username"]
    assert recovery(c, username).status_code == 200
    c.portal.call(app.state.db.execute, "UPDATE platform_maintenance SET active=true WHERE singleton")
    try:
        with pytest.raises(APIError) as error:
            c.portal.call(deliver_one, app)
        assert error.value.code == "PLATFORM_MAINTENANCE"
    finally:
        c.portal.call(app.state.db.execute, "UPDATE platform_maintenance SET active=false WHERE singleton")

    class LostFence:
        async def check(self):
            raise APIError(503, "SERVICE_RESTARTING", "停止旧服务")

    original = app.state.service_fence
    app.state.service_fence = LostFence()
    try:
        with pytest.raises(APIError) as error:
            c.portal.call(deliver_one, app)
        assert error.value.code == "SERVICE_RESTARTING"
    finally:
        app.state.service_fence = original
    rows = c.portal.call(
        app.state.db.fetch,
        "SELECT d.* FROM identity_deliveries d JOIN identity_recovery_tokens t ON t.token_hash=d.token_hash "
        "WHERE t.owner_id=%s",
        (owner,),
    )
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 0
    local_server["sms_delay"] = 1.1

    async def cancel_inside_gate():
        task = asyncio.create_task(deliver_one(app))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = await app.state.db.fetch("SELECT status FROM identity_deliveries WHERE id=%s", (rows[0]["id"],))
            if status[0]["status"] == "sending":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Delivery did not reach sending state")
        async with await AsyncConnection.connect(app.state.settings.database_url, autocommit=True) as guard:
            acquired = await (await guard.execute("SELECT pg_try_advisory_lock(%s)", (GATE,))).fetchone()
            assert acquired[0] is False, "An external delivery must retain shared maintenance admission"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            acquired = await (await guard.execute("SELECT pg_try_advisory_lock(%s)", (GATE,))).fetchone()
            assert acquired[0] is True
            await guard.execute("SELECT pg_advisory_unlock(%s)", (GATE,))

    c.portal.call(cancel_inside_gate)
    status = c.portal.call(
        app.state.db.fetch, "SELECT status,error_code FROM identity_deliveries WHERE id=%s", (rows[0]["id"],)
    )
    assert status == [{"status": "unknown", "error_code": "DELIVERY_CANCELLED"}]
    assert not c.portal.call(deliver_one, app)
    # A crashed host's leased sending row is likewise unknown, never reclaimed for sending.
    c.portal.call(
        app.state.db.execute,
        "UPDATE identity_deliveries SET status='sending',available_at=now()-interval '1 second' WHERE id=%s",
        (rows[0]["id"],),
    )
    assert not c.portal.call(deliver_one, app)
    status = c.portal.call(
        app.state.db.fetch, "SELECT status,error_code FROM identity_deliveries WHERE id=%s", (rows[0]["id"],)
    )
    assert status == [{"status": "unknown", "error_code": "DELIVERY_INTERRUPTED"}]
