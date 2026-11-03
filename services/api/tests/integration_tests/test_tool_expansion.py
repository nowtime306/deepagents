"""Real local OAuth/SSE and isolated protocol tests, with no provider credentials."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import mcp
import pytest
from deepagents.backends.protocol import ExecuteResponse
from deepagents.backends.state import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_api import ORIGIN
from test_api import client as client
from test_mcp_wire import SERVER

from app.errors import APIError
from app.graphs import build_work
from app.mcp_oauth import access_token, router
from app.tool_transports import MAX_TEXT, protocol_command, validate_command


@pytest.mark.parametrize(
    "command", [[], ["python", "x"], ["/bin/sh", "-c", "echo x"], ["/usr/local/bin/python", "x\x00y"], [1], ["x"] * 33]
)
def test_unapproved_executables_are_rejected(command):
    with pytest.raises(APIError):
        validate_command(command)


@pytest.mark.parametrize("change", ["revoke", "configure"])
def test_oauth_real_pkce_resource_owner_refresh_and_revoke(client, change):
    connection, app = client
    app.include_router(router)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    requests = []
    pause, entered, release = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            body = (
                {"resource": origin + "/mcp", "authorization_servers": [origin]}
                if "protected" in self.path
                else {
                    "issuer": origin,
                    "authorization_endpoint": origin + "/authorize",
                    "token_endpoint": origin + "/token",
                    "code_challenge_methods_supported": ["S256"],
                }
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            requests.append(values)
            if pause.is_set():
                entered.set()
                assert release.wait(10)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "token_type": "Bearer",
                        "access_token": "access-fixture",
                        "refresh_token": "refresh-fixture",
                        "expires_in": 300,
                        "scope": "read",
                    }
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        response = connection.post(
            "/api/v1/capabilities/mcp",
            headers=ORIGIN,
            json={"name": "OAuth-" + uuid4().hex, "url": base + "/mcp", "allow_private": True},
        )
        assert response.status_code == 201
        identifier = response.json()["id"]
        prefix = "/api/v1/tool-oauth/" + identifier
        assert (
            connection.put(
                prefix + "/client",
                headers=ORIGIN,
                json={"client_id": "fixture", "secret": "client-fixture", "scopes": "read"},
            ).status_code
            == 200
        )
        started = connection.post(prefix + "/authorize", headers=ORIGIN)
        assert started.status_code == 200
        query = parse_qs(urlsplit(started.json()["authorization_url"]).query)
        assert query["resource"] == [base + "/mcp"] and query["code_challenge_method"] == ["S256"]
        callback = "/api/v1/tool-oauth/callback?state=" + query["state"][0] + "&code=fixture"
        assert connection.get(callback, follow_redirects=False).status_code == 303
        assert connection.get(callback, follow_redirects=False).status_code == 403
        assert requests[0]["resource"] == [base + "/mcp"] and requests[0]["code_verifier"]
        config = connection.portal.call(app.state.db.fetch, "SELECT * FROM mcp_servers WHERE id=%s", (identifier,))[0]
        vault = app.state.models.vault
        assert connection.portal.call(access_token, app.state.db, vault, UUID(owner), config) == "access-fixture"
        with pytest.raises(APIError):
            connection.portal.call(access_token, app.state.db, vault, uuid4(), config)
        connection.portal.call(
            app.state.db.execute,
            "UPDATE mcp_oauth_grants SET expires_at=now()-interval '1 second' WHERE server_id=%s",
            (identifier,),
        )
        assert connection.portal.call(access_token, app.state.db, vault, UUID(owner), config) == "access-fixture"
        assert len(requests) == 2 and requests[1]["grant_type"] == ["refresh_token"]
        status = connection.get(prefix + "/status").json()
        assert "access-fixture" not in json.dumps(status) and status["grant"]
        assert connection.delete(prefix + "/grant", headers=ORIGIN).status_code == 200
        with pytest.raises(APIError):
            connection.portal.call(access_token, app.state.db, vault, UUID(owner), config)
        # Keep the token response in flight while a separate request revokes/configures.
        started = connection.post(prefix + "/authorize", headers=ORIGIN)
        state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
        pause.set()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                connection.get, "/api/v1/tool-oauth/callback?state=" + state + "&code=delayed",
                follow_redirects=False,
            )
            assert entered.wait(5)
            try:
                if change == "revoke":
                    assert connection.delete(prefix + "/grant", headers=ORIGIN).status_code == 200
                else:
                    assert connection.put(
                        prefix + "/client", headers=ORIGIN, json={"client_id": "new-client", "scopes": "read"}
                    ).status_code == 200
            finally:
                release.set()
            assert pending.result(5).status_code in {403, 409}
        assert connection.get(prefix + "/status").json()["grant"] is None
        assert not connection.portal.call(
            app.state.db.fetch, "SELECT * FROM mcp_oauth_grants WHERE server_id=%s", (identifier,)
        )
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_real_legacy_sse_discovery_and_call(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = SERVER.replace("transport='streamable-http'", "transport='sse'").replace(
        ",stateless_http=True,json_response=True", ""
    )
    counter = app.state.settings.data_dir / ("sse-" + uuid4().hex)
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script, str(port), str(counter), str(Path(mcp.__file__).parent.parent)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **flags,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(process.stderr.read().decode("utf-8", "replace"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        identifier = connection.post(
            "/api/v1/capabilities/mcp",
            headers=ORIGIN,
            json={
                "name": "SSE-" + uuid4().hex,
                "url": f"http://127.0.0.1:{port}/sse",
                "transport": "sse",
                "allow_private": True,
            },
        ).json()["id"]
        row = connection.portal.call(app.state.db.fetch, "SELECT * FROM mcp_servers WHERE id=%s", (identifier,))[0]
        connection.portal.call(app.state.mcp.discover, row)
        discovered = connection.post(f"/api/v1/capabilities/mcp/{identifier}/discover", headers=ORIGIN)
        assert discovered.status_code == 200, discovered.json()
        assert {item["name"] for item in discovered.json()["items"]} == {"read_count", "write_record"}
    finally:
        process.terminate()
        process.wait(8)
        process.stderr.close()
        counter.unlink(missing_ok=True)


def test_isolated_work_tool_requires_approval_keeps_exact_arguments_and_rechecks_pin(client):
    connection, app = client
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    identifier = connection.post(
        "/api/v1/capabilities/mcp",
        headers=ORIGIN,
        json={
            "name": "隔离-" + uuid4().hex,
            "transport": "stdio",
            "command": ["/usr/local/bin/python", "-c", "print('local')"],
        },
    ).json()["id"]
    connection.put("/api/v1/capabilities/bindings", headers=ORIGIN, json={"mcp_ids": [identifier]})
    calls = []

    class Backend:
        async def _execute(self, command, seconds, name, arguments):
            calls.append((name, arguments))
            return ExecuteResponse(output="本机已验证", exit_code=0)

    async def tools_for_work():
        bindings = await app.state.capabilities.bindings(owner)
        selection = await app.state.capabilities.freeze(owner, bindings)
        ask, _ = await app.state.capabilities.for_owner(owner, selection)
        assert not any(tool.name.startswith("isolated_") for tool in ask)
        tools, _ = await app.state.capabilities.for_owner(
            owner, selection, work_run={"id": uuid4(), "lease_token": uuid4()}, workspace=Backend()
        )
        return tools

    tools = connection.portal.call(tools_for_work)
    tool = next(t for t in tools if t.name.startswith("isolated_"))

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    arguments = {"operation": "discover"}
    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"name": tool.name, "id": "local", "args": arguments}]),
            AIMessage(content="已完成"),
        ]
    )
    graph = build_work(model, InMemorySaver(), backend=StateBackend(), tools=tools)
    config = {"configurable": {"thread_id": "isolated-" + uuid4().hex}}
    result = connection.portal.call(graph.ainvoke, {"messages": [{"role": "user", "content": "发现工具"}]}, config)
    assert result["__interrupt__"] and calls == []
    connection.portal.call(graph.ainvoke, Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert calls == [(tool.name, arguments)]
    connection.portal.call(app.state.db.execute, "UPDATE mcp_servers SET command_json='[]' WHERE id=%s", (identifier,))
    connection.portal.call(tool.ainvoke, arguments)
    assert len(calls) == 2
    # A warm tool retains its originally approved command, not a later empty command.
    assert calls[-1] == (tool.name, arguments)


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires trusted local coding image")
@pytest.mark.parametrize("transport", ["stdio", "acp"])
def test_isolated_native_jsonrpc_protocol(transport):
    agent = """
import json,sys
for line in sys.stdin:
 m=json.loads(line); method=m.get('method')
 if 'id' not in m: continue
 if method=='initialize': result={'protocolVersion':m['params']['protocolVersion']}
 elif method=='session/new': result={'sessionId':'local-session'}
 elif method=='session/prompt': result={'stopReason':'end_turn'}
 else: result={'tools':[]}
 if method=='session/prompt':
  update={'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'local proof'}}
  print(json.dumps({'jsonrpc':'2.0','method':'session/update',
    'params':{'sessionId':'local-session','update':update}}),flush=True)
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
"""
    body = {
        "command": ["/usr/local/bin/python", "-u", "-c", agent],
        "transport": transport,
        "operation": "discover",
        "text": "😀'" * (MAX_TEXT // 2),
    }
    command = protocol_command(body)
    assert len(command) <= 16000
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "128m",
            "--cpus",
            "0.25",
            "-i",
            "--entrypoint",
            "sh",
            "owner-agent/coding:dev",
            "-c",
            "mkdir -p /workspace && " + command,
        ],
        input=json.dumps(body),
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    if transport == "acp":
        assert actual["session_id"] == "local-session" and len(actual["updates"]) == 1
    else:
        assert actual["tools"] == []
