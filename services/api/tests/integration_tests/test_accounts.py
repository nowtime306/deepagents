"""Real registration, approval, role enforcement and session revocation contracts."""

from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client

from app.manage import add_user
from app.security import hash_password, token_digest, verify_password

PASSWORD = "registration-test-password"


@pytest.fixture
def accounts(client):
    connection, app = client
    actor = connection.get("/api/v1/auth/me").json()
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (actor["id"],))
    previous = connection.portal.call(app.state.db.fetch, "SELECT registration_mode FROM account_settings WHERE id=1")[
        0
    ]
    connection.portal.call(app.state.db.execute, "UPDATE account_settings SET registration_mode='approval' WHERE id=1")
    connection.portal.call(
        app.state.db.execute,
        "DELETE FROM auth_rate_windows WHERE key=%s",
        (token_digest("registration:" + actor["username"]),),
    )
    cookie = connection.cookies.get("owner_agent_session")
    yield connection, app, actor, cookie
    connection.portal.call(
        app.state.db.execute,
        "UPDATE account_settings SET registration_mode=%s WHERE id=1",
        (previous["registration_mode"],),
    )


def login(connection, username, password=PASSWORD):
    connection.cookies.clear()
    return connection.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=ORIGIN)


def restore_admin(connection, cookie):
    connection.cookies.clear()
    connection.cookies.set("owner_agent_session", cookie)


def test_registration_approval_and_privilege_injection(accounts):
    connection, app, _, cookie = accounts
    username = "registered-" + uuid4().hex[:12]
    response = connection.post(
        "/api/v1/auth/register", json={"username": username, "password": PASSWORD}, headers=ORIGIN
    )
    assert response.status_code == 201 and response.json()["approval_status"] == "pending"
    assert PASSWORD not in response.text
    assert (
        connection.post(
            "/api/v1/auth/register", json={"username": username.upper(), "password": PASSWORD}, headers=ORIGIN
        ).status_code
        == 409
    )
    assert (
        connection.post(
            "/api/v1/auth/register",
            json={"username": "forge-" + uuid4().hex[:8], "password": PASSWORD, "is_admin": True},
            headers=ORIGIN,
        ).status_code
        == 422
    )
    assert login(connection, username).status_code == 403
    restore_admin(connection, cookie)
    row = connection.get("/api/v1/admin/accounts", params={"q": username}).json()["items"][0]
    assert "password_hash" not in row
    assert (
        connection.put(
            f"/api/v1/admin/accounts/{row['id']}",
            json={"role_id": "member", "enabled": True, "approval_status": "approved"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert login(connection, username.upper()).status_code == 200
    assert connection.get("/api/v1/auth/me").json()["role_id"] == "member"
    assert connection.get("/api/v1/admin/accounts").status_code == 403
    assert connection.post("/api/v1/conversations", json={"title": "允许的私有会话"}, headers=ORIGIN).status_code == 201


def test_invitation_one_use_expiry_and_closed_registration(accounts):
    connection, app, _, _ = accounts
    assert (
        connection.put(
            "/api/v1/admin/account-settings", json={"registration_mode": "closed"}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert (
        connection.post(
            "/api/v1/auth/register",
            json={"username": "closed-" + uuid4().hex[:8], "password": PASSWORD},
            headers=ORIGIN,
        ).status_code
        == 403
    )
    invite = connection.post(
        "/api/v1/admin/invitations", json={"name": "只读邀请", "role_id": "viewer"}, headers=ORIGIN
    ).json()
    assert invite["invitation"] not in connection.get("/api/v1/admin/invitations").text
    row = connection.portal.call(
        app.state.db.fetch, "SELECT token_hash FROM account_invitations WHERE id=%s", (invite["id"],)
    )[0]
    assert row["token_hash"] != invite["invitation"]
    username = "invited-" + uuid4().hex[:8]
    assert (
        connection.post(
            "/api/v1/auth/register",
            json={"username": username, "password": PASSWORD, "invitation": invite["invitation"]},
            headers=ORIGIN,
        ).json()["approval_status"]
        == "approved"
    )
    assert (
        connection.post(
            "/api/v1/auth/register",
            json={"username": "reuse-" + uuid4().hex[:8], "password": PASSWORD, "invitation": invite["invitation"]},
            headers=ORIGIN,
        ).status_code
        == 422
    )
    expired = connection.post("/api/v1/admin/invitations", json={"name": "过期邀请"}, headers=ORIGIN).json()
    connection.portal.call(
        app.state.db.execute,
        "UPDATE account_invitations SET expires_at=now()-interval '1 second' WHERE id=%s",
        (expired["id"],),
    )
    assert (
        connection.post(
            "/api/v1/auth/register",
            json={"username": "expire-" + uuid4().hex[:8], "password": PASSWORD, "invitation": expired["invitation"]},
            headers=ORIGIN,
        ).status_code
        == 422
    )
    assert login(connection, username).status_code == 200
    assert connection.get("/api/v1/conversations").status_code == 200
    assert connection.post("/api/v1/conversations", json={"title": "禁止写入"}, headers=ORIGIN).status_code == 403
    assert (
        connection.post("/api/v1/work-tasks", json={"title": "禁止任务", "goal": "test"}, headers=ORIGIN).status_code
        == 403
    )


def test_custom_role_enforcement_disable_and_session_revocation(accounts):
    connection, app, _, cookie = accounts
    role = connection.post(
        "/api/v1/admin/roles",
        json={"name": "仅对话", "permissions": ["chat.read", "chat.manage", "chat.run"]},
        headers=ORIGIN,
    )
    assert role.status_code == 201
    identifier = role.json()["id"]
    assert (
        connection.post(
            "/api/v1/admin/roles", json={"name": "伪管理员", "permissions": ["*"]}, headers=ORIGIN
        ).status_code
        == 422
    )
    created = connection.post(
        "/api/v1/admin/accounts", json={"username": "custom-" + uuid4().hex[:8]}, headers=ORIGIN
    ).json()
    assert (
        connection.put(
            f"/api/v1/admin/accounts/{created['id']}",
            json={"role_id": identifier, "enabled": True, "approval_status": "approved"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert login(connection, created["username"], created["temporary_password"]).json()["must_change_password"]
    assert connection.get("/api/v1/conversations").status_code == 403
    assert (
        connection.post(
            "/api/v1/auth/password",
            json={"current_password": created["temporary_password"], "new_password": PASSWORD},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert connection.get("/api/v1/auth/me").status_code == 401
    assert login(connection, created["username"]).status_code == 200
    session = connection.cookies.get("owner_agent_session")
    assert connection.get("/api/v1/conversations").status_code == 200
    assert connection.get("/api/v1/agents").status_code == 403
    restore_admin(connection, cookie)
    assert connection.delete(f"/api/v1/admin/roles/{identifier}", headers=ORIGIN).status_code == 409
    assert (
        connection.put(
            f"/api/v1/admin/roles/{identifier}",
            json={"name": "已收回执行", "permissions": ["chat.read"]},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    connection.cookies.clear()
    connection.cookies.set("owner_agent_session", session)
    assert connection.get("/api/v1/auth/me").status_code == 401
    assert login(connection, created["username"]).status_code == 200
    assert connection.post("/api/v1/conversations", json={"title": "禁止"}, headers=ORIGIN).status_code == 403
    restore_admin(connection, cookie)
    assert (
        connection.put(
            f"/api/v1/admin/accounts/{created['id']}",
            json={"role_id": identifier, "enabled": False, "approval_status": "approved"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert login(connection, created["username"]).status_code == 403


def test_password_reset_and_private_session_controls(accounts):
    connection, app, _, cookie = accounts
    created = connection.post(
        "/api/v1/admin/accounts", json={"username": "reset-" + uuid4().hex[:8]}, headers=ORIGIN
    ).json()
    assert login(connection, created["username"], created["temporary_password"]).status_code == 200
    first = connection.cookies.get("owner_agent_session")
    assert (
        connection.post(
            "/api/v1/auth/password", json={"current_password": "wrong", "new_password": PASSWORD}, headers=ORIGIN
        ).status_code
        == 403
    )
    assert (
        connection.post(
            "/api/v1/auth/password",
            json={"current_password": created["temporary_password"], "new_password": PASSWORD},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    connection.cookies.set("owner_agent_session", first)
    assert connection.get("/api/v1/auth/me").status_code == 401
    assert login(connection, created["username"]).status_code == 200
    own = connection.get("/api/v1/auth/sessions").json()["items"]
    assert len(own) == 1 and own[0]["current"] and "token_hash" not in own[0]
    assert connection.delete(f"/api/v1/auth/sessions/{uuid4()}", headers=ORIGIN).status_code == 404
    restore_admin(connection, cookie)
    reset = connection.post(f"/api/v1/admin/accounts/{created['id']}/reset-password", headers=ORIGIN).json()
    assert login(connection, created["username"]).status_code == 401
    assert login(connection, created["username"], reset["temporary_password"]).json()["must_change_password"]
    assert (
        connection.post(
            "/api/v1/auth/password",
            json={"current_password": reset["temporary_password"], "new_password": PASSWORD + "-new"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert login(connection, created["username"], PASSWORD + "-new").status_code == 200


def test_last_active_admin_and_builtin_roles_cannot_be_removed(accounts):
    connection, app, actor, _ = accounts
    others = connection.portal.call(
        app.state.db.fetch,
        "UPDATE users SET enabled=false WHERE is_admin AND enabled AND id<>%s RETURNING id",
        (actor["id"],),
    )
    try:
        assert (
            connection.put(
                f"/api/v1/admin/accounts/{actor['id']}",
                json={"role_id": "viewer", "enabled": True, "approval_status": "approved"},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        assert (
            connection.put(
                f"/api/v1/admin/accounts/{actor['id']}",
                json={"role_id": "admin", "enabled": False, "approval_status": "approved"},
                headers=ORIGIN,
            ).status_code
            == 409
        )
        assert connection.delete("/api/v1/admin/roles/member", headers=ORIGIN).status_code == 409
        assert (
            connection.put(
                "/api/v1/admin/roles/member", json={"name": "替换", "permissions": []}, headers=ORIGIN
            ).status_code
            == 409
        )
    finally:
        connection.portal.call(
            app.state.db.execute,
            "UPDATE users SET enabled=true WHERE id=ANY(%s::uuid[])",
            ([row["id"] for row in others],),
        )


def test_registration_rate_limit(accounts):
    connection, app, actor, _ = accounts
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO auth_rate_windows VALUES(%s,20,now()+interval '1 hour') "
        "ON CONFLICT(key) DO UPDATE SET hits=20,expires_at=now()+interval '1 hour'",
        (token_digest("registration:" + actor["username"]),),
    )
    assert (
        connection.post(
            "/api/v1/auth/register",
            json={"username": "limited-" + uuid4().hex[:8], "password": PASSWORD},
            headers=ORIGIN,
        ).status_code
        == 429
    )


def test_password_reset_during_login_cannot_mint_a_session(accounts, monkeypatch):
    connection, app, actor, _ = accounts
    encoded = hash_password("replaced-during-login")

    def raced_verification(password, original):
        valid = verify_password(password, original)
        connection.portal.call(
            app.state.db.execute, "UPDATE users SET password_hash=%s WHERE id=%s", (encoded, actor["id"])
        )
        return valid

    monkeypatch.setattr("app.main.verify_password", raced_verification)
    connection.cookies.clear()
    response = connection.post(
        "/api/v1/auth/login",
        json={"username": actor["username"], "password": "integration-password"},
        headers=ORIGIN,
    )
    assert response.status_code == 401
    assert not connection.cookies.get("owner_agent_session")


def test_provisioned_administrator_has_consistent_platform_role(accounts):
    connection, app, _, _ = accounts
    username = "provisioned-" + uuid4().hex[:12]

    async def provision():
        await add_user(app.state.settings.database_url, username, PASSWORD, is_admin=True)

    connection.portal.call(provision)
    row = connection.get("/api/v1/admin/accounts", params={"q": username}).json()["items"][0]
    assert row["role_id"] == "admin" and row["is_admin"] and row["role_name"] == "平台管理员"
    assert login(connection, username).status_code == 200
    assert connection.get("/api/v1/auth/me").json()["role_id"] == "admin"
