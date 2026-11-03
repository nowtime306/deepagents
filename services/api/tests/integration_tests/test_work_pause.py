"""Pause pending work immediately and active execution only at a safe checkpoint."""

from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client

from app.work_queue import WorkQueue


def test_pause_resume_and_single_live_run(client):
    connection, app = client
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post("/api/v1/work-tasks", json={"title": "暂停", "goal": "验证"}, headers=ORIGIN).json()["id"]
    payload = {"revision": 1, "model_id": "test"}
    run = connection.post(f"/api/v1/work-tasks/{task}/runs", json=payload, headers=ORIGIN).json()["run_id"]
    base = f"/api/v1/work-runs/{run}"
    assert connection.post(base + "/pause", headers=ORIGIN).json()["status"] == "PAUSED"
    assert connection.post(f"/api/v1/work-tasks/{task}/runs", json=payload, headers=ORIGIN).status_code == 409
    assert connection.post(base + "/resume", headers=ORIGIN).json()["status"] == "PENDING"
    claimed = connection.portal.call(WorkQueue(app.state.db).claim)
    assert str(claimed["id"]) == run
    assert connection.post(base + "/pause", headers=ORIGIN).json()["status"] == "RUNNING"
    detail = connection.get(base).json()
    assert detail["pause_requested"] and detail["status"] == "RUNNING"
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_queue_runs SET status='PAUSED',lease_token=NULL,lease_until=NULL WHERE id=%s",
        (run,),
    )
    assert connection.post(base + "/cancel", headers=ORIGIN).status_code == 202
    assert connection.get(base).json()["status"] == "CANCELLED"
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run,))
