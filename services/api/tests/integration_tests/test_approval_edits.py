"""Edits cannot change tools, escape the workspace or alter an existing decision."""

import hashlib
import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.operation_journal import OperationJournal
from app.work_queue import WorkQueue


def test_approval_edits_are_bounded_and_idempotent(client):
    connection, app = client
    task = connection.post("/api/v1/work-tasks", json={"title": "改参", "goal": "验证"}, headers=ORIGIN).json()["id"]
    owner = connection.portal.call(app.state.db.fetch, "SELECT owner_id FROM work_tasks WHERE id=%s", (task,))[0][
        "owner_id"
    ]
    run, approval = uuid4(), uuid4()
    payload = {"action_requests": [{"name": "execute", "args": {"command": "echo old"}}]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,status) "
        "VALUES(%s,%s,%s,'改参','验证',1,'WAITING_APPROVAL')",
        (run, task, owner),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,expires_at) "
        "VALUES(%s,%s,%s,'interrupt',%s,%s,now()+interval '1 minute')",
        (approval, run, owner, Jsonb(payload), digest),
    )
    url = f"/api/v1/work-approvals/{approval}/decisions"
    body = {
        "digest": digest,
        "decision": "edit",
        "edited_actions": [{"name": "delete", "args": {"file_path": "/file"}}],
    }
    assert connection.post(url, json=body, headers=ORIGIN).status_code == 422
    body["edited_actions"] = [{"name": "execute", "args": {"command": "echo new"}}]
    assert connection.post(url, json=body, headers=ORIGIN).status_code == 200
    assert connection.post(url, json=body, headers=ORIGIN).status_code == 200
    row = connection.portal.call(app.state.db.fetch, "SELECT resume_decision FROM work_queue_runs WHERE id=%s", (run,))[
        0
    ]
    assert row["resume_decision"]["interrupt"]["decisions"][0]["edited_action"]["args"] == {"command": "echo new"}
    body["edited_actions"][0]["args"]["command"] = "echo changed"
    assert connection.post(url, json=body, headers=ORIGIN).status_code == 409
    claimed = connection.portal.call(WorkQueue(app.state.db).claim)
    assert claimed["id"] == run
    journal = OperationJournal(app.state.db, claimed)
    with pytest.raises(PermissionError):
        connection.portal.call(journal.start, "execute", {"command": "echo new"})
    connection.portal.call(app.state.db.execute, "UPDATE work_approvals SET consumed_at=now() WHERE id=%s", (approval,))
    with pytest.raises(PermissionError):
        connection.portal.call(journal.start, "execute", {"command": "echo old"})
    operation = connection.portal.call(journal.start, "execute", {"command": "echo new", "timeout": 60})
    with pytest.raises(PermissionError):
        connection.portal.call(journal.start, "execute", {"command": "echo new"})
    connection.portal.call(journal.unknown, operation)
    with pytest.raises(PermissionError):
        connection.portal.call(journal.start, "execute", {"command": "echo new"})
    connection.portal.call(journal.finish, operation, 0, "已核实的迟到结果")
    assert connection.get(f"/api/v1/work-runs/{run}/operations").json()["items"][0]["status"] == "SUCCEEDED"
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run,))
