"""Role enforcement, explicit version grants and revocation before execution."""

import time
from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from test_external_workflows import setup_version


def login(connection, name):
    assert (
        connection.post(
            "/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN
        ).status_code
        == 200
    )


def test_organization_roles_sharing_and_immediate_revocation(client):
    connection, app = client
    owner = connection.portal.call(
        app.state.db.fetch, "SELECT username,id FROM users ORDER BY created_at DESC LIMIT 1"
    )[0]
    _, version = setup_version(connection)
    org = connection.post("/api/v1/organizations", json={"name": "团队"}, headers=ORIGIN).json()["id"]
    username = "member-" + str(uuid4())
    run_async(add_user(DATABASE, username, "integration-password"))
    assert (
        connection.put(
            f"/api/v1/organizations/{org}/members", json={"username": username, "role": "viewer"}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert (
        connection.post(
            f"/api/v1/organizations/{org}/workflows", json={"version_id": version}, headers=ORIGIN
        ).status_code
        == 201
    )
    login(connection, username)
    assert connection.get("/api/v1/workflows").json()["items"] == []
    assert connection.get(f"/api/v1/organizations/{org}/workflows").json()["items"][0]["id"] == version
    url = f"/api/v1/organizations/{org}/workflows/{version}/runs"
    assert (
        connection.post(url, json={"text": "viewer"}, headers={**ORIGIN, "Idempotency-Key": "viewer"}).status_code
        == 403
    )
    assert (
        connection.put(
            f"/api/v1/organizations/{org}/members", json={"username": username, "role": "admin"}, headers=ORIGIN
        ).status_code
        == 403
    )
    login(connection, owner["username"])
    connection.put(
        f"/api/v1/organizations/{org}/members", json={"username": username, "role": "editor"}, headers=ORIGIN
    )
    login(connection, username)
    result = connection.post(url, json={"text": "authorized"}, headers={**ORIGIN, "Idempotency-Key": "editor"})
    assert result.status_code == 202
    for _ in range(60):
        history = connection.get(f"/api/v1/organizations/{org}/runs").json()["items"]
        if history and history[0]["status"] == "SUCCEEDED":
            break
        time.sleep(0.03)
    assert history[0]["status"] == "SUCCEEDED"
    login(connection, owner["username"])
    members = connection.get(f"/api/v1/organizations/{org}/members").json()["items"]
    member = next(row for row in members if row["username"] == username)
    assert connection.delete(f"/api/v1/organizations/{org}/members/{owner['id']}", headers=ORIGIN).status_code == 403
    assert (
        connection.delete(f"/api/v1/organizations/{org}/members/{member['user_id']}", headers=ORIGIN).status_code == 200
    )
    login(connection, username)
    assert connection.get(f"/api/v1/organizations/{org}/runs").status_code == 404
    assert (
        connection.post(url, json={"text": "revoked"}, headers={**ORIGIN, "Idempotency-Key": "revoked"}).status_code
        == 404
    )
