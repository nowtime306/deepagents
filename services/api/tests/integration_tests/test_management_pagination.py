"""Older management resources and histories remain reachable with scoped stable cursors."""

import base64
import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client

from app.workflow_definition import default_definition


@pytest.mark.parametrize("resource", ["work-tasks", "workflows"])
def test_management_pages_search_scope_and_old_record_selection(client, resource):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    table = "work_tasks" if resource == "work-tasks" else "workflows"
    if table == "work_tasks":
        connection.portal.call(app.state.db.execute,
            "INSERT INTO work_tasks(id,owner_id,title,goal,created_at,updated_at) "
            "SELECT gen_random_uuid(),%s,'分页-'||n,'100%%_字面',%s,%s FROM generate_series(1,105) n",
            (owner, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z"))
    else:
        connection.portal.call(app.state.db.execute,
            "INSERT INTO workflows(id,owner_id,name,instructions,model_id,definition,created_at) "
            "SELECT gen_random_uuid(),%s,'分页-'||n,'100%%_字面','test',%s,%s FROM generate_series(1,105) n",
            (owner, Jsonb(default_definition().model_dump()), "2020-01-01T00:00:00Z"))
    prefix = "/api/v1/" + resource
    first = connection.get(prefix).json()
    assert len(first["items"]) == 100 and first["next_cursor"]
    if resource == "work-tasks":
        connection.portal.call(app.state.db.execute,
            "UPDATE work_tasks SET updated_at=now() WHERE id=%s", (first["items"][-1]["id"],))
    second = connection.get(prefix, params={"before": first["next_cursor"]}).json()
    assert len(second["items"]) == 5 and second["next_cursor"] is None
    identifiers = [row["id"] for row in first["items"] + second["items"]]
    assert len(set(identifiers)) == 105
    assert identifiers == sorted(identifiers, reverse=True)
    assert connection.get(prefix, params={"q": "100%_字面"}).json()["items"]
    assert connection.get(prefix, params={"q": "100X_字面"}).json()["items"] == []
    assert connection.get(prefix, params={"before": "bad-cursor"}).status_code == 422
    malformed = json.loads(base64.urlsafe_b64decode(first["next_cursor"]))
    malformed["id"] = 123
    encoded = base64.urlsafe_b64encode(json.dumps(malformed).encode()).decode()
    assert connection.get(prefix, params={"before": encoded}).status_code == 422
    other = "/api/v1/workflows" if resource == "work-tasks" else "/api/v1/work-tasks"
    assert connection.get(other, params={"before": first["next_cursor"]}).status_code == 422
    if resource == "workflows":
        old = second["items"][-1]
        body = {"name": old["name"], "model_id": "test", "instructions": "修改旧稿", "revision": 1}
        assert connection.put(prefix + "/" + old["id"], json=body, headers=ORIGIN).status_code == 200
        selected = connection.get(prefix, params={"identifier": old["id"]}).json()["items"]
        assert len(selected) == 1 and selected[0]["revision"] == 2
    username = "pagination-other-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", headers=ORIGIN,
                    json={"username": username, "password": "integration-password"})
    assert connection.get(prefix).json()["items"] == []
    assert connection.get(prefix, params={"before": first["next_cursor"]}).status_code == 422


def test_workflow_history_pages_and_exact_old_run(client):
    connection, app = client
    created = connection.post("/api/v1/workflows", headers=ORIGIN,
        json={"name": "历史分页-" + uuid4().hex, "model_id": "test"})
    assert created.status_code == 201
    identifier = created.json()["id"]
    connection.portal.call(app.state.db.execute,
        "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot,created_at) "
        "SELECT gen_random_uuid(),%s,'SUCCEEDED','输入','{}',%s FROM generate_series(1,56)",
        (identifier, "2020-01-01T00:00:00Z"))
    prefix = f"/api/v1/workflows/{identifier}/runs"
    first = connection.get(prefix).json()
    second = connection.get(prefix, params={"before": first["next_cursor"]}).json()
    assert len(first["items"]) == 50 and len(second["items"]) == 6
    old = second["items"][-1]["id"]
    exact = connection.get(prefix, params={"run_id": old}).json()
    assert [row["id"] for row in exact["items"]] == [old] and exact["next_cursor"] is None
    assert connection.get(prefix, params={"before": "malformed"}).status_code == 422


def test_task_history_continues_beyond_fifty(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    task = connection.post("/api/v1/work-tasks", headers=ORIGIN,
                           json={"title": "历史分页", "goal": "测试历史"}).json()["id"]
    connection.portal.call(app.state.db.execute,
        "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,status,created_at) "
        "SELECT gen_random_uuid(),%s,%s,'历史','测试',1,'COMPLETED',%s FROM generate_series(1,56)",
        (task, owner, "2020-01-01T00:00:00Z"))
    prefix = f"/api/v1/work-tasks/{task}/runs"
    first = connection.get(prefix).json()
    second = connection.get(prefix, params={"before": first["next_cursor"]}).json()
    assert len(first["items"]) == 50 and len(second["items"]) == 6
    assert len({row["id"] for row in first["items"] + second["items"]}) == 56
    assert second["next_cursor"] is None
