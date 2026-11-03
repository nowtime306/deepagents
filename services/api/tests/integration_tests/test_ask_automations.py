"""Chat-created schedules stay paused, owned, fenced and idempotent."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client
from test_ask_worker import queued
from test_automations import version

from app.ask_queue import AskQueue


def claim(connection, app):
    connection.headers.update(ORIGIN)
    _, run = queued((connection, app))
    row = connection.portal.call(AskQueue(app.state.db).claim, run, uuid4())
    return run, str(row["lease_token"])


def test_chat_schedule_paused_idempotent_and_manual_enable(client):
    connection, app = client
    published = version(connection)
    run, token = claim(connection, app)
    prefix = f"/api/v1/internal/ask-runs/{run}"
    options = connection.post(prefix + "/automation-targets", json={"token": token})
    assert options.status_code == 200
    assert published in {row["id"] for row in options.json()["items"]}
    payload = {"token": token, "call_id": "chat-create-once", "schedule": {
        "name": "对话定时草稿", "agent_version_id": published, "goal": "执行只读检查",
        "starts_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }}
    first = connection.post(prefix + "/automation-drafts", json=payload)
    assert first.status_code == 200, first.text
    row = first.json()
    assert row["type"] == "automation_draft" and not row["enabled"]
    second = connection.post(prefix + "/automation-drafts", json=payload)
    assert second.json()["id"] == row["id"]
    listing = connection.get("/api/v1/automations").json()["items"]
    assert len([item for item in listing if item["id"] == row["id"]]) == 1
    assert connection.put(f"/api/v1/automations/{row['id']}/state", json={"enabled": True},
                          headers=ORIGIN).status_code == 200
    assert connection.post(prefix + "/automation-drafts", json=payload).json()["enabled"]
    assert connection.delete(f"/api/v1/automations/{row['id']}", headers=ORIGIN).status_code == 200
    assert connection.post(prefix + "/automation-drafts", json=payload).status_code == 409


def test_chat_schedule_stale_cancelled_automatic_and_foreign_target_rejected(client):
    connection, app = client
    run, token = claim(connection, app)
    path = f"/api/v1/internal/ask-runs/{run}"
    assert connection.post(path + "/automation-targets", json={"token": str(uuid4())}).status_code == 403
    payload = {"token": token, "call_id": "private-version", "schedule": {
        "name": "拒绝其他版本", "agent_version_id": str(uuid4()), "goal": "检查",
        "starts_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }}
    assert connection.post(path + "/automation-drafts", json=payload).status_code == 403
    payload["schedule"]["kind"] = "webhook"
    assert connection.post(path + "/automation-drafts", json=payload).status_code == 422
    async def cancel():
        await app.state.db.execute("UPDATE task_runs SET cancel_requested=true WHERE id=%s", (run,))
    connection.portal.call(cancel)
    assert connection.post(path + "/automation-targets", json={"token": token}).status_code == 403
    async def expire():
        await app.state.db.execute("UPDATE task_runs SET origin='interactive',lease_until=now()-interval '1 second' "
                                  "WHERE id=%s", (run,))
    connection.portal.call(expire)
    assert connection.post(path + "/automation-targets", json={"token": token}).status_code == 403
    async def automatic():
        await app.state.db.execute("UPDATE task_runs SET cancel_requested=false,origin='schedule',"
                                  "lease_until=now()+interval '60 seconds' WHERE id=%s", (run,))
    connection.portal.call(automatic)
    assert connection.post(path + "/automation-targets", json={"token": token}).status_code == 403


def test_chat_schedule_rechecks_revoked_role(client):
    connection, app = client
    run, token = claim(connection, app)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    async def revoke():
        await app.state.db.execute("UPDATE users SET is_admin=false,role_id='viewer' WHERE id=%s", (owner,))
    connection.portal.call(revoke)
    result = connection.post(f"/api/v1/internal/ask-runs/{run}/automation-targets", json={"token": token})
    assert result.status_code == 403 and result.json()["error"]["code"] == "PERMISSION_REQUIRED"
