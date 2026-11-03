"""Actual checkpoints, receipts and worker transitions with a controlled remote command boundary."""

from types import SimpleNamespace

import pytest
from langgraph.types import Command
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from test_workflow_composition import create, definition
from test_workflow_tasks import enable, start

from app.operation_journal import OperationJournal
from app.permissions import require_account
from app.work_backend import RPC, WorkBackend
from app.work_engine import WorkEngine
from app.work_worker import WorkPaused, WorkWorker
from app.workflow_work import WorkWorkflow, binding


@pytest.mark.parametrize("case,expected,calls", [
    ("reject", "FAILED", 0), ("expired", "NEEDS_REVIEW", 0), ("revoked", "NEEDS_REVIEW", 0),
    ("nonzero", "FAILED", 1), ("unknown", "NEEDS_REVIEW", 1), ("unsafe_file", "FAILED", 0),
    ("pause", "COMPLETED", 1),
])
def test_unapproved_unknown_and_known_failures_do_not_replay_or_publish(client, monkeypatch, case, expected, calls):
    connection, app = client
    enable(connection, app)
    identifier = create(connection, {
        **definition("code", code="print('approved code')", timeout=2), "execution": "work"
    })
    reply = start(connection, identifier)
    assert reply.status_code == 202, reply.text
    root, issued = reply.json(), []
    if case == "pause":
        original = WorkWorkflow.observe

        async def pause_before_code(context, event):
            await original(context, event)
            if event["node_id"] == "start" and event["event"] == "node.completed":
                await app.state.db.execute("UPDATE work_queue_runs SET pause_requested=true WHERE id=%s",
                                           (context.run["id"],))

        monkeypatch.setattr(WorkWorkflow, "observe", pause_before_code)
    engine = WorkEngine(app.state.db, app.state.models, None, None, app.state.runtime.saver,
                        app.state.runtime.model_factory, capabilities_factory=lambda _: app.state.capabilities)

    async def execute(current):
        async def guard():
            await require_account(app.state.db, current["owner_id"], "work.execute")
            assert await app.state.db.fetch("SELECT 1 FROM work_queue_runs WHERE id=%s AND status='RUNNING' "
                "AND lease_token=%s AND lease_until>now()", (current["id"], current["lease_token"]))

        backend = WorkBackend(SimpleNamespace(id="local-command-protocol-standin"), guard,
                              journal=OperationJournal(app.state.db, current))

        async def boundary(script, body, seconds=15):
            await guard()
            if script == RPC:
                assert body["operation"] == "write"
                return {"error": "unsafe fixture path" if case == "unsafe_file" else None}
            issued.append(body)
            if case == "unknown":
                raise ConnectionError("Controlled lost response after sending the remote command")
            return {"exit_code": 7 if case == "nonzero" else 0, "output": "remote response", "truncated": False}

        backend._call = boundary
        await engine.consume_approvals(current)
        selected = await binding(app.state.db, current)
        context = WorkWorkflow(engine, current, selected, backend, guard)
        graph = await context.graph()
        result = await graph.ainvoke(
            Command(resume=current["resume_decision"]) if current["resume_decision"] else
            {"input": selected["input"], "values": {}, "output": "", "route": False},
            {"configurable": {"thread_id": "work:" + str(current["id"])}, "recursion_limit": 20},
        )
        if result.get("__interrupt__"):
            await engine.pause(current, result["__interrupt__"])
            raise WorkPaused()
        return {"text": result["output"]}

    worker = WorkWorker(app.state.db, execute)
    engine.events = worker
    assert connection.portal.call(worker.tick)
    if case == "pause":
        assert connection.get(f"/api/v1/work-runs/{root['run_id']}").json()["status"] == "PAUSED"
        assert connection.post(f"/api/v1/work-runs/{root['run_id']}/resume", headers=ORIGIN).status_code == 202
        assert connection.portal.call(worker.tick)
        assert connection.get(f"/api/v1/work-runs/{root['run_id']}").json()["status"] == "WAITING_APPROVAL"
    approvals = connection.get(f"/api/v1/work-runs/{root['run_id']}/approvals").json()["items"]
    assert len(approvals) == 1 and issued == []
    approval = approvals[0]
    full_input = connection.get(f"/api/v1/work-approvals/{approval['id']}/input")
    assert full_input.status_code == 200 and full_input.json()["text"] == "输入"
    assert full_input.headers["Cache-Control"] == "no-store"
    if case == "reject":
        from uuid import uuid4

        cookies = dict(connection.cookies)
        other = "approval-reader-" + uuid4().hex
        run_async(add_user(DATABASE, other, "integration-password"))
        connection.post("/api/v1/auth/login", headers=ORIGIN,
                        json={"username": other, "password": "integration-password"})
        assert connection.get(f"/api/v1/work-approvals/{approval['id']}/input").status_code == 404
        connection.cookies.clear()
        connection.cookies.update(cookies)
    elif case == "unsafe_file":
        connection.portal.call(app.state.db.execute,
            "UPDATE workflow_node_events SET data=jsonb_set(data,'{input}','\"tampered\"'::jsonb) "
            "WHERE run_id=%s AND event='node.started' AND node_id='action'", (root["workflow_run_id"],))
        assert connection.get(f"/api/v1/work-approvals/{approval['id']}/input").status_code == 503
    decision = connection.post(f"/api/v1/work-approvals/{approval['id']}/decisions", headers=ORIGIN,
        json={"digest": approval["digest"], "decision": "reject" if case == "reject" else "approve"})
    assert decision.status_code == 200, decision.text
    if case == "expired":
        connection.portal.call(app.state.db.execute,
            "UPDATE work_approvals SET expires_at=now()-interval '1 second' WHERE id=%s", (approval["id"],))
    elif case == "revoked":
        connection.portal.call(app.state.db.execute, "UPDATE users SET enabled=false WHERE id=("
            "SELECT owner_id FROM work_queue_runs WHERE id=%s)", (root["run_id"],))
    assert connection.portal.call(worker.tick)
    state = connection.portal.call(app.state.db.fetch,
        "SELECT status FROM work_queue_runs WHERE id=%s", (root["run_id"],))[0]
    assert state["status"] == expected and len(issued) == calls
    assert not connection.portal.call(worker.tick), "No automatic replay after a known failure or unknown response"
    operations = connection.portal.call(app.state.db.fetch,
        "SELECT status FROM operation_executions WHERE run_id=%s", (root["run_id"],))
    if case in {"unknown", "nonzero"}:
        assert operations == [{"status": "UNKNOWN" if case == "unknown" else "FAILED"}]
    workflow = connection.portal.call(app.state.db.fetch,
        "SELECT tested_revision FROM workflows WHERE id=%s", (identifier,))[0]
    assert workflow["tested_revision"] == (1 if case == "pause" else None)
