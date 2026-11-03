"""Durable task drafts use optimistic revisions and account ownership."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_draft_create_edit_conflict_and_owner(client):
    connection, _ = client
    body = {"title": f"任务-{uuid4()}", "goal": "整理资料并形成报告"}
    created = connection.post("/api/v1/work-tasks", json=body, headers=ORIGIN)
    assert created.status_code == 201
    identifier = created.json()["id"]
    item = next(row for row in connection.get("/api/v1/work-tasks").json()["items"] if row["id"] == identifier)
    assert item["status"] == "DRAFT" and item["goal"] == body["goal"] and item["revision"] == 1
    assert connection.get(f"/api/v1/work-tasks/{identifier}").json()["goal"] == body["goal"]
    assert (
        connection.put(
            f"/api/v1/work-tasks/{identifier}", json={**body, "goal": "新目标", "revision": 1}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert (
        connection.put(f"/api/v1/work-tasks/{identifier}", json={**body, "revision": 1}, headers=ORIGIN).status_code
        == 409
    )
    old_cookies = dict(connection.cookies)
    name = f"task-other-{uuid4()}"
    run_async(add_user(DATABASE, name, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": name, "password": "integration-password"}, headers=ORIGIN)
    assert identifier not in {row["id"] for row in connection.get("/api/v1/work-tasks").json()["items"]}
    assert connection.get(f"/api/v1/work-tasks/{identifier}").status_code == 404
    assert connection.delete(f"/api/v1/work-tasks/{identifier}", headers=ORIGIN).status_code == 404
    assert (
        connection.put(f"/api/v1/work-tasks/{identifier}", json={**body, "revision": 2}, headers=ORIGIN).status_code
        == 409
    )
    connection.cookies.clear()
    connection.cookies.update(old_cookies)
    assert connection.delete(f"/api/v1/work-tasks/{identifier}", headers=ORIGIN).status_code == 200
    connection.cookies.clear()
    assert connection.get("/api/v1/work-tasks").status_code == 401
