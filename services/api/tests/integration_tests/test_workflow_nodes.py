"""Visual definitions execute retrieval, model and condition nodes with source evidence."""

import json
from contextlib import asynccontextmanager
from uuid import uuid4

from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from test_api import DATABASE, ORIGIN, TestModel, add_user, run_async
from test_api import client as client


def test_rag_llm_condition_nodes_and_publish_gate(client):
    connection, app = client
    kb = connection.post(
        "/api/v1/capabilities/knowledge", json={"name": "流程知识-" + str(uuid4())}, headers=ORIGIN
    ).json()["id"]
    connection.post(
        f"/api/v1/capabilities/knowledge/{kb}/documents",
        files={"file": ("warranty.txt", "产品保修三年，凭发票申请维修。".encode())},
        headers=ORIGIN,
    )
    prompts = []

    class InspectModel(TestModel):
        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages[-1].content)
            return "你好，产品保修三年。"

    app.state.runtime.model_factory = lambda _: InspectModel(responses=["unused"])
    definition = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "search", "kind": "rag", "parameters": {"knowledge_id": kb}},
            {"id": "answer", "kind": "llm", "input": "search"},
            {
                "id": "choose",
                "kind": "condition",
                "input": "answer",
                "parameters": {"operator": "contains", "value": "你好"},
            },
            {"id": "yes", "kind": "end", "input": "answer"},
            {"id": "no", "kind": "end", "input": "search"},
        ],
        "edges": [
            {"source": "start", "target": "search"},
            {"source": "search", "target": "answer"},
            {"source": "answer", "target": "choose"},
            {"source": "choose", "target": "yes", "case": "true"},
            {"source": "choose", "target": "no", "case": "false"},
        ],
    }
    body = {"name": "画布验收-" + str(uuid4()), "model_id": "test", "definition": definition}
    created = connection.post("/api/v1/workflows", json=body, headers=ORIGIN)
    assert created.status_code == 201
    identifier = created.json()["id"]
    prefix = f"/api/v1/workflows/{identifier}"
    assert connection.post(prefix + "/publish", json={"revision": 1}, headers=ORIGIN).status_code == 409
    tested = connection.post(prefix + "/test-runs", json={"text": "保修多久"}, headers=ORIGIN)
    assert tested.status_code == 200 and "保修三年" in tested.json()["output"]
    assert "保修三年" in prompts[0]
    events = connection.get(f"{prefix}/runs/{tested.json()['id']}/events").json()["items"]
    completed = [event["node_id"] for event in events if event["event"] == "node.completed"]
    assert completed == ["start", "search", "answer", "choose", "yes"]
    published = connection.post(prefix + "/publish", json={"revision": 1}, headers=ORIGIN)
    assert published.status_code == 201 and published.json()["version"] == 1
    assert connection.put(prefix + "/versions/1/state", json={"active": False}, headers=ORIGIN).status_code == 200
    assert connection.post(prefix + "/versions/1/runs", json={"text": "保修"}, headers=ORIGIN).status_code == 409
    assert connection.put(prefix + "/versions/1/state", json={"active": True}, headers=ORIGIN).status_code == 200
    assert (
        connection.put(
            prefix, json={**body, "revision": 1, "layout": {"start": {"x": 100, "y": 200}}}, headers=ORIGIN
        ).status_code
        == 200
    )
    row = next(item for item in connection.get("/api/v1/workflows").json()["items"] if item["id"] == identifier)
    assert row["tested_revision"] == row["revision"] == 2 and row["layout"]["start"]["x"] == 100
    username = "workflow-private-" + str(uuid4())
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
    )
    assert connection.get(f"{prefix}/runs/{tested.json()['id']}/events").status_code == 404
    assert connection.post("/api/v1/workflows", json={**body, "name": "越权"}, headers=ORIGIN).status_code == 404


def test_transform_http_and_node_failure_are_recorded(client, monkeypatch):
    connection, app = client
    called = []

    def fetch(url):
        called.append(url)
        return " hello public "

    monkeypatch.setattr("app.workflow_runtime.fetch_public_text", fetch)
    definition = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "fetch", "kind": "http", "parameters": {"url": "https://example.org/text"}},
            {"id": "format", "kind": "transform", "input": "fetch", "parameters": {"operation": "upper"}},
            {"id": "end", "kind": "end", "input": "format"},
        ],
        "edges": [
            {"source": "start", "target": "fetch"},
            {"source": "fetch", "target": "format"},
            {"source": "format", "target": "end"},
        ],
    }
    body = {"name": "读取验收-" + str(uuid4()), "model_id": "test", "definition": definition}
    identifier = connection.post("/api/v1/workflows", json=body, headers=ORIGIN).json()["id"]
    prefix = f"/api/v1/workflows/{identifier}"
    result = connection.post(prefix + "/test-runs", json={"text": "ignored"}, headers=ORIGIN)
    assert result.status_code == 200 and result.json()["output"] == " HELLO PUBLIC "
    assert called == ["https://example.org/text"]
    definition["nodes"][2]["parameters"] = {"operation": "json_get", "path": "name"}
    assert connection.put(prefix, json={**body, "revision": 1}, headers=ORIGIN).status_code == 200
    failed = connection.post(prefix + "/test-runs", json={"text": "test"}, headers=ORIGIN)
    assert failed.status_code == 502
    row = connection.get(prefix + "/runs").json()["items"][0]
    assert row["error_node"] == "format" and row["status"] == "FAILED"
    assert connection.post(prefix + "/publish", json={"revision": 2}, headers=ORIGIN).status_code == 409


def test_agent_and_read_only_mcp_nodes_respect_current_bindings(client):
    from psycopg.types.json import Jsonb

    connection, app = client
    server_id = uuid4()
    metadata = {
        "name": "lookup",
        "description": "Read a value",
        "read_only": True,
        "enabled": True,
        "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO mcp_servers(id,name,url,tools) VALUES(%s,%s,%s,%s)",
        (server_id, "节点工具-" + str(server_id), "https://example.org/mcp", Jsonb([metadata])),
    )
    assert (
        connection.put("/api/v1/capabilities/bindings", json={"mcp_ids": [str(server_id)]}, headers=ORIGIN).status_code
        == 200
    )
    called, targets = [], []
    behavior = {"revoke_during_discovery": False}
    owner = connection.get("/api/v1/auth/me").json()["id"]

    class Session:
        async def list_tools(self):
            if behavior["revoke_during_discovery"]:
                await app.state.db.execute(
                    "UPDATE capability_bindings SET mcp_ids=%s WHERE owner_id=%s", (Jsonb([]), owner))
            return ListToolsResult(tools=[Tool(name="lookup", input_schema=metadata["schema"],
                annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))])

        async def call_tool(self, name, arguments):
            assert name == "lookup"
            called.append(arguments)
            return CallToolResult(content=[TextContent(type="text", text="tool-value:" + arguments["query"])])

    @asynccontextmanager
    async def session(server):
        targets.append(server["url"])
        yield Session()

    app.state.mcp.session = session
    agent = connection.post(
        "/api/v1/agents",
        json={"name": "节点助手-" + str(uuid4()), "model_id": "test", "instructions": "只读节点助手"},
        headers=ORIGIN,
    ).json()["id"]
    definition = {
        "nodes": [
            {"id": "start", "kind": "start"},
            {
                "id": "tool",
                "kind": "mcp",
                "parameters": {"server_id": str(server_id), "tool": "lookup", "arguments": {"query": {"ref": "input"}}},
            },
            {"id": "assistant", "kind": "agent", "input": "tool", "parameters": {"agent_id": agent}},
            {"id": "end", "kind": "end", "input": "assistant"},
        ],
        "edges": [
            {"source": "start", "target": "tool"},
            {"source": "tool", "target": "assistant"},
            {"source": "assistant", "target": "end"},
        ],
    }
    body = {"name": "工具智能体验收-" + str(uuid4()), "model_id": "test", "definition": definition}
    result = connection.post("/api/v1/workflows", json=body, headers=ORIGIN)
    assert result.status_code == 201
    identifier = result.json()["id"]
    prefix = f"/api/v1/workflows/{identifier}"
    try:
        tested = connection.post(prefix + "/test-runs", json={"text": "requested-value"}, headers=ORIGIN)
        assert tested.status_code == 200 and tested.json()["output"] == "你好，已收到。"
        assert called == [{"query": "requested-value"}]
        assert connection.post(prefix + "/publish", json={"revision": 1}, headers=ORIGIN).status_code == 201
        connection.portal.call(
            app.state.db.execute,
            "UPDATE mcp_servers SET url=%s WHERE id=%s",
            ("https://example.org/changed", server_id),
        )
        latest = connection.post(prefix + "/versions/1/runs", json={"text": "current-target"}, headers=ORIGIN)
        assert latest.status_code == 200, latest.text
        assert called == [{"query": "requested-value"}, {"query": "current-target"}]
        assert targets == ["https://example.org/mcp", "https://example.org/changed"]
        snapshots = connection.portal.call(app.state.db.fetch,
            "SELECT run_id,execution_config,used_at FROM run_resource_snapshots "
            "WHERE resource_kind='mcp' AND resource_id=%s AND run_id=ANY(%s::uuid[])",
            (str(server_id), [tested.json()["id"], latest.json()["id"]]))
        resolved = {str(row["run_id"]): app.state.models.vault.decrypt(row["execution_config"]).get_secret_value()
                    for row in snapshots}
        assert len(snapshots) == 2 and all(row["used_at"] for row in snapshots)
        # Decode only in the test: each published workflow run resolved its own current target.
        assert json.loads(resolved[tested.json()["id"]])["url"] == "https://example.org/mcp"
        assert json.loads(resolved[latest.json()["id"]])["url"] == "https://example.org/changed"
        behavior["revoke_during_discovery"] = True
        blocked_run = str(uuid4())
        blocked = connection.post(prefix + "/versions/1/runs",
            json={"text": "revoked-in-flight", "run_id": blocked_run}, headers=ORIGIN)
        assert blocked.status_code == 502, blocked.text
        events = connection.get(f"{prefix}/runs/{blocked_run}/events").json()["items"]
        assert any(item["event"] == "node.failed" and item["node_id"] == "tool"
                   and item["data"]["error_code"] == "MCP_BINDING_REVOKED" for item in events)
        assert len(called) == 2
        assert connection.post(prefix + "/test-runs", json={"text": "revoked"}, headers=ORIGIN).status_code == 403
        assert len(called) == 2 and len(targets) == 3
    finally:
        connection.delete(prefix, headers=ORIGIN)
        connection.portal.call(app.state.db.execute, "DELETE FROM mcp_servers WHERE id=%s", (server_id,))
