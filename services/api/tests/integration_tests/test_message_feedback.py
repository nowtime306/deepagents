"""Feedback belongs to a saved normal assistant answer and follows live account permissions."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_api import DATABASE, ORIGIN
from test_api import client as client

from app.manage import add_user


def test_private_feedback_revision_permissions_and_message_delete_cascade(client):
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    conversation = connection.post("/api/v1/conversations", json={"title": "回答评价"}, headers=ORIGIN).json()["id"]
    started = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"message": "请给出可供评价的回答", "model_id": "test"},
        headers=ORIGIN,
    )
    assert started.status_code == 202
    connection.get(f"/api/v1/runs/{started.json()['run_id']}/events")
    assert connection.get(f"/api/v1/runs/{started.json()['run_id']}").json()["status"] == "SUCCEEDED"
    messages = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
    message = next(row["id"] for row in messages if row["role"] == "assistant")
    target = f"/api/v1/messages/{message}/feedback"
    assert connection.get(target).json()["rating"] is None
    body = {"rating": "helpful", "citations_correct": True, "refusal_correct": None, "comment": "已核对", "revision": 0}
    assert connection.put(target, json=body, headers=ORIGIN).json()["revision"] == 1
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda text: connection.put(target, json={**body, "revision": 1, "comment": text}, headers=ORIGIN),
                ["评价甲", "评价乙"],
            )
        )
    assert sorted(result.status_code for result in responses) == [200, 409]
    current = connection.get(target).json()
    assert current["revision"] == 2 and current["citations_correct"] is True and current["refusal_correct"] is None
    other = "feedback-other-" + uuid4().hex[:12]
    connection.portal.call(add_user, DATABASE, other, "integration-password")
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(target).status_code == 404
    assert connection.put(target, json=body, headers=ORIGIN).status_code == 404
    assert connection.delete(target + "?revision=2", headers=ORIGIN).status_code == 404
    connection.post(
        "/api/v1/auth/login", json={"username": account["username"], "password": "integration-password"}, headers=ORIGIN
    )
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id='viewer' WHERE id=%s", (account["id"],))
    assert connection.put(target, json={**body, "revision": 2}, headers=ORIGIN).status_code == 403
    assert connection.get(target).json()["revision"] == 2
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id='member' WHERE id=%s", (account["id"],))
    assert connection.delete(target + "?revision=1", headers=ORIGIN).status_code == 409
    assert connection.delete(target + "?revision=2", headers=ORIGIN).status_code == 200
    assert connection.put(target, json=body, headers=ORIGIN).status_code == 200
    connection.portal.call(
        app.state.db.execute, "UPDATE conversations SET internal_purpose='memory' WHERE id=%s", (conversation,)
    )
    assert connection.get(target).status_code == 404
    connection.portal.call(
        app.state.db.execute, "UPDATE conversations SET internal_purpose=NULL WHERE id=%s", (conversation,)
    )
    assert connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN).status_code == 200
    assert (
        connection.portal.call(
            app.state.db.fetch, "SELECT message_id FROM message_feedback WHERE message_id=%s", (message,)
        )
        == []
    )
