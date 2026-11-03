"""Every node can be debugged without running its canvas neighbours or unlocking publication."""

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_api import ORIGIN, TestModel
from test_api import client as client
from test_workflow_composition import definition, publish
from test_workflow_tasks import enable


@pytest.mark.parametrize("kind", ["start", "end", "llm", "agent", "rag", "mcp", "http", "condition",
                                 "transform", "subworkflow", "iteration", "parallel", "loop"])
def test_each_readonly_node_isolated_with_real_events(client, monkeypatch, kind):
    connection, app = client
    parameters, text = {}, "hello"
    if kind in {"subworkflow", "iteration", "parallel", "loop"}:
        _, version = publish(connection, definition(operation="upper"))
        parameters = {"version_id": version}
        if kind == "parallel":
            parameters = {"version_ids": [version, version]}
        if kind == "loop":
            parameters.update(operator="equals", value="HELLO", max_iterations=2)
        if kind == "iteration":
            text = '["hello", "world"]'
    if kind == "condition":
        parameters = {"operator": "contains", "value": "hello"}
    if kind == "transform":
        parameters = {"operation": "upper"}
    if kind == "http":
        parameters = {"url": "https://example.org/node"}
        monkeypatch.setattr("app.workflow_runtime.fetch_public_text", lambda _: "local HTTP result")
    if kind == "agent":
        agent = connection.post("/api/v1/agents", headers=ORIGIN,
            json={"name": "单节点助手-" + uuid4().hex, "model_id": "test", "instructions": "回答测试输入"})
        assert agent.status_code == 201
        parameters = {"agent_id": agent.json()["id"]}
    if kind == "rag":
        knowledge = connection.post("/api/v1/capabilities/knowledge", headers=ORIGIN,
            json={"name": "单节点资料-" + uuid4().hex}).json()["id"]
        parameters = {"knowledge_id": knowledge}
    if kind == "mcp":
        server = uuid4()
        tool = {"name": "lookup", "description": "只读测试", "read_only": True, "enabled": True,
                "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}
        connection.portal.call(app.state.db.execute,
            "INSERT INTO mcp_servers(id,name,url,tools) VALUES(%s,%s,%s,%s)",
            (server, "单节点工具-" + str(server), "https://example.org/mcp", Jsonb([tool])))
        assert connection.put("/api/v1/capabilities/bindings", headers=ORIGIN,
                              json={"mcp_ids": [str(server)]}).status_code == 200
        parameters = {"server_id": str(server), "tool": "lookup", "arguments": {"query": {"ref": "other"}}}

        async def invoke(server, name, arguments, *, expected_pin=None):
            assert expected_pin and arguments == {"query": "explicit test variable"}
            return "local MCP result"

        app.state.mcp.invoke = invoke
    node = {"id": "target", "kind": kind, "input": "unavailable", "parameters": parameters}
    nodes = [node] if kind == "start" else [{"id": "start", "kind": "start"}, node]
    if kind != "end":
        nodes.append({"id": "end", "kind": "end"})
    nodes.append({"id": "unconfigured", "kind": "rag"})
    created = connection.post("/api/v1/workflows", headers=ORIGIN,
        json={"name": "逐节点-" + kind + uuid4().hex, "model_id": "test", "allow_incomplete": True,
              "definition": {"nodes": nodes, "edges": []}})
    assert created.status_code == 201, created.text
    prefix = "/api/v1/workflows/" + created.json()["id"]
    if kind == "mcp":
        missing = connection.post(prefix + "/nodes/target/test-runs", headers=ORIGIN,
                                  json={"text": text, "revision": 1})
        assert missing.status_code == 422 and "other" in missing.json()["error"]["message"]
    result = connection.post(prefix + "/nodes/target/test-runs", headers=ORIGIN,
        json={"text": text, "revision": 1, "variables": {"other": "explicit test variable"}})
    assert result.status_code == 200, result.text
    events = connection.get(prefix + "/runs/" + result.json()["id"] + "/events").json()["items"]
    if kind == "mcp":
        evidence = connection.get(prefix + "/runs").json()["items"][0]
        assert evidence["snapshot"]["debug_variables"] == {"other": "explicit test variable"}
    assert any(event["node_id"] == "target" and event["event"] == "node.completed" for event in events)
    assert not any(event["node_id"] == "unconfigured" for event in events)
    current = next(item for item in connection.get("/api/v1/workflows").json()["items"]
                   if item["id"] == created.json()["id"])
    original = next(item for item in current["definition"]["nodes"] if item["id"] == "target")
    assert original["input"] == node["input"] and original["parameters"] == parameters
    assert current["tested_revision"] is None


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires actual local Coding sandbox")
@pytest.mark.parametrize("kind", ["code", "approval"])
def test_durable_node_debug_approval_resume_and_publish_boundary(client, kind):
    from opensandbox.config import ConnectionConfig

    from app.durable_sandboxes import DurableSandboxes
    from app.work_artifacts import WorkArtifacts
    from app.work_engine import WorkEngine

    connection, app = client
    enable(connection, app)
    parameters = {"code": "import sys\nprint(sys.stdin.read().strip().upper())", "timeout": 5} if kind == "code" else {
        "prompt": "确认仅测试当前节点"}
    graph = {"execution": "work", "nodes": [{"id": "start", "kind": "start"},
        {"id": "target", "kind": kind, "input": "unconfigured", "parameters": parameters},
        {"id": "unconfigured", "kind": "llm"}, {"id": "end", "kind": "end"}], "edges": []}
    created = connection.post("/api/v1/workflows", headers=ORIGIN,
        json={"name": "持久单节点-" + uuid4().hex, "model_id": "test", "definition": graph, "allow_incomplete": True})
    assert created.status_code == 201
    prefix = "/api/v1/workflows/" + created.json()["id"]
    identifier = str(uuid4())
    body = {"text": "hello", "revision": 1, "run_id": identifier}
    response = connection.post(prefix + "/nodes/target/test-runs", json=body, headers=ORIGIN)
    assert response.status_code == 200, response.text
    root = response.json()
    duplicate = connection.post(prefix + "/nodes/target/test-runs", json=body, headers=ORIGIN)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "WORKFLOW_RUN_EXISTS"
    assert len(connection.get(prefix + "/runs").json()["items"]) == 1
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    service = DurableSandboxes(app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key,
                                                             use_server_proxy=True))
    artifacts = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")

    class NoCalls(TestModel):
        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            raise AssertionError("Debug must not execute the unrelated model node")

    def tick():
        for _ in range(10):
            engine = WorkEngine(app.state.db, app.state.models, service, artifacts, app.state.runtime.saver,
                                lambda _: NoCalls(responses=["unused"]),
                                capabilities_factory=lambda _: app.state.capabilities)
            assert connection.portal.call(engine.events.tick)
            state = connection.get(f"/api/v1/work-runs/{root['run_id']}").json()
            if state["status"] != "PENDING" or state["error_code"] != "WORK_CAPACITY_WAIT":
                return state
            time.sleep(5)
            connection.portal.call(app.state.db.execute,
                "UPDATE work_queue_runs SET available_after=now() WHERE id=%s AND status='PENDING'", (root["run_id"],))
        pytest.fail("Actual host did not admit the single-node fixture within 50 seconds")

    try:
        waiting = tick()
        assert waiting["status"] == "WAITING_APPROVAL", waiting
        approvals = connection.get(f"/api/v1/work-runs/{root['run_id']}/approvals").json()["items"]
        assert len(approvals) == 1 and not approvals[0]["decision"]
        approval = approvals[0]
        assert approval["payload"]["action_requests"][0]["name"] == "workflow_" + kind
        accepted = connection.post(f"/api/v1/work-approvals/{approval['id']}/decisions", headers=ORIGIN,
            json={"digest": approval["digest"], "decision": "approve"})
        assert accepted.status_code == 200
        state = tick()  # A fresh engine reconstructs the checkpoint and resumes the selected node.
        assert state["status"] == "COMPLETED", state
        assert state["model_calls"] == 0
        run = connection.get(prefix + "/runs").json()["items"][0]
        assert run["snapshot"]["debug_node_id"] == "target" and run["status"] == "SUCCEEDED"
        assert run["output"].strip() == ("HELLO" if kind == "code" else "hello")
        events = connection.get(prefix + "/runs/" + identifier + "/events").json()["items"]
        assert not any(event["node_id"] == "unconfigured" for event in events)
        workflow = next(item for item in connection.get("/api/v1/workflows").json()["items"]
                        if item["id"] == created.json()["id"])
        assert workflow["tested_revision"] is None
    finally:
        sessions = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (root["run_id"],))
        for session in sessions:
            connection.portal.call(service.release, session["id"])
        for file in connection.portal.call(app.state.db.fetch,
                "SELECT id FROM work_artifacts WHERE run_id=%s", (root["run_id"],)):
            artifacts.path(file["id"]).unlink(missing_ok=True)
        connection.portal.call(app.state.db.execute,
                               "DELETE FROM work_sandbox_sessions WHERE run_id=%s", (root["run_id"],))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (root["run_id"],))
        connection.portal.call(app.state.db.execute, "DELETE FROM workflow_runs WHERE id=%s", (identifier,))
        connection.delete(f"/api/v1/work-tasks/{root['task_id']}", headers=ORIGIN)
        connection.portal.call(service.close)
