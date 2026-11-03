"""Verify per-agent bindings and independent authorization."""

from contextlib import asynccontextmanager
from uuid import uuid4

from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from psycopg.types.json import Jsonb
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_agent_bindings_are_independent_and_revocable(client) -> None:
    connection, app = client
    kb = connection.post("/api/v1/capabilities/knowledge", json={"name": f"库-{uuid4()}"}, headers=ORIGIN).json()["id"]
    connection.post(
        f"/api/v1/capabilities/knowledge/{kb}/documents",
        files={"file": ("a.txt", "产品保修三年".encode())},
        headers=ORIGIN,
    )
    a = connection.post(
        "/api/v1/agents", json={"name": f"甲-{uuid4()}", "model_id": "test", "knowledge_ids": [kb]}, headers=ORIGIN
    ).json()["id"]
    b = connection.post("/api/v1/agents", json={"name": f"乙-{uuid4()}", "model_id": "test"}, headers=ORIGIN).json()[
        "id"
    ]
    conversation_a = connection.post(
        "/api/v1/conversations", json={"title": "甲对话", "agent_id": a}, headers=ORIGIN
    ).json()["id"]
    conversation_b = connection.post(
        "/api/v1/conversations", json={"title": "乙对话", "agent_id": b}, headers=ORIGIN
    ).json()["id"]
    agents = connection.get("/api/v1/agents").json()["items"]
    first = next(item for item in agents if item["id"] == a)
    second = next(item for item in agents if item["id"] == b)
    first_tools, _ = connection.portal.call(app.state.capabilities.for_conversation, conversation_a, first)
    second_tools, _ = connection.portal.call(app.state.capabilities.for_conversation, conversation_b, second)
    assert [tool.name for tool in first_tools] == ["search_knowledge"] and not second_tools
    assert "保修" in connection.portal.call(first_tools[0].ainvoke, {"query": "保修"})
    body = {"name": first["name"], "model_id": "test", "revision": first["revision"], "knowledge_ids": []}
    assert connection.put(f"/api/v1/agents/{a}", json=body, headers=ORIGIN).status_code == 200
    try:
        connection.portal.call(first_tools[0].ainvoke, {"query": "保修"})
        raise AssertionError("Revoked knowledge should not remain callable")
    except Exception as error:
        assert getattr(error, "code", "") == "KNOWLEDGE_REVOKED"
    foreign_name = f"foreign-{uuid4()}"
    run_async(add_user(DATABASE, foreign_name, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login", json={"username": foreign_name, "password": "integration-password"}, headers=ORIGIN
    )
    denied = connection.post(
        "/api/v1/agents", json={"name": "越权", "model_id": "test", "knowledge_ids": [kb]}, headers=ORIGIN
    )
    assert denied.status_code == 404


def test_agent_skill_and_mcp_are_independent_and_mcp_revocation_applies(client) -> None:
    connection, app = client
    skill = connection.post(
        "/api/v1/capabilities/skills",
        files={"file": ("SKILL.md", b"---\nname: agent-example\ndescription: Example procedure\n---\n\n# Read this\n")},
        headers=ORIGIN,
    ).json()["id"]
    server = uuid4()
    tools = [
        {
            "name": "lookup",
            "description": "查询",
            "schema": {"type": "object", "properties": {}},
            "read_only": True,
            "enabled": True,
        }
    ]
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO mcp_servers(id,name,url,enabled,tools) VALUES (%s,%s,%s,true,%s)",
        (server, f"mcp-{uuid4()}", "http://127.0.0.1:8791/mcp", Jsonb(tools)),
    )
    body = {"name": f"资源助手-{uuid4()}", "model_id": "test", "skill_ids": [skill], "mcp_ids": [str(server)]}
    agent = connection.post("/api/v1/agents", json=body, headers=ORIGIN).json()["id"]
    conversation = connection.post(
        "/api/v1/conversations", json={"title": "资源", "agent_id": agent}, headers=ORIGIN
    ).json()["id"]
    snapshot = next(row for row in connection.get("/api/v1/agents").json()["items"] if row["id"] == agent)
    registered, middleware = connection.portal.call(app.state.capabilities.for_conversation, conversation, snapshot)
    assert len(middleware) == 2 and len(registered) == 1

    class Session:
        async def list_tools(self):
            return ListToolsResult(
                tools=[
                    Tool(
                        name="lookup", input_schema={"type": "object"}, annotations=ToolAnnotations(read_only_hint=True)
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            return CallToolResult(content=[TextContent(type="text", text="查询结果")])

    @asynccontextmanager
    async def session(server):
        yield Session()

    app.state.mcp.session = session
    assert "查询结果" in connection.portal.call(registered[0].ainvoke, {})
    assert (
        connection.put(
            f"/api/v1/agents/{agent}", json={**body, "mcp_ids": [], "revision": snapshot["revision"]}, headers=ORIGIN
        ).status_code
        == 200
    )
    try:
        connection.portal.call(registered[0].ainvoke, {})
        raise AssertionError("Revoked MCP should not remain callable")
    except Exception as error:
        assert getattr(error, "code", "") == "MCP_BINDING_REVOKED"
