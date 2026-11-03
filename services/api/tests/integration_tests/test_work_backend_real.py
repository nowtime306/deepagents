"""Opt-in acceptance against local OpenSandbox; never contacts an external model."""

import asyncio
import os
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from opensandbox.config import ConnectionConfig
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.durable_sandboxes import DurableSandboxes
from app.graphs import build_work
from app.work_backend import WorkBackend
from app.work_queue import WorkQueue


@pytest.mark.skipif(
    os.getenv("RUN_LOCAL_SANDBOX") != "1",
    reason="Requires explicitly enabled local sandbox acceptance",
)
def test_actual_task_workspace_commands_and_paths(client):
    connection, app, run = prepare(client)
    key = (Path(__file__).parents[3] / "sandbox" / ".local" / "manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)

    async def exercise():
        service = DurableSandboxes(app.state.db, config)
        queue = WorkQueue(app.state.db)
        session = sandbox = None

        async def guard():
            if not await queue.renew(run["id"], run["lease_token"]):
                raise RuntimeError("Lease lost")

        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                await guard()

        pulse = asyncio.create_task(heartbeat())
        try:
            created = await service.create(run["id"], run["lease_token"], "coding")
            assert created is not None
            session, sandbox = created
            backend = WorkBackend(sandbox, guard)
            await backend.initialize()
            assert not (await backend.awrite("/nested/code.py", "print(1+1)")).error
            assert (await backend.aread("/nested/code.py")).file_data is not None
            assert not (await backend.aedit("/nested/code.py", "1+1", "2+3")).error
            result = await backend.aexecute("python3 nested/code.py")
            assert result.exit_code == 0 and result.output.strip() == "5"
            assert (await backend.agrep("2+3")).matches
            assert (await backend.aglob("*.py")).matches
            assert (await backend.als("/nested")).entries
            result = await backend.aexecute("ln -s /etc/passwd /workspace/leak")
            assert result.exit_code == 0
            assert (await backend.aread("/leak")).error
            assert (await backend.aread("/etc/passwd")).error
            with pytest.raises(ValueError):
                await backend.aread("/../etc/passwd")
            result = await backend.aexecute("sleep 3", timeout=1)
            assert result.exit_code == 124
            result = await backend.aexecute("python3 -c \"print('x'*100000)\"")
            assert result.truncated and len(result.output) <= 65536

            class ToolModel(FakeMessagesListChatModel):
                def bind_tools(self, tools, **kwargs):
                    return self

            model = ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "write_file",
                                "id": "write",
                                "args": {"file_path": "/agent.py", "content": "print(7)"},
                            }
                        ],
                    ),
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "execute", "id": "exec", "args": {"command": "python3 agent.py"}}],
                    ),
                    AIMessage(content="已通过实际执行验证"),
                ]
            )
            graph = build_work(model, InMemorySaver(), backend=backend)
            graph_config = {"configurable": {"thread_id": str(run["id"])}, "recursion_limit": 30}
            state = await graph.ainvoke({"messages": [{"role": "user", "content": "写入脚本并执行"}]}, graph_config)
            assert state.get("__interrupt__")
            state = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), graph_config)
            assert state["messages"][-1].text == "已通过实际执行验证"
            assert any("7" in str(message.content) for message in state["messages"] if message.type == "tool")
            assert not (await backend.adelete("/nested/code.py")).error
            assert (await backend.aread("/nested/code.py")).error
            assert await service.owned(run["id"], run["lease_token"], session["id"])
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            if session:
                assert await service.release(session["id"])
            if sandbox:
                await sandbox.close()
            await service.close()

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)
