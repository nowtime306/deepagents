"""Ask workflow invocation is explicit, bounded, deduplicated and live-authorized."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_workflow_composition import create, definition, publish

from app.errors import APIError
from app.workflow_queue import reconcile_startup
from app.workflow_runtime import WorkflowRuntime


def tool_context(connection, app, version, **resources):
    agent = connection.post(
        "/api/v1/agents",
        headers=ORIGIN,
        json={
            "name": "对话工作流助手",
            "model_id": "test",
            "workflow_version_ids": [version],
            **resources,
        },
    ).json()["id"]
    app.state.settings.ask_worker_enabled = True
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN, json={"agent_id": agent}).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        headers=ORIGIN,
        json={
            "model_id": "test",
            "agent_id": agent,
            "message": "请调用授权工作流",
        },
    ).json()["run_id"]
    connection.portal.call(app.state.db.execute, "UPDATE task_runs SET status='RUNNING' WHERE id=%s", (run,))
    user = connection.portal.call(
        app.state.db.fetch,
        "SELECT owner_id,agent_snapshot FROM task_runs r JOIN conversations c ON c.id=r.conversation_id WHERE r.id=%s",
        (run,),
    )[0]
    tools, _ = connection.portal.call(
        app.state.capabilities.for_conversation, UUID(conversation), user["agent_snapshot"]
    )
    tool = next(tool for tool in tools if tool.name == "run_workflow")
    return run, agent, tool


def test_ask_workflow_tool_reuses_result_and_rechecks_grant(client):
    connection, app = client
    _, version = publish(connection, definition(operation="upper"))
    run, agent, tool = tool_context(connection, app, version)
    first = json.loads(connection.portal.call(tool.ainvoke, {"version_id": version, "text": "hello"}))
    second = json.loads(connection.portal.call(tool.ainvoke, {"version_id": version, "text": "hello"}))
    assert first["output"] == second["output"] == "HELLO" and second["reused"]
    assert first["run_id"] == second["run_id"]
    root_usage = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (run,))
    assert root_usage == []  # Pure transforms do not manufacture a model call.
    connection.portal.call(app.state.db.execute, "UPDATE agents SET workflow_version_ids='[]' WHERE id=%s", (agent,))
    with pytest.raises(APIError) as error:
        connection.portal.call(tool.ainvoke, {"version_id": version, "text": "hello"})
    assert error.value.code == "WORKFLOW_REVOKED"


def test_ask_tool_rejects_hidden_resource_expansion_and_cancel(client):
    connection, app = client
    kb = connection.post("/api/v1/capabilities/knowledge", headers=ORIGIN, json={"name": "私有子流程资源"}).json()["id"]
    workflow = create(connection, definition("rag", knowledge_id=kb))
    assert (
        connection.post(f"/api/v1/workflows/{workflow}/test-runs", headers=ORIGIN, json={"text": "问题"}).status_code
        == 200
    )
    connection.post(f"/api/v1/workflows/{workflow}/publish", headers=ORIGIN, json={"revision": 1})
    version = connection.get(f"/api/v1/workflows/{workflow}/versions").json()["items"][0]["id"]
    run, _, tool = tool_context(connection, app, version)
    with pytest.raises(APIError) as error:
        connection.portal.call(tool.ainvoke, {"version_id": version, "text": "问题"})
    assert error.value.code == "WORKFLOW_SCOPE_EXCEEDED"
    connection.portal.call(app.state.db.execute, "UPDATE task_runs SET cancel_requested=true WHERE id=%s", (run,))
    with pytest.raises(APIError) as error:
        connection.portal.call(tool.ainvoke, {"version_id": version, "text": "问题"})
    assert error.value.code == "RESOURCE_PARENT_STOPPED"


def test_api_restart_preserves_live_ask_child_and_fences_expired_parent(client, monkeypatch):
    connection, app = client
    _, version = publish(connection, definition(operation="upper"))
    run, _, _ = tool_context(connection, app, version)

    async def scenario():
        await app.state.db.execute(
            "UPDATE task_runs SET lease_token=%s,lease_until=now()+interval '120 seconds' WHERE id=%s",
            (uuid4(), run),
        )
        parent = (await app.state.db.fetch("SELECT * FROM task_runs WHERE id=%s", (run,)))[0]
        tools, _ = await app.state.capabilities.for_conversation(parent["conversation_id"], parent["agent_snapshot"])
        tool = next(item for item in tools if item.name == "run_workflow")
        started, release = asyncio.Event(), asyncio.Event()

        async def gated(*args, **kwargs):
            started.set()
            await release.wait()
            return "HELLO"

        monkeypatch.setattr(WorkflowRuntime, "run", gated)
        for expired, text in [(False, "live"), (True, "expired")]:
            started.clear()
            release.clear()
            future = asyncio.create_task(tool.ainvoke({"version_id": version, "text": text}))
            await asyncio.wait_for(started.wait(), 5)
            try:
                if expired:
                    await app.state.db.execute(
                        "UPDATE task_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (run,)
                    )
                await reconcile_startup(app.state.db)
                child = (
                    await app.state.db.fetch(
                        "SELECT status FROM workflow_runs WHERE input=%s AND snapshot->>'parent_ask_run'=%s",
                        (text, run),
                    )
                )[0]
                assert child["status"] == ("NEEDS_REVIEW" if expired else "RUNNING")
            finally:
                release.set()
            if expired:
                with pytest.raises(APIError):
                    await future
            else:
                assert json.loads(await future)["output"] == "HELLO"
            child = (
                await app.state.db.fetch(
                    "SELECT status FROM workflow_runs WHERE input=%s AND snapshot->>'parent_ask_run'=%s",
                    (text, run),
                )
            )[0]
            assert child["status"] == ("NEEDS_REVIEW" if expired else "SUCCEEDED")

    connection.portal.call(scenario)
