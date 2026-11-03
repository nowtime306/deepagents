"""HTTP authorization, authenticated audit actors and digest-bound notification confirmation."""
from uuid import uuid4

from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.manage import add_user
from app.notifications import notify
from app.security import COOKIE


def actor(connection, app):
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    return owner


def history(connection, kind, identifier):
    return connection.get(f"/api/v1/resource-configurations/{kind}/{identifier}/changes")


def test_http_actor_default_link_cas_redaction_deleted_history_and_cross_owner(client):
    connection, app = client
    owner = actor(connection, app)
    cookie = connection.cookies.get(COOKIE)
    forged = str(uuid4())
    body = {"name": uuid4().hex, "model": "fixture", "base_url": "https://fixture.invalid/private-hidden-path",
            "api_key": "HTTP_HISTORY_SECRET_NEVER_DISPLAY", "is_default": True}
    created = connection.post("/api/v1/admin/models", headers={**ORIGIN, "X-Actor": forged, "X-Change-ID": "forged"},
                              json=body)
    assert created.status_code == 201, created.text
    first = created.json()["id"]
    initial = history(connection, "model", first).json()["items"]
    assert initial and all(item["actor_id"] == owner and item["source"] == "user" for item in initial)
    assert initial[0]["change_id"] != "forged"
    assert "HTTP_HISTORY_SECRET_NEVER_DISPLAY" not in str(initial) and "private-hidden-path" not in str(initial)
    second = connection.post("/api/v1/admin/models", headers=ORIGIN, json={**body, "name": uuid4().hex})
    assert second.status_code == 201, second.text
    linked = history(connection, "model", first).json()["items"]
    other = history(connection, "model", second.json()["id"]).json()["items"]
    assert linked[0]["changes"]["is_default"] == {"before": True, "after": False}
    assert linked[0]["change_id"] == other[0]["change_id"] != initial[0]["change_id"]
    failed = connection.put(f"/api/v1/admin/models/{first}", headers=ORIGIN, json={**body, "revision": 1})
    assert failed.status_code == 409
    assert history(connection, "model", first).json()["items"] == linked
    detail = connection.get(f"/api/v1/configuration-changes/{linked[0]['id']}")
    assert detail.status_code == 200 and detail.json() == linked[0]
    repository = connection.post("/api/v1/repositories", headers=ORIGIN,
                                 json={"name": "私有仓库", "slug": "org/fixture", "ref": "main"})
    assert repository.status_code == 201, repository.text
    identifier = repository.json()["id"]
    assert connection.delete(f"/api/v1/repositories/{identifier}", headers=ORIGIN).status_code == 200
    retained = history(connection, "repository", identifier)
    assert retained.status_code == 200 and retained.json()["items"][0]["operation"] == "DELETE"
    private_record = retained.json()["items"][0]["id"]
    username = "rdc-other-" + uuid4().hex[:10]
    connection.portal.call(add_user, app.state.settings.database_url, username, "integration-password")
    other_role = "repository-reader-" + uuid4().hex[:8]
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
                           (other_role, other_role, Jsonb(["repositories.read"])))
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id=%s WHERE username=%s",
                           (other_role, username))
    connection.post("/api/v1/auth/login", headers=ORIGIN,
                    json={"username": username, "password": "integration-password"})
    assert history(connection, "model", first).status_code == 403
    assert history(connection, "repository", identifier).status_code == 404
    assert connection.get(f"/api/v1/configuration-changes/{private_record}").status_code == 404
    # Administrator status also does not grant another owner's private history.
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE username=%s", (username,))
    assert history(connection, "repository", identifier).status_code == 404
    connection.cookies.clear()
    connection.cookies.set(COOKIE, cookie)
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,'[]')",
                           ("restricted-" + owner, uuid4().hex))
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false,role_id=%s WHERE id=%s",
                           ("restricted-" + owner, owner))
    assert history(connection, "repository", identifier).status_code == 403
    assert connection.get(f"/api/v1/configuration-changes/{private_record}").status_code == 403


def test_http_notification_target_confirmation_cannot_change_under_user_and_reader_is_readonly(client):
    connection, app = client
    owner = actor(connection, app)
    channel = uuid4()
    target_a, target_b, target_c = "a" * 64, "b" * 64, "c" * 64
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO notification_channels(id,owner_id,name,kind,endpoint_ciphertext,host_hint,"
                           "target_digest) VALUES(%s,%s,'fixture','webhook','','old.invalid',%s)",
                           (channel, owner, target_a))

    async def enqueue():
        async with app.state.db.pool.connection() as current:
            await notify(current, owner, "消息冻结", "SUCCEEDED", "work", uuid4(), uuid4().hex,
                         [{"id": channel, "revision": 1}])
        await app.state.db.execute("UPDATE notification_deliveries SET status='PAUSED' WHERE channel_id=%s", (channel,))
    connection.portal.call(enqueue)
    row = connection.get("/api/v1/notifications/deliveries").json()["items"][0]
    endpoint = f"/api/v1/notifications/deliveries/{row['id']}/retry"
    connection.portal.call(app.state.db.execute,
                           "UPDATE notification_channels SET target_digest=%s,host_hint='reviewed.invalid' WHERE id=%s",
                           (target_b, channel))
    reviewed = connection.get("/api/v1/notifications/deliveries").json()["items"][0]
    assert reviewed["current_target_summary"]["identity"] == target_b
    connection.portal.call(app.state.db.execute,
                           "UPDATE notification_channels SET target_digest=%s,host_hint='changed-again.invalid' "
                           "WHERE id=%s",
                           (target_c, channel))
    request = {"acknowledge_duplicate": True, "confirm_target": True, "expected_target": target_b}
    rejected = connection.post(endpoint, headers=ORIGIN, json=request)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "DELIVERY_TARGET_CHANGED"
    assert len(connection.get("/api/v1/notifications/deliveries").json()["items"]) == 1
    assert connection.post(endpoint, headers=ORIGIN,
                           json={"acknowledge_duplicate": True, "confirm_target": True}).status_code == 409
    accepted = connection.post(endpoint, headers=ORIGIN, json={**request, "expected_target": target_c})
    assert accepted.status_code == 200, accepted.text
    rows = connection.get("/api/v1/notifications/deliveries").json()["items"]
    assert len(rows) == 2 and any(item["retry_of"] == row["id"] for item in rows)
    assert next(item for item in rows if item["id"] == row["id"])["status"] == "PAUSED"
    # No dispatcher is invoked, and the test channel contains no routable endpoint or credentials.
    role = "notification-reader-" + uuid4().hex[:8]
    connection.portal.call(app.state.db.execute,
                           "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
                           (role, role, Jsonb(["notifications.read"])))
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false,role_id=%s WHERE id=%s",
                           (role, owner))
    safe = connection.get("/api/v1/notification-channels")
    assert safe.status_code == 200 and safe.json()["items"]
    assert "endpoint_ciphertext" not in safe.text
    assert history(connection, "notification", str(channel)).status_code == 200
    assert connection.post(endpoint, headers=ORIGIN, json={**request, "expected_target": target_c}).status_code == 403
    assert connection.delete(f"/api/v1/notification-channels/{channel}", headers=ORIGIN).status_code == 403

    connection.portal.call(app.state.db.execute,
                           "UPDATE notification_deliveries SET status='CANCELLED' "
                           "WHERE channel_id=%s AND status='PENDING'",
                           (channel,))
