"""Fixed task workflows require owned persistent approvals and preserve completed nodes."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from test_workflow_composition import create, definition, run


def enable(connection, app):
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))


def start(connection, identifier, **kwargs):
    return connection.post(f"/api/v1/workflows/{identifier}/work-runs", headers=ORIGIN,
                           json={"text": "输入", "revision": 1, **kwargs})


def test_modes_validation_readonly_entry_and_work_admission(client):
    connection, app = client
    pure = create(connection, definition(operation="upper"))
    assert connection.get("/api/v1/workflows").json()["items"][0]["definition"]["execution"] == "readonly"
    approval = {**definition("approval", prompt="确认继续"), "execution": "work"}
    invalid = connection.post("/api/v1/workflows", headers=ORIGIN,
        json={"name": "拒绝隐式代码", "model_id": "test", "definition": definition("code", code="print(1)")})
    assert invalid.status_code == 422
    identifier = create(connection, approval)
    assert run(connection, identifier, "input").json()["error"]["code"] == "WORKFLOW_WORK_REQUIRED"
    assert start(connection, identifier).status_code == 503
    enable(connection, app)
    assert start(connection, pure).status_code == 422
    assert start(connection, identifier, revision=2).status_code == 409
    assert start(connection, identifier, version=1).status_code == 422
    result = start(connection, identifier)
    assert result.status_code == 202, result.text
    root = result.json()
    row = connection.get(f"/api/v1/work-runs/{root['run_id']}").json()
    assert row["status"] == "PENDING" and row["model_calls"] == 0
    assert connection.post(f"/api/v1/work-runs/{root['run_id']}/cancel", headers=ORIGIN).status_code == 202
    from app.workflow_tasks import reconcile
    connection.portal.call(reconcile, app.state.db)
    runs = connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"]
    assert runs[0]["status"] == "CANCELLED"
    # The generated task remains a fixed-flow task, even when reached from the task list.
    assert connection.get(f"/api/v1/work-tasks/{root['task_id']}").json()["workflow_task"] is True
    assert connection.post(f"/api/v1/work-tasks/{root['task_id']}/runs", headers=ORIGIN,
                           json={"model_id": "test", "revision": 1}).status_code == 422
    other = "fixed-flow-other-" + uuid4().hex
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.post("/api/v1/auth/login", headers=ORIGIN,
                    json={"username": other, "password": "integration-password"})
    assert start(connection, identifier).status_code == 404
    assert connection.get(f"/api/v1/work-runs/{root['run_id']}").status_code == 404


@pytest.mark.parametrize("code,seconds", [("print(", 30), ("print(1)", True), ("print(1)", 61), ("x" * 8001, 1)])
def test_code_limits_reject_before_creating_a_task(client, code, seconds):
    connection, _ = client
    result = connection.post("/api/v1/workflows", headers=ORIGIN,
        json={"name": "代码校验", "model_id": "test",
              "definition": {**definition("code", code=code, timeout=seconds), "execution": "work"}})
    assert result.status_code == 422, result.text


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires actual local Coding sandbox")
def test_actual_code_three_approvals_rebuilt_engine_no_model_or_command_replay(client):
    from langchain_core.messages import AIMessage
    from opensandbox.config import ConnectionConfig

    from app.durable_sandboxes import DurableSandboxes
    from app.work_artifacts import WorkArtifacts
    from app.work_engine import WorkEngine
    from tests.unit_tests.test_harness import ToolModel

    connection, app = client
    enable(connection, app)
    prompts = []

    class InspectModel(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = InspectModel(responses=[AIMessage(content=text,
        usage_metadata={"input_tokens": 20, "output_tokens": 4, "total_tokens": 24})
        for text in ("模型前置结果", "模型最终结果")])
    kinds = ["start", "llm", "approval", "code", "approval", "llm", "end"]
    code = ("import sys\nfrom pathlib import Path\np=Path('code-counter.txt')\n"
            "p.write_text((p.read_text() if p.exists() else '')+'1')\nprint(sys.stdin.read().strip()+'-code')")
    nodes = [{"id": "n" + str(index), "kind": kind, "input": "input" if not index else "n" + str(index - 1),
              "parameters": {"prompt": "确认节点"} if kind == "approval" else {"code": code, "timeout": 5}
              if kind == "code" else {}} for index, kind in enumerate(kinds)]
    graph = {"execution": "work", "nodes": nodes,
             "edges": [{"source": "n" + str(index), "target": "n" + str(index + 1)} for index in range(6)],
             "model_calls": 2, "token_limit": 10000, "node_budget": 7}
    identifier = create(connection, graph)
    response = start(connection, identifier)
    assert response.status_code == 202, response.text
    root = response.json()
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    service = DurableSandboxes(app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key,
                                                             use_server_proxy=True))
    artifacts = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")

    def engine():
        result = WorkEngine(app.state.db, app.state.models, service, artifacts, app.state.runtime.saver,
                            lambda _: model, capabilities_factory=lambda _: app.state.capabilities)
        original = result.events.execute

        async def diagnose(current):
            from app.work_worker import WorkPaused

            try:
                return await original(current)
            except WorkPaused:
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Isolated fixed workflow failure")
                raise

        result.events.execute = diagnose
        return result

    def tick():
        import time

        for _ in range(10):
            assert connection.portal.call(engine().events.tick)
            state = connection.get(f"/api/v1/work-runs/{root['run_id']}").json()
            if state["status"] != "PENDING" or state["error_code"] != "WORK_CAPACITY_WAIT":
                return state
            # Admission returned before creating a sandbox or starting any node. Wait for the real host;
            # do not override the memory probe or repeat an unknown operation.
            time.sleep(5)
            connection.portal.call(app.state.db.execute,
                "UPDATE work_queue_runs SET available_after=now() WHERE id=%s AND status='PENDING'", (root["run_id"],))
        pytest.fail("Actual host did not admit the isolated Coding fixture within 50 seconds")

    try:
        for operation in ("workflow_approval", "workflow_code", "workflow_approval"):
            state = tick()
            assert state["status"] == "WAITING_APPROVAL", state
            assert len(prompts) == 1, "Completed model node must survive each reconstructed graph"
            approvals = connection.get(f"/api/v1/work-runs/{root['run_id']}/approvals").json()["items"]
            pending = [item for item in approvals if not item["decision"]]
            assert len(pending) == 1
            approval = pending[0]
            assert approval["payload"]["action_requests"][0]["name"] == operation
            url = f"/api/v1/work-approvals/{approval['id']}/decisions"
            assert connection.post(url, headers=ORIGIN,
                json={"digest": "0" * 64, "decision": "approve"}).status_code == 409
            assert connection.post(url, headers=ORIGIN,
                json={"digest": approval["digest"], "decision": "edit",
                      "edited_actions": [{"name": "execute", "args": {"command": "echo bypass"}}]}).status_code == 422
            accepted = connection.post(url, headers=ORIGIN,
                json={"digest": approval["digest"], "decision": "approve"})
            assert accepted.status_code == 200, accepted.text
            # Actual API startup reconciliation must preserve a workflow waiting on its independent Work root.
            from app.workflow_queue import reconcile_startup
            connection.portal.call(reconcile_startup, app.state.db)
            assert connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"][0]["status"] == "RUNNING"
        state = tick()
        assert state["status"] == "COMPLETED", state
        assert state["result"]["text"] == "模型最终结果"
        assert state["result"]["verification"]["successful_code_nodes"] == 1
        assert len(prompts) == 2 and "模型前置结果-code" in str(prompts[-1])
        usage = connection.get(f"/api/v1/work-runs/{root['run_id']}/usage").json()["total"]
        assert usage["calls"] == usage["reported_calls"] == 2
        assert (usage["input_tokens"], usage["output_tokens"]) == (40, 8)
        operations = connection.get(f"/api/v1/work-runs/{root['run_id']}/operations").json()["items"]
        assert len(operations) == 3 and all(item["status"] == "SUCCEEDED" for item in operations)
        files = connection.get(f"/api/v1/work-runs/{root['run_id']}/artifacts").json()["items"]
        counter = next(item for item in files if item["path"] == "/code-counter.txt")
        assert connection.get(f"/api/v1/work-artifacts/{counter['id']}/download").content == b"1"
        workflow = connection.get("/api/v1/workflows").json()["items"]
        assert next(item for item in workflow if item["id"] == identifier)["tested_revision"] == 1
        assert connection.post(f"/api/v1/workflows/{identifier}/publish", headers=ORIGIN,
                               json={"revision": 1}).status_code == 201
        version = connection.get(f"/api/v1/workflows/{identifier}/versions").json()["items"][0]
        assert version["execution"] == "work"
        assert not any(item["id"] == version["id"] for item in connection.get(
            "/api/v1/workflows/options").json()["items"])
        assert connection.post("/api/v1/api-keys", headers=ORIGIN,
            json={"name": "禁止后台绕过审批", "version_id": version["id"]}).status_code == 422
        nested = connection.post("/api/v1/workflows", headers=ORIGIN,
            json={"name": "禁止中断子流程重放", "model_id": "test",
                  "definition": definition("subworkflow", version_id=version["id"])})
        assert nested.status_code == 422
        assert run(connection, identifier, "仍不允许短入口").status_code == 422
        assert not (Path.cwd() / "code-counter.txt").exists(), "Script must never run on the host"
    finally:
        sessions = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (root["run_id"],))
        for session in sessions:
            connection.portal.call(service.release, session["id"])
        files = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_artifacts WHERE run_id=%s", (root["run_id"],))
        for file in files:
            artifacts.path(file["id"]).unlink(missing_ok=True)
        connection.portal.call(app.state.db.execute,
                               "DELETE FROM work_sandbox_sessions WHERE run_id=%s", (root["run_id"],))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (root["run_id"],))
        connection.portal.call(app.state.db.execute,
                               "DELETE FROM workflow_runs WHERE id=%s", (root["workflow_run_id"],))
        connection.delete(f"/api/v1/work-tasks/{root['task_id']}", headers=ORIGIN)
        connection.portal.call(service.close)
