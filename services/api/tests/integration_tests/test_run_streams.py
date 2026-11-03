"""Resumable Ask events, safe snapshots and authorization during replay."""

import asyncio
import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request
from test_api import ORIGIN
from test_api import client as client

from app.run_streams import frame


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def ask(connection):
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN,
        json={"title": "Ask流-" + str(uuid4())}).json()["id"]
    accepted = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
        json={"message": "测试聊天流", "model_id": "test"})
    assert accepted.status_code == 202
    identifier = accepted.json()["run_id"]
    assert "stream.end" in connection.get(f"/api/v1/runs/{identifier}/events").text
    return conversation, identifier


@pytest.mark.parametrize("cursor", ["-1", "no", "9223372036854775808"])
def test_invalid_cursor_before_stream_headers(client, cursor):
    connection, _ = client
    _, identifier = ask(connection)
    response = connection.get(f"/api/v1/runs/{identifier}/events", headers={"Last-Event-ID": cursor})
    assert response.status_code == 422


def test_ask_replay_snapshot_and_auth(client):
    connection, _ = client
    conversation, identifier = ask(connection)
    url = f"/api/v1/runs/{identifier}/events"
    response = connection.get(url)
    data = frames(response)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in response.headers["cache-control"]
    persisted = [item for item in data if "event_id" in item]
    assert persisted and all(item["thread_id"] == conversation for item in persisted)
    state = next(item["data"] for item in data if item["event"] == "stream.snapshot")
    assert set(state) == {"run", "diagnostics", "usage", "team"}
    assert state["run"]["status"] == "SUCCEEDED" and "lease_token" not in state["run"]
    assert "lease_until" not in state["diagnostics"].get("execution", {})
    assert data[-1] == {"event": "stream.end", "data": {"status": "SUCCEEDED"}}
    replay = frames(connection.get(url, headers={"Last-Event-ID": str(persisted[0]["event_id"])}))
    assert [item["event_id"] for item in replay if "event_id" in item] == [item["event_id"] for item in persisted[1:]]
    connection.cookies.clear()
    assert connection.get(url).status_code == 401


def test_snapshot_decimal_remains_exact():
    value = Decimal("9007199254740991.123456789")
    encoded = frame("stream.snapshot", {"cost": value})
    assert json.loads(encoded.split("data: ")[1])["data"]["cost"] == str(value)


def test_ask_terminal_feed_drains_multiple_pages(client):
    connection, app = client
    _, identifier = ask(connection)
    connection.portal.call(app.state.db.execute, "DELETE FROM run_events WHERE run_id=%s", (identifier,))
    connection.portal.call(app.state.db.execute,
        "INSERT INTO run_events(run_id,event_id,event,data) "
        "SELECT %s,number,'message.delta',jsonb_build_object('text',number::text) FROM generate_series(1,205) number",
        (identifier,))
    connection.portal.call(app.state.db.execute, "UPDATE task_runs SET next_event_id=205 WHERE id=%s", (identifier,))
    data = frames(connection.get(f"/api/v1/runs/{identifier}/events"))
    assert [event["event_id"] for event in data if "event_id" in event] == list(range(1, 206))
    assert data[-1]["event"] == "stream.end"


def test_open_feed_rechecks_revocation_before_cached_events(client):
    from app.run_streams import events

    connection, app = client
    _, identifier = ask(connection)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    cookies = "; ".join(f"{key}={value}" for key, value in dict(connection.cookies).items())

    async def revoked():
        async def receive():
            await asyncio.sleep(10)
            return {"type": "http.disconnect"}

        request = Request({"type": "http", "app": app, "method": "GET", "scheme": "http",
                           "path": f"/api/v1/runs/{identifier}/events", "query_string": b"",
                           "headers": [(b"cookie", cookies.encode())]}, receive)
        feed = events(request, UUID(identifier), 0)
        try:
            assert (await anext(feed)).startswith("id:")
            await app.state.db.execute("UPDATE users SET enabled=false WHERE id=%s", (owner,))
            remaining = [item async for item in feed]
            assert any('"status_code": 403' in item for item in remaining)
            assert not any(item.startswith("id:") for item in remaining)
        finally:
            await app.state.db.execute("UPDATE users SET enabled=true WHERE id=%s", (owner,))
            await feed.aclose()

    connection.portal.call(revoked)
