"""Isolated account MFA and recovery routes, without changing application registration."""

import json

from starlette.requests import Request
from test_api import ORIGIN
from test_api import client as client

from app.identity_api import login_challenge, router, totp
from app.identity_oidc import router as oidc_router
from app.identity_recovery import router as recovery_router


def test_mfa_enrollment_challenge_recovery_once_and_disable(client):
    connection, app = client
    app.include_router(router)
    user = connection.get("/api/v1/auth/me").json()
    proof = {"password": "integration-password", "code": ""}
    assert connection.post("/api/v1/auth/security/mfa/enroll", headers=ORIGIN, json={"password": ""}).status_code == 401
    result = connection.post("/api/v1/auth/security/mfa/enroll", headers=ORIGIN, json=proof)
    assert result.status_code == 200
    secret = result.json()["secret"]
    proof["code"] = totp(secret)
    result = connection.post("/api/v1/auth/security/mfa/confirm", headers=ORIGIN, json=proof)
    assert result.status_code == 200
    codes = result.json()["recovery_codes"]
    assert len(codes) == 8
    rows = connection.portal.call(app.state.db.fetch, "SELECT * FROM identity_mfa WHERE owner_id=%s", (user["id"],))
    assert secret not in rows[0]["secret_ciphertext"] and codes[0] not in json.dumps(rows[0]["recovery_hashes"])

    async def challenge():
        async with app.state.db.pool.connection() as db:
            response = await login_challenge(db, Request({"type": "http", "app": app, "headers": []}), user)
            return json.loads(response.body)

    pending = connection.portal.call(challenge)
    connection.cookies.clear()
    assert connection.get("/api/v1/auth/me").status_code == 401
    assert (
        connection.post(
            "/api/v1/auth/security/challenge",
            headers=ORIGIN,
            json={"challenge": pending["challenge"], "code": totp(secret)},
        ).status_code
        == 401
    )
    response = connection.post(
        "/api/v1/auth/security/challenge", headers=ORIGIN, json={"challenge": pending["challenge"], "code": codes[0]}
    )
    assert response.status_code == 200 and connection.get("/api/v1/auth/me").status_code == 200
    assert (
        connection.post(
            "/api/v1/auth/security/challenge",
            headers=ORIGIN,
            json={"challenge": pending["challenge"], "code": codes[1]},
        ).status_code
        == 401
    )
    proof["code"] = codes[0]
    assert connection.post("/api/v1/auth/security/mfa/disable", headers=ORIGIN, json=proof).status_code == 401
    proof["code"] = codes[1]
    assert connection.post("/api/v1/auth/security/mfa/disable", headers=ORIGIN, json=proof).status_code == 200


def test_channels_admin_scope_and_recovery_no_contact(client):
    connection, app = client
    app.include_router(router)
    app.include_router(oidc_router)
    app.include_router(recovery_router)
    assert connection.get("/api/v1/admin/identity/channels").status_code == 403
    # This case explicitly exercises an unconfigured installation; other tests retain global sources.
    providers = connection.portal.call(app.state.db.fetch, "SELECT id FROM identity_providers WHERE enabled")
    channels = connection.portal.call(app.state.db.fetch, "SELECT kind FROM identity_channels WHERE enabled")
    connection.portal.call(app.state.db.execute, "UPDATE identity_providers SET enabled=false WHERE enabled")
    connection.portal.call(app.state.db.execute, "UPDATE identity_channels SET enabled=false WHERE enabled")
    try:
        assert connection.get("/api/v1/auth/identity/providers").json() == {"items": []}
        assert connection.post("/api/v1/auth/identity/recovery/request", headers=ORIGIN,
                               json={"username": "unknown", "kind": "sms"}).status_code == 503
        assert connection.post("/api/v1/auth/identity/recovery/reset", headers=ORIGIN,
                               json={"token": "x" * 32, "password": "new-long-password"}).status_code == 401
    finally:
        connection.portal.call(app.state.db.execute,
            "UPDATE identity_providers SET enabled=true WHERE id=ANY(%s::uuid[])", ([r["id"] for r in providers],))
        connection.portal.call(app.state.db.execute,
            "UPDATE identity_channels SET enabled=true WHERE kind=ANY(%s::text[])", ([r["kind"] for r in channels],))
