"""MCP writes require Work, current admin role, pinned target and single-use approval."""

import hashlib
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from deepagents.backends.state import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.errors import APIError
from app.graphs import build_work
from app.operation_journal import receipts
from app.work_engine import WorkEngine
from app.work_queue import WorkQueue


def test_mcp_write_approval_unknown_outcome_and_revocation(client):
    connection, app = client
    account = connection.get("/api/v1/auth/me").json()
    owner = account["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    server = connection.post(
        "/api/v1/capabilities/mcp",
        json={"name": "写入验收-" + str(uuid4()), "url": "http://127.0.0.1:8791/mcp", "allow_private": True},
        headers=ORIGIN,
    ).json()["id"]
    calls = []
    lost = False

    class Session:
        async def list_tools(self):
            return ListToolsResult(
                tools=[
                    Tool(
                        name="update_record",
                        description="修改测试记录",
                        input_schema={
                            "type": "object",
                            "properties": {"value": {"type": "integer"}},
                            "required": ["value"],
                        },
                        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            calls.append(arguments["value"])
            if lost:
                raise TimeoutError("Response lost after the write")
            return CallToolResult(content=[TextContent(type="text", text="写入完成")])

    @asynccontextmanager
    async def session(_):
        yield Session()

    app.state.mcp.session = session
    connection.post(f"/api/v1/capabilities/mcp/{server}/discover", headers=ORIGIN)
    assert (
        connection.put(
            f"/api/v1/capabilities/mcp/{server}/tools", json={"names": ["update_record"]}, headers=ORIGIN
        ).status_code
        == 422
    )
    assert (
        connection.put(
            f"/api/v1/capabilities/mcp/{server}/tools",
            json={"names": ["update_record"], "allow_write_tools": True},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    agent = connection.post(
        "/api/v1/agents", json={"name": "写入助手", "model_id": "test", "mcp_ids": [server]}, headers=ORIGIN
    ).json()["id"]
    task = connection.post(
        "/api/v1/work-tasks", json={"title": "MCP写入验收", "goal": "写一条记录"}, headers=ORIGIN
    ).json()["id"]
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    response = connection.post(
        f"/api/v1/work-tasks/{task}/runs", json={"revision": 1, "model_id": "test", "agent_id": agent}, headers=ORIGIN
    )
    assert response.status_code == 202
    queue = WorkQueue(app.state.db)
    run = connection.portal.call(queue.claim)
    readonly, _ = connection.portal.call(app.state.capabilities.for_owner, run["owner_id"], run["agent_snapshot"])
    assert not any(tool.name.startswith("mcpw_") for tool in readonly)

    async def work_tools(current):
        return await app.state.capabilities.for_owner(current["owner_id"], current["agent_snapshot"], work_run=current)

    tools, _ = connection.portal.call(work_tools, run)
    tool = next(tool for tool in tools if tool.name.startswith("mcpw_"))

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"name": tool.name, "id": "write", "args": {"value": 1}}]),
            AIMessage(content="写入已执行"),
        ]
    )
    saver = InMemorySaver()
    graph = build_work(model, saver, backend=StateBackend(), tools=tools)
    config = {"configurable": {"thread_id": "work:" + str(run["id"])}}
    result = connection.portal.call(graph.ainvoke, {"messages": [{"role": "user", "content": "写入"}]}, config)
    assert result["__interrupt__"] and calls == []
    engine = WorkEngine(app.state.db, app.state.models, None, None, saver, lambda _: model)
    connection.portal.call(engine.pause, run, list(result["__interrupt__"]))
    approval = connection.get(f"/api/v1/work-runs/{run['id']}/approvals").json()["items"][0]
    assert "MCP 写操作" in approval["payload"]["action_requests"][0]["description"]
    assert (
        connection.post(
            f"/api/v1/work-approvals/{approval['id']}/decisions",
            json={"digest": approval["digest"], "decision": "approve"},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    run = connection.portal.call(queue.claim)
    connection.portal.call(engine.consume_approvals, run)
    tools, _ = connection.portal.call(work_tools, run)
    tool = next(tool for tool in tools if tool.name.startswith("mcpw_"))
    graph = build_work(model, saver, backend=StateBackend(), tools=tools)
    connection.portal.call(graph.ainvoke, Command(resume=run["resume_decision"]), config)
    assert calls == [1]
    with pytest.raises(PermissionError):
        connection.portal.call(tool.ainvoke, {"value": 1})
    assert calls == [1]
    # A second explicit receipt models a user-authorized retry with new parameters.
    approval_id = uuid4()
    payload = {"action_requests": [{"name": tool.name, "args": {"value": 2}}]}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    async def approve_again():
        async with app.state.db.pool.connection() as c:
            await c.execute(
                "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,"
                "expires_at,decision,consumed_at) "
                "VALUES(%s,%s,%s,'again',%s,%s,now()+interval '1 minute','approve',now())",
                (approval_id, run["id"], run["owner_id"], Jsonb(payload), digest),
            )
            await receipts(c, {"id": approval_id, "run_id": run["id"]}, payload["action_requests"])

    connection.portal.call(approve_again)
    lost = True
    with pytest.raises(TimeoutError):
        connection.portal.call(tool.ainvoke, {"value": 2})
    with pytest.raises(PermissionError):
        connection.portal.call(tool.ainvoke, {"value": 2})
    assert calls == [1, 2]
    operations = connection.get(f"/api/v1/work-runs/{run['id']}/operations").json()["items"]
    assert [item["status"] for item in operations] == ["SUCCEEDED", "UNKNOWN"]
    lost = False
    connection.portal.call(
        app.state.db.execute, "UPDATE mcp_servers SET url='http://127.0.0.1:8792/mcp' WHERE id=%s", (server,)
    )
    with pytest.raises(APIError, match="配置已变化"):
        connection.portal.call(tool.ainvoke, {"value": 3})
    connection.portal.call(
        app.state.db.execute, "UPDATE mcp_servers SET url='http://127.0.0.1:8791/mcp' WHERE id=%s", (server,)
    )
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=false WHERE id=%s", (owner,))
    with pytest.raises(APIError, match="平台管理员"):
        connection.portal.call(tool.ainvoke, {"value": 3})
    assert calls == [1, 2]
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run["id"],))
