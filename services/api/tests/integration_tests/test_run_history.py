"""Run history is visible only to the owning account."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_run_history_is_scoped_to_owner(client) -> None:
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={"title": "记录验证"}, headers=ORIGIN).json()["id"]
    identifier = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES (%s,%s,'test','SUCCEEDED')",
        (identifier, conversation),
    )
    history = connection.get("/api/v1/runs").json()["items"]
    assert any(item["id"] == identifier and item["conversation_title"] == "记录验证" for item in history)
    username = f"other-{uuid4()}"
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    assert connection.get("/api/v1/runs").status_code == 401
    connection.post(
        "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
    )
    assert identifier not in {item["id"] for item in connection.get("/api/v1/runs").json()["items"]}
