"""Independent negative acceptance cases; never launches CLI, browser or a model."""

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_browser_skill_api import PREFIX, seed

from app.browser_skill_work import browser_skill_tool, guard
from app.errors import APIError


async def nothing():
    pass


def test_publish_does_not_bind_run_or_allow_client_step_replacement(client):
    connection, app = client
    identifier = seed(connection, app)
    path = f"{PREFIX}/recordings/{identifier}"
    bindings = connection.get("/api/v1/capabilities/bindings").json()
    before = connection.portal.call(app.state.db.fetch, "SELECT count(*) AS n FROM work_queue_runs")[0]["n"]
    forged = {
        "revision": 1,
        "title": "参数表单",
        "description": "用途",
        "parameters": [{"name": "customer", "description": "客户标识"}],
        "steps": [],
    }
    assert connection.put(path + "/draft", json=forged, headers=ORIGIN).status_code == 422
    assert connection.get(path).json()["revision"] == 1
    published = connection.post(path + "/publish", json={"revision": 1}, headers=ORIGIN).json()
    assert connection.get("/api/v1/capabilities/bindings").json() == bindings
    assert connection.portal.call(app.state.db.fetch, "SELECT count(*) AS n FROM work_queue_runs")[0]["n"] == before
    assert connection.get(path).json()["skill_id"] == published["skill_id"]


def test_deleted_skill_cannot_be_replayed_from_old_recording(client):
    connection, app = client
    identifier = seed(connection, app)
    published = connection.post(
        f"{PREFIX}/recordings/{identifier}/publish", json={"revision": 1}, headers=ORIGIN
    ).json()
    skill = published["skill_id"]
    assert connection.delete("/api/v1/capabilities/skills/" + skill, headers=ORIGIN).status_code == 200
    assert connection.get("/api/v1/capabilities/skills/" + skill).status_code == 404
    assert not connection.portal.call(
        app.state.db.fetch, "SELECT * FROM browser_skill_versions WHERE skill_id=%s", (skill,)
    )
    runtime = SimpleNamespace(command=lambda *args: pytest.fail("No CLI may be called"))
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])
    tool = browser_skill_tool(
        app.state.db, runtime, SimpleNamespace(journal=object()), {"owner_id": user}, {"skill_ids": [skill]}
    )
    with pytest.raises(APIError) as error:
        connection.portal.call(
            tool.ainvoke,
            {
                "skill_id": skill,
                "version": 1,
                "parameters": {"input_2": "new"},
                "skill_name": "old",
                "step_count": 2,
                "execution_boundary": "local_authenticated_browser",
            },
        )
    assert error.value.code == "NOT_FOUND"


@pytest.mark.parametrize("change", ["expired", "cancelled", "revoked", "snapshot"])
def test_live_guard_rechecks_real_database_not_only_start_snapshot(client, change):
    connection, app = client
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user,))
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post(
        "/api/v1/work-tasks", json={"title": "独立权限反例", "goal": "无外部执行"}, headers=ORIGIN
    ).json()["id"]
    response = connection.post(
        f"/api/v1/work-tasks/{task}/runs",
        json={"revision": 1, "model_id": "test", "environment": "browser", "allow_local_browser": True},
        headers=ORIGIN,
    )
    assert response.status_code == 202, response.text
    identifier, lease = response.json()["run_id"], uuid4()
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_queue_runs SET status='RUNNING',lease_token=%s,lease_until=now()+interval '1 minute' WHERE id=%s",
        (lease, identifier),
    )
    run = {"id": UUID(identifier), "owner_id": user, "lease_token": lease}
    backend = SimpleNamespace(guard=nothing)
    connection.portal.call(guard, app.state.db, backend, run)
    updates = {
        "expired": "lease_until=now()-interval '1 second'",
        "cancelled": "cancel_requested=true",
        "snapshot": "agent_snapshot=agent_snapshot-'allow_local_browser'",
    }
    if change == "revoked":
        connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false WHERE id=%s", (user,))
    else:
        connection.portal.call(
            app.state.db.execute, "UPDATE work_queue_runs SET " + updates[change] + " WHERE id=%s", (identifier,)
        )
    with pytest.raises(APIError):
        connection.portal.call(guard, app.state.db, backend, run)


def test_automatic_work_clears_inherited_local_browser_opt_in(client):
    from app.automation_target import enqueue

    connection, app = client
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])
    source = {
        "mode": "work",
        "owner_id": user,
        "name": "独立自动派发",
        "environment": "browser",
        "network_domains": [],
        "budget": {"model_calls": 5, "token_limit": 1000},
        "snapshot": {"model_id": "test", "model_revision": 1, "allow_local_browser": True},
    }

    async def insert():
        async with app.state.db.pool.connection() as database:
            return await enqueue(database, source, "虚构自动任务")

    run, _ = connection.portal.call(insert)
    row = connection.portal.call(
        app.state.db.fetch, "SELECT allow_local_browser,agent_snapshot FROM work_queue_runs WHERE id=%s", (run,)
    )[0]
    assert row["allow_local_browser"] is False and row["agent_snapshot"]["allow_local_browser"] is False
    assert source["snapshot"]["allow_local_browser"] is True


def test_recording_listing_pagination_never_includes_other_owner(client):
    connection, app = client
    own = seed(connection, app)
    other = seed(connection, app, other=True)
    first = connection.get(PREFIX + "/recordings?limit=1&offset=0").json()["items"]
    second = connection.get(PREFIX + "/recordings?limit=1&offset=1").json()["items"]
    assert [row["id"] for row in first] == [own] and second == []
    assert other not in json.dumps(first)
