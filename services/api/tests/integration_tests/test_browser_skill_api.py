"""Isolated database API tests; seeded safe trajectories are protocol doubles, not real recordings."""

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

PREFIX = "/api/v1/browser-skills"


def seed(connection, app, *, blocked=False, other=False):
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])
    identifier = uuid4()
    if other:
        # Another real account in the isolated test database.
        from test_api import DATABASE, run_async

        from app.manage import add_user

        name = "bsk-other-" + uuid4().hex
        run_async(add_user(DATABASE, name, "integration-password"))
        user = connection.portal.call(app.state.db.fetch, "SELECT id FROM users WHERE username=%s", (name,))[0]["id"]
    draft = {
        "steps": [
            {"id": 1, "op": "navigate", "url": "https://fixture.test", "blocked": []},
            {
                "id": 2,
                "op": "fill",
                "target": {"role": "textbox", "name": "Name"},
                "parameter": "input_2",
                "blocked": ["敏感目标"] if blocked else [],
            },
        ],
        "parameters": [{"name": "input_2", "description": "输入", "required": True}],
    }
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO browser_skill_recordings(id,owner_id,connection_id,title,status,draft) "
        "VALUES(%s,%s,'local','演示','review',%s)",
        (identifier, user, Jsonb(draft)),
    )
    return str(identifier)


def test_owner_cas_publish_and_delete_preserves_private_skill(client):
    connection, app = client
    identifier = seed(connection, app)
    path = PREFIX + "/recordings/" + identifier
    edit = {
        "revision": 1,
        "title": "新的标题",
        "description": "演示",
        "parameters": [{"name": "name", "description": "姓名"}],
    }
    response = connection.put(path + "/draft", json=edit, headers=ORIGIN)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["revision"] == 2 and row["draft"]["steps"][1]["parameter"] == "name"
    assert connection.put(path + "/draft", json=edit, headers=ORIGIN).status_code == 409
    response = connection.post(path + "/publish", json={"revision": 2}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    published = response.json()
    assert connection.post(path + "/publish", json={"revision": 2}, headers=ORIGIN).json() == published
    skill = connection.get("/api/v1/capabilities/skills/" + published["skill_id"])
    assert skill.status_code == 200, skill.text
    assert "SKILL.md" in skill.json()["files"]
    assert "新的标题" in skill.json()["description"] and "演示" in skill.json()["description"]
    assert connection.request("DELETE", path, json={"revision": 2}, headers=ORIGIN).status_code == 200
    assert connection.get("/api/v1/capabilities/skills/" + published["skill_id"]).status_code == 200


def test_blocked_publish_owner_isolation_and_nonadmin_live_denial(client):
    connection, app = client
    identifier = seed(connection, app, blocked=True)
    assert (
        connection.post(PREFIX + f"/recordings/{identifier}/publish", json={"revision": 1}, headers=ORIGIN).status_code
        == 422
    )
    other = seed(connection, app, other=True)
    for suffix in ("", "/stop", "/cancel", "/publish"):
        response = (
            connection.get(PREFIX + f"/recordings/{other}")
            if not suffix
            else connection.post(PREFIX + f"/recordings/{other}" + suffix, json={"revision": 1}, headers=ORIGIN)
        )
        assert response.status_code == 404, response.text
    status = connection.get(PREFIX + "/status").json()
    assert status["code"] == "BROWSER_HOST_ADMIN_REQUIRED" and not status["connections"]
    response = connection.post(
        PREFIX + "/recordings", json={"title": "演示", "start_url": "https://fixture.test"}, headers=ORIGIN
    )
    assert response.status_code == 403, response.text


def test_publish_metadata_cannot_inject_frontmatter(client):
    from app.skill_library import parse_skill

    connection, app = client
    identifier = seed(connection, app)
    title = '引号"\n---\nname: injected'
    description = '用途：测试\n---\n危险字段: "value"'
    path = PREFIX + "/recordings/" + identifier
    edit = {
        "revision": 1,
        "title": title,
        "description": description,
        "parameters": [{"name": "safe_name", "description": '用户参数"说明'}],
    }
    assert connection.put(path + "/draft", json=edit, headers=ORIGIN).status_code == 200
    published = connection.post(path + "/publish", json={"revision": 2}, headers=ORIGIN).json()
    skill = connection.get("/api/v1/capabilities/skills/" + published["skill_id"]).json()
    name, summary, files = parse_skill("SKILL.md", skill["files"]["SKILL.md"].encode())
    assert name.startswith("browser-path-") and name != "injected"
    assert title in summary and description in summary and summary == skill["description"]
    assert "用户参数" in files["SKILL.md"]
