"""Projects, literal search, pinning and owner-safe pagination against PostgreSQL."""

from uuid import uuid4

from psycopg.types.json import Jsonb
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_projects_pin_search_and_delete_preserve_history(client):
    connection, app = client
    project = connection.post("/api/v1/conversation-projects", json={"name": "  项目一  "}, headers=ORIGIN).json()
    assert project["name"] == "项目一"
    identifier = connection.post(
        "/api/v1/conversations", json={"title": "普通标题", "project_id": project["id"]}, headers=ORIGIN
    ).json()["id"]
    other = connection.post("/api/v1/conversations", json={"title": "新的对话"}, headers=ORIGIN).json()["id"]
    run = uuid4()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES (%s,%s,'test','SUCCEEDED')",
        (run, identifier),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) VALUES (%s,%s,%s,'user',%s)",
        (uuid4(), identifier, run, Jsonb({"text": "100%_literal 资料 Alpha"})),
    )
    assert connection.get("/api/v1/conversations", params={"q": "100%_literal"}).json()["items"][0]["id"] == identifier
    assert connection.get("/api/v1/conversations", params={"q": "%missing"}).json()["items"] == []
    assert (
        connection.get("/api/v1/conversations", params={"q": "alpha", "project_id": project["id"]}).json()["items"][0][
            "id"
        ]
        == identifier
    )
    assert (
        connection.patch(
            f"/api/v1/conversations/{identifier}/workspace", json={"pinned": True}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert connection.get("/api/v1/conversations").json()["items"][0]["id"] == identifier
    assert connection.get("/api/v1/conversations?unassigned=true").json()["items"][0]["id"] == other
    assert (
        connection.patch(
            f"/api/v1/conversation-projects/{project['id']}", json={"name": "已重命名"}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert connection.delete(f"/api/v1/conversation-projects/{project['id']}", headers=ORIGIN).status_code == 200
    assert connection.get(f"/api/v1/conversations/{identifier}").json()["project_id"] is None
    assert (
        connection.get(f"/api/v1/conversations/{identifier}/messages").json()["items"][0]["content"]["text"]
        == "100%_literal 资料 Alpha"
    )


def test_workspace_validation_and_owner_isolation(client):
    connection, app = client
    project = connection.post("/api/v1/conversation-projects", json={"name": "私有项目"}, headers=ORIGIN).json()["id"]
    conversation = connection.post(
        "/api/v1/conversations", json={"title": "私有 Alpha", "project_id": project}, headers=ORIGIN
    ).json()["id"]
    assert connection.post("/api/v1/conversation-projects", json={"name": "  "}, headers=ORIGIN).status_code == 422
    assert (
        connection.patch(f"/api/v1/conversations/{conversation}/workspace", json={}, headers=ORIGIN).status_code == 422
    )
    assert connection.get("/api/v1/conversations", params={"q": "x" * 201}).status_code == 422
    assert connection.patch(
        f"/api/v1/conversations/{conversation}", json={"project_id": project}, headers=ORIGIN,
    ).status_code == 422
    assert connection.patch(
        f"/api/v1/conversations/{conversation}", json={"title": " "}, headers=ORIGIN,
    ).status_code == 422
    assert (
        connection.get("/api/v1/conversations", params={"project_id": project, "unassigned": True}).status_code == 422
    )
    username = "workspace-other-" + str(uuid4())
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
    )
    assert connection.get("/api/v1/conversation-projects").json()["items"] == []
    assert connection.get("/api/v1/conversations?q=Alpha").json()["items"] == []
    assert connection.get("/api/v1/conversations", params={"project_id": project}).status_code == 404
    assert connection.post("/api/v1/conversations", json={"project_id": project}, headers=ORIGIN).status_code == 404
    assert (
        connection.patch(
            f"/api/v1/conversations/{conversation}/workspace", json={"pinned": True}, headers=ORIGIN
        ).status_code
        == 404
    )
    assert (
        connection.patch(f"/api/v1/conversation-projects/{project}", json={"name": "接管"}, headers=ORIGIN).status_code
        == 404
    )
    assert connection.delete(f"/api/v1/conversation-projects/{project}", headers=ORIGIN).status_code == 404


def test_pin_keyset_pagination_stays_filtered(client):
    connection, app = client
    project = connection.post("/api/v1/conversation-projects", json={"name": "分页项目"}, headers=ORIGIN).json()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO conversations(id,owner_id,title,project_id,pinned) "
        "SELECT gen_random_uuid(),%s,'needle-'||n,%s,n<12 FROM generate_series(1,65) n",
        (project["owner_id"], project["id"]),
    )
    params = {"project_id": project["id"], "q": "needle"}
    first = connection.get("/api/v1/conversations", params=params).json()
    second = connection.get("/api/v1/conversations", params={**params, "before": first["next_cursor"]}).json()
    assert len(first["items"]) == 50 and len(second["items"]) == 15
    assert len({row["id"] for row in first["items"] + second["items"]}) == 65
    assert sum(row["pinned"] for row in first["items"][:11]) == 11
