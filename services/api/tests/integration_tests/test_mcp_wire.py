"""Official MCP v2 server/client over real localhost HTTP, including response loss."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import mcp
import pytest
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.operation_journal import receipts
from app.work_queue import WorkQueue

SERVER = """
import os,sys,site
site.addsitedir(sys.argv[3])
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
server=MCPServer('owner-agent-wire-fixture')
path=Path(sys.argv[2])
@server.tool(description='只读计数',annotations=ToolAnnotations(read_only_hint=True))
def read_count()->str:
 return str(len(path.read_text().splitlines()) if path.exists() else 0)
@server.tool(description='写入独立测试文件',annotations=ToolAnnotations(read_only_hint=False,destructive_hint=True))
def write_record(value:int)->str:
 with path.open('a') as file:
  file.write(str(value)+'\\n');file.flush();os.fsync(file.fileno())
 if value==2:os._exit(0)
 return 'written'
server.run(transport='streamable-http',host='127.0.0.1',port=int(sys.argv[1]),stateless_http=True,json_response=True)
"""


def test_actual_mcp_http_and_no_repeat_after_server_dies(client):
    connection, app = client
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    counter = app.state.settings.data_dir / ("mcp-wire-" + uuid4().hex + ".txt")
    process = None

    def start():
        env = {**os.environ, "PYTHONPATH": str(Path(mcp.__file__).parent.parent)}
        child = subprocess.Popen(
            [sys._base_executable, "-c", SERVER, str(port), str(counter), str(Path(mcp.__file__).parent.parent)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if child.poll() is not None:
                message = child.stderr.read().decode("utf-8", "replace")
                child.stderr.close()
                raise AssertionError("MCP fixture exited: " + message)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return child
            except OSError:
                time.sleep(0.1)
        child.terminate()
        child.wait(timeout=5)
        child.stderr.close()
        raise AssertionError("MCP fixture did not listen")

    run = None
    try:
        process = start()
        owner = connection.get("/api/v1/auth/me").json()["id"]
        connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
        identifier = connection.post(
            "/api/v1/capabilities/mcp",
            json={"name": "真实协议验收-" + uuid4().hex, "url": f"http://127.0.0.1:{port}/mcp", "allow_private": True},
            headers=ORIGIN,
        ).json()["id"]
        response = connection.post(f"/api/v1/capabilities/mcp/{identifier}/discover", headers=ORIGIN)
        assert response.status_code == 200
        assert {tool["name"] for tool in response.json()["items"]} == {"read_count", "write_record"}
        assert (
            connection.put(
                f"/api/v1/capabilities/mcp/{identifier}/tools",
                json={"names": ["read_count", "write_record"], "allow_write_tools": True},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        agent = connection.post(
            "/api/v1/agents", json={"name": "协议助手", "model_id": "test", "mcp_ids": [identifier]}, headers=ORIGIN
        ).json()["id"]
        task = connection.post(
            "/api/v1/work-tasks", json={"title": "MCP协议测试", "goal": "检查受控写入"}, headers=ORIGIN
        ).json()["id"]
        app.state.settings.work_enabled = True
        connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
        assert (
            connection.post(
                f"/api/v1/work-tasks/{task}/runs",
                json={"revision": 1, "model_id": "test", "agent_id": agent},
                headers=ORIGIN,
            ).status_code
            == 202
        )
        run = connection.portal.call(WorkQueue(app.state.db).claim)

        async def tools():
            return await app.state.capabilities.for_owner(run["owner_id"], run["agent_snapshot"], work_run=run)

        registered, _ = connection.portal.call(tools)
        writer = next(tool for tool in registered if tool.name.startswith("mcpw_"))
        reader = next(tool for tool in registered if tool.name.endswith("_read_count"))

        def approve(value):
            approval = uuid4()
            payload = {"action_requests": [{"name": writer.name, "args": {"value": value}}]}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

            async def record():
                async with app.state.db.pool.connection() as c:
                    await c.execute(
                        "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,"
                        "expires_at,decision,consumed_at) "
                        "VALUES(%s,%s,%s,%s,%s,%s,now()+interval '1 minute','approve',now())",
                        (approval, run["id"], run["owner_id"], str(approval), Jsonb(payload), digest),
                    )
                    await receipts(c, {"id": approval, "run_id": run["id"]}, payload["action_requests"])

            connection.portal.call(record)

        approve(1)
        assert "written" in connection.portal.call(writer.ainvoke, {"value": 1})
        assert "1" in connection.portal.call(reader.ainvoke, {})
        approve(2)
        with pytest.raises((ExceptionGroup, RuntimeError, ConnectionError, TimeoutError)):
            connection.portal.call(writer.ainvoke, {"value": 2})
        process.wait(timeout=5)
        process.stderr.close()
        process = start()
        with pytest.raises(ExceptionGroup) as denial:
            connection.portal.call(writer.ainvoke, {"value": 2})
        assert denial.value.subgroup(PermissionError) is not None
        assert counter.read_text().splitlines() == ["1", "2"]
        rows = connection.get(f"/api/v1/work-runs/{run['id']}/operations").json()["items"]
        assert [row["status"] for row in rows] == ["SUCCEEDED", "UNKNOWN"]
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if process and process.stderr:
            process.stderr.close()
        if run:
            connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run["id"],))
        counter.unlink(missing_ok=True)
