"""No external channel calls: a real loopback receiver verifies signed deliveries."""

import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_automations import schedule, version

from app.automation_scheduler import AutomationScheduler
from app.notification_channels import dispatch
from app.notifications import collect, notify


@pytest.fixture
def receiver():
    received, configuration = [], {"kind": "webhook", "disconnect": False}

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((dict(self.headers), body))
            if configuration["disconnect"]:
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            result = {"webhook": b"{}", "slack": b"ok", "feishu": b'{"code":0}', "wecom": b'{"errcode":0}'}[
                configuration["kind"]
            ]
            self.send_response(200)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/secret-hook", received, configuration
    server.shutdown()
    server.server_close()
    thread.join(3)


def channel(connection, url, kind="webhook", **changes):
    return connection.post(
        "/api/v1/notification-channels",
        json={
            "name": "本机协议验证",
            "kind": kind,
            "endpoint": url,
            "secret": "local-test-signature-key",
            "allow_private": True,
            **changes,
        },
        headers=ORIGIN,
    )


def admin(connection, app):
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    return owner


def make_notice(connection, app, owner, pin):
    async def create():
        async with app.state.db.pool.connection() as database:
            await notify(database, owner, "测试完成", "SUCCEEDED", "chat", uuid4(), "test:" + uuid4().hex, [pin])

    connection.portal.call(create)


def test_real_loopback_signed_delivery_encryption_and_channel_revision(client, receiver):
    connection, app = client
    url, received, _ = receiver
    assert channel(connection, url).status_code == 403
    owner = admin(connection, app)
    response = channel(connection, url)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    configuration = connection.get("/api/v1/notification-channels").json()
    assert "secret-hook" not in json.dumps(configuration) and "local-test-signature-key" not in response.text
    stored = connection.portal.call(
        app.state.db.fetch,
        "SELECT endpoint_ciphertext,secret_ciphertext FROM notification_channels WHERE id=%s",
        (identifier,),
    )[0]
    assert url not in stored["endpoint_ciphertext"] and "local-test" not in stored["secret_ciphertext"]
    make_notice(connection, app, owner, {"id": identifier, "revision": 1})
    connection.portal.call(dispatch, app.state)
    headers, body = received[0]
    signature = (
        "sha256="
        + hmac.new(
            b"local-test-signature-key", headers["X-Agent-Timestamp"].encode() + b"." + body, hashlib.sha256
        ).hexdigest()
    )
    assert hmac.compare_digest(headers["X-Agent-Signature"], signature)
    assert set(json.loads(body)) == {"id", "title", "status", "time", "target_kind", "target_id"}
    assert connection.get("/api/v1/notifications/deliveries").json()["items"][0]["status"] == "SUCCEEDED"
    make_notice(connection, app, owner, {"id": identifier, "revision": 1})
    edited = connection.put(
        f"/api/v1/notification-channels/{identifier}",
        json={"name": "新配置", "kind": "webhook", "revision": 1, "allow_private": True},
        headers=ORIGIN,
    )
    assert edited.status_code == 200, edited.text
    connection.portal.call(dispatch, app.state)
    rows = connection.get("/api/v1/notifications/deliveries").json()["items"]
    failed = next(row for row in rows if row["status"] == "FAILED")
    assert failed["error_code"] == "CHANNEL_CHANGED_REVIEW_REQUIRED" and len(received) == 1
    assert (
        connection.post(f"/api/v1/notifications/deliveries/{failed['id']}/retry", json={}, headers=ORIGIN).status_code
        == 422
    )
    assert (
        connection.post(
            f"/api/v1/notifications/deliveries/{failed['id']}/retry",
            json={"acknowledge_duplicate": True},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    connection.portal.call(dispatch, app.state)
    assert len(received) == 2


@pytest.mark.parametrize("kind", ["feishu", "wecom", "slack"])
def test_bot_wire_protocols_and_unknown_never_automatically_replay(client, receiver, kind):
    connection, app = client
    url, received, config = receiver
    config["kind"] = kind
    owner = admin(connection, app)
    response = channel(connection, url, kind)
    assert response.status_code == 201, response.text
    make_notice(connection, app, owner, {"id": response.json()["id"], "revision": 1})
    connection.portal.call(dispatch, app.state)
    body = json.loads(received[0][1])
    if kind == "feishu":
        import base64

        assert (
            body["sign"]
            == base64.b64encode(
                hmac.new((body["timestamp"] + "\nlocal-test-signature-key").encode(), b"", hashlib.sha256).digest()
            ).decode()
        )
        assert body["msg_type"] == "text"
    elif kind == "wecom":
        assert body["msgtype"] == "text" and body["text"]["mentioned_list"] == []
    else:
        assert body["blocks"][0]["text"]["type"] == "plain_text" and not body["mrkdwn"]
    assert connection.get("/api/v1/notifications/deliveries").json()["items"][0]["status"] == "SUCCEEDED"
    config["disconnect"] = True
    make_notice(connection, app, owner, {"id": response.json()["id"], "revision": 1})
    connection.portal.call(dispatch, app.state)
    assert connection.get("/api/v1/notifications/deliveries").json()["items"][0]["status"] == "UNKNOWN"
    connection.portal.call(dispatch, app.state)
    assert len(received) == 2


def test_in_app_notification_dedup_and_read_persistence(client):
    connection, app = client
    identifier = schedule(connection)
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN)
    connection.portal.call(AutomationScheduler(app.state).schedule, UUID(identifier))
    item = connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"][0]
    connection.portal.call(
        app.state.db.execute, "UPDATE task_runs SET status='SUCCEEDED' WHERE id=%s", (item["ask_run_id"],)
    )
    connection.portal.call(collect, app.state.db)
    result = connection.get("/api/v1/notifications").json()
    assert result["unread"] == 1 and result["items"][0]["message"] == "已完成"
    assert connection.post(f"/api/v1/notifications/{result['items'][0]['id']}/read", headers=ORIGIN).status_code == 200
    connection.portal.call(
        app.state.db.execute, "UPDATE automation_occurrences SET notified=false WHERE id=%s", (item["id"],)
    )
    connection.portal.call(collect, app.state.db)
    assert connection.get("/api/v1/notifications").json()["unread"] == 0
    assert len(connection.get("/api/v1/notifications").json()["items"]) == 1
    assert connection.post(f"/api/v1/notifications/{uuid4()}/read", headers=ORIGIN).status_code == 404


def test_signed_event_tamper_replay_changed_payload_and_pause(client):
    connection, app = client
    response = connection.post(
        "/api/v1/automations",
        json={
            "name": "外部事件",
            "kind": "webhook",
            "agent_version_id": version(connection),
            "goal": "审核资料",
            "starts_at": (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    row = response.json()
    identifier, key, path = row["id"], row["event_key"], row["event_path"]
    assert key not in connection.get("/api/v1/automations").text
    assert (
        connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": True}, headers=ORIGIN).status_code
        == 200
    )

    def event(value, *, bad=False, timestamp=None):
        raw = json.dumps({"event_id": "event-1", "data": value}, ensure_ascii=False).encode()
        stamp = timestamp or str(int(time.time()))
        sign = "sha256=" + hmac.new(key.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        return connection.post(
            path, content=raw, headers={"X-Agent-Timestamp": stamp, "X-Agent-Signature": "bad" if bad else sign}
        )

    assert event({"value": "资料"}, bad=True).status_code == 401
    assert event({}, timestamp="1").status_code == 401
    first = event({"value": "资料"})
    assert first.status_code == 202 and not first.json()["duplicate"], first.text
    duplicate = event({"value": "资料"})
    assert (
        duplicate.status_code == 202
        and duplicate.json()["run_id"] == first.json()["run_id"]
        and duplicate.json()["duplicate"]
    )
    assert event({"value": "修改"}).status_code == 409
    assert len(connection.get(f"/api/v1/automations/{identifier}/occurrences").json()["items"]) == 1
    connection.put(f"/api/v1/automations/{identifier}/state", json={"enabled": False}, headers=ORIGIN)
    assert event({"value": "资料"}).status_code == 401
