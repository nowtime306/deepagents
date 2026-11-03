"""Task input revisions are frozen; recovery copies cannot cross owners or active sandboxes."""

import hashlib
from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client

from app.work_artifacts import WorkArtifacts


def test_work_inputs_are_versioned_and_frozen_at_admission(client):
    connection, app = client
    task = connection.post("/api/v1/work-tasks", json={"title": "导入代码", "goal": "修改"}, headers=ORIGIN).json()[
        "id"
    ]
    base = f"/api/v1/work-tasks/{task}/files"

    def upload(revision, text, path="/src/main.py"):
        return connection.post(base, params={"path": path, "revision": revision}, content=text, headers=ORIGIN)

    assert upload(1, b"secret", "/../escape").status_code == 422
    first = upload(1, b"print(1)")
    assert first.status_code == 201 and first.json()["revision"] == 2
    assert upload(1, b"stale").status_code == 409
    second = upload(2, b"print(2)")
    assert second.status_code == 201 and second.json()["revision"] == 3
    assert upload(3, b"file", "/src").status_code == 409
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    run = connection.post(f"/api/v1/work-tasks/{task}/runs", json={"revision": 3, "model_id": "test"}, headers=ORIGIN)
    assert run.status_code == 202
    run_id = run.json()["run_id"]
    assert upload(3, b"print(3)").status_code == 201
    frozen = connection.get(f"/api/v1/work-runs/{run_id}").json()["input_files"]
    assert len(frozen) == 1 and frozen[0]["id"] == second.json()["id"]
    assert connection.get(f"/api/v1/work-input-files/{second.json()['id']}/download").content == b"print(2)"
    assert (
        connection.post(
            f"/api/v1/work-tasks/{task}/runs",
            json={"revision": 4, "model_id": "test", "restore_from_run": str(uuid4())},
            headers=ORIGIN,
        ).status_code
        == 409
    )
    connection.portal.call(
        app.state.db.execute, "UPDATE work_queue_runs SET status='NEEDS_REVIEW' WHERE id=%s", (run_id,)
    )
    snapshot = uuid4()
    connection.portal.call(
        app.state.db.execute, "INSERT INTO work_snapshots(id,run_id) VALUES(%s,%s)", (snapshot, run_id)
    )
    recovered = connection.post(
        f"/api/v1/work-tasks/{task}/runs",
        json={"revision": 4, "model_id": "test", "restore_from_run": run_id},
        headers=ORIGIN,
    )
    assert recovered.status_code == 202
    row = connection.portal.call(
        app.state.db.fetch, "SELECT restore_snapshot_id FROM work_queue_runs WHERE id=%s", (recovered.json()["run_id"],)
    )[0]
    assert row["restore_snapshot_id"] == snapshot
    file_id = uuid4()
    changed = b"print(2+1)"
    storage = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")
    storage.path(file_id).write_bytes(changed)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_artifacts(id,snapshot_id,run_id,owner_id,path,digest,size) "
        "SELECT %s,%s,id,owner_id,'/src/main.py',%s,%s FROM work_queue_runs WHERE id=%s",
        (file_id, snapshot, hashlib.sha256(changed).hexdigest(), len(changed), run_id),
    )
    difference = connection.get(f"/api/v1/work-artifacts/{file_id}/diff")
    assert (
        difference.status_code == 200
        and "-print(2)" in difference.json()["diff"]
        and "+print(2+1)" in difference.json()["diff"]
    )
    storage.path(file_id).unlink()
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE task_id=%s", (task,))
    assert connection.delete(f"/api/v1/work-tasks/{task}", headers=ORIGIN).status_code == 200
    assert connection.get(f"/api/v1/work-input-files/{first.json()['id']}/download").status_code == 404
