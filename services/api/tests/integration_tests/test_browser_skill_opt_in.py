"""Local authenticated browser consent is fresh per run and never an Agent preference."""

from uuid import UUID, uuid4

from test_api import ORIGIN
from test_api import client as client


def test_local_browser_consent_defaults_denial_and_frozen_snapshot(client):
    connection, app = client
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])

    def submit(**extra):
        task = connection.post(
            "/api/v1/work-tasks", json={"title": "授权验证", "goal": "无模型无浏览器执行"}, headers=ORIGIN
        ).json()["id"]
        return connection.post(
            f"/api/v1/work-tasks/{task}/runs", json={"model_id": "test", "revision": 1, **extra}, headers=ORIGIN
        )

    assert submit(environment="browser", allow_local_browser=True).status_code == 403
    ordinary = submit(environment="browser")
    assert ordinary.status_code == 202, ordinary.text
    run_id = ordinary.json()["run_id"]
    row = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (run_id,))[0]
    assert row["allow_local_browser"] is False
    assert row["agent_snapshot"]["allow_local_browser"] is False
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user,))
    assert submit(environment="coding", allow_local_browser=True).status_code == 422
    explicit = submit(environment="browser", allow_local_browser=True)
    assert explicit.status_code == 202, explicit.text
    checked = explicit.json()["run_id"]
    row = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (checked,))[0]
    assert row["allow_local_browser"] is True and row["agent_snapshot"]["allow_local_browser"] is True
    assert connection.get(f"/api/v1/work-runs/{checked}").json()["allow_local_browser"] is True
    following = submit(environment="browser")
    row = connection.portal.call(
        app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (following.json()["run_id"],)
    )[0]
    assert row["allow_local_browser"] is False
    # Later submissions cannot alter the authorization of an existing run.
    row = connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (checked,))[0]
    assert row["allow_local_browser"] is True and row["agent_snapshot"]["allow_local_browser"] is True
