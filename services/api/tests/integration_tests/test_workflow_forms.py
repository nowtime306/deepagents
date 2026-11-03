"""Forms use the selected immutable version's schema, with existing ownership checks."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from test_workflow_composition import create, definition, run


def test_published_input_schema_survives_draft_edit_and_remains_private(client):
    connection, app = client
    schema = {
        "type": "object",
        "title": "申请资料",
        "properties": {"name": {"type": "string", "minLength": 2}},
        "required": ["name"],
    }
    graph = {**definition(operation="text"), "input_schema": schema}
    identifier = create(connection, graph)
    assert run(connection, identifier, '{"name":"测试"}').status_code == 200
    assert (
        connection.post(f"/api/v1/workflows/{identifier}/publish", headers=ORIGIN, json={"revision": 1}).status_code
        == 201
    )
    assert (
        connection.put(
            f"/api/v1/workflows/{identifier}",
            headers=ORIGIN,
            json={
                "name": "表单新草稿",
                "model_id": "test",
                "revision": 1,
                "definition": {**graph, "input_schema": {"type": "integer", "minimum": 3}},
            },
        ).status_code
        == 200
    )
    published = connection.get(f"/api/v1/workflows/{identifier}/versions").json()["items"][0]
    assert published["input_schema"] == schema
    before = connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"]
    assert (
        connection.post(
            f"/api/v1/workflows/{identifier}/versions/1/runs", headers=ORIGIN, json={"text": '{"name":"短"}'}
        ).status_code
        == 422
    )
    assert run(connection, identifier, '{"name":"测试"}').status_code == 422
    assert connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"] == before
    assert (
        connection.post(
            f"/api/v1/workflows/{identifier}/versions/1/runs", headers=ORIGIN, json={"text": '{"name":"测试"}'}
        ).status_code
        == 200
    )
    username = "forms-other-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"username": username, "password": "integration-password"},
    )
    assert connection.get(f"/api/v1/workflows/{identifier}/versions").status_code == 404
    assert not connection.portal.call(
        app.state.db.fetch, "SELECT 1 FROM model_usage WHERE run_id=ANY(%s)", ([item["id"] for item in before],)
    )
