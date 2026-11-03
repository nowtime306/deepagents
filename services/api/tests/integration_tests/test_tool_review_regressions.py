"""Independent review regressions: bounded protocol inputs and OAuth transport/discovery."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.errors import APIError
from app.mcp_oauth import bounded_json, discover, oauth_target, store_grant
from app.tool_transports import MAX_TEXT, protocol_command
from app.work_backend import WorkBackend


def test_protocol_budget_uses_real_work_backend_boundary():
    async def exercise():
        backend = WorkBackend(SimpleNamespace(id="local"), AsyncMock())
        backend._call = AsyncMock(return_value={"exit_code": 0, "output": "ok", "truncated": False})
        body = {"transport": "acp", "command": ["/usr/local/bin/python", "-c", "pass"], "text": "😀" * MAX_TEXT}
        command = protocol_command(body)
        assert len(command) <= 16000
        result = await backend._execute(command, 10, "isolated_test", {"text": body["text"]})
        assert result.exit_code == 0 and backend._call.await_count == 1
        body["arguments"] = {"oversized": "x" * 16000}
        with pytest.raises(APIError, match="协议预算"):
            protocol_command(body)
        assert backend._call.await_count == 1
        rejected = await backend._execute("x" * 16001, 10, "isolated_test", {})
        assert rejected.exit_code == 1 and backend._call.await_count == 1
    asyncio.run(exercise())


@pytest.mark.parametrize("url,private,allowed", [
    ("http://8.8.8.8/mcp", False, False),
    ("http://8.8.8.8/token", True, False),
    ("http://10.0.0.1/token", True, False),
    ("http://127.0.0.1/token", False, False),
    ("http://127.0.0.1/token", True, True),
    ("http://[::1]/token", True, True),
    ("https://8.8.8.8/token", False, True),
])
def test_oauth_https_or_explicit_ip_loopback_only(url, private, allowed):
    if allowed:
        asyncio.run(oauth_target(url, private))
    else:
        with pytest.raises(APIError):
            asyncio.run(oauth_target(url, private))


@pytest.mark.parametrize("metadata_mode,oidc_mode", [("root", "root"), ("header", "insert"), ("path", "append")])
def test_real_resource_and_oidc_discovery_fallback(metadata_mode, oidc_mode):
    visited = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            visited.append(self.path)
            origin = f"http://127.0.0.1:{self.server.server_port}"
            path = "" if oidc_mode == "root" else "/tenant"
            resource_path = {"root": "/.well-known/oauth-protected-resource", "header": "/metadata",
                             "path": "/.well-known/oauth-protected-resource/mcp"}[metadata_mode]
            issuer_path = {"root": "/.well-known/openid-configuration",
                           "insert": "/.well-known/openid-configuration/tenant",
                           "append": "/tenant/.well-known/openid-configuration"}[oidc_mode]
            body = None
            if self.path == "/mcp":
                self.send_response(401)
                if metadata_mode == "header":
                    self.send_header("WWW-Authenticate", f'Bearer resource_metadata="{origin}/metadata"')
            elif self.path == resource_path:
                self.send_response(200)
                body = {"resource": origin + "/mcp", "authorization_servers": [origin + path]}
            elif self.path == issuer_path:
                self.send_response(200)
                body = {"issuer": origin + path, "authorization_endpoint": origin + "/authorize",
                        "token_endpoint": origin + "/token", "code_challenge_methods_supported": ["S256"]}
            else:
                self.send_response(404)
            self.end_headers()
            if body:
                self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async def exercise():
            async with httpx.AsyncClient(trust_env=False) as client:
                target = {"url": f"http://127.0.0.1:{server.server_port}/mcp", "allow_private": True}
                return await discover(target, client)
        assert asyncio.run(exercise())["token_endpoint"].endswith("/token")
        assert visited[0] == "/mcp"
        if metadata_mode == "header":
            assert visited[1] == "/metadata" and not any("protected-resource" in p for p in visited)
        if metadata_mode == "root":
            assert visited[1:3] == [
                "/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"
            ]
        if oidc_mode == "append":
            assert visited[-3:] == [
                "/.well-known/oauth-authorization-server/tenant", "/.well-known/openid-configuration/tenant",
                "/tenant/.well-known/openid-configuration"
            ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_discovery_rejects_unsafe_header_without_sending_credentials():
    calls = []
    async def exercise():
        def reply(request):
            calls.append(request)
            assert "authorization" not in request.headers
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="http://169.254.169.254/x"'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(APIError):
                await discover({"url": "https://8.8.8.8/mcp", "allow_private": False}, client)
    asyncio.run(exercise())
    assert len(calls) == 1


@pytest.mark.parametrize("limit", [32768, 131072])
def test_local_chunked_oversize_response_is_closed_early(limit):
    sent, disconnected = [], threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(1024):
                    self.wfile.write(b"1000\r\n" + b"x" * 4096 + b"\r\n")
                    self.wfile.flush()
                    sent.append(4096)
                    time.sleep(0.001)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async def exercise():
            async with httpx.AsyncClient(trust_env=False) as client:
                with pytest.raises(APIError, match="超过限制"):
                    await bounded_json(client, "GET", f"http://127.0.0.1:{server.server_port}/large", True, limit=limit)
        asyncio.run(exercise())
        assert disconnected.wait(5) and sum(sent) < 4 * 1024 * 1024
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("token", [[], {"token_type": []}, {"token_type": "Bearer", "access_token": []},
                                  {"token_type": "Bearer", "access_token": "ok", "scope": []}])
def test_token_json_shape_is_safe(token):
    async def exercise():
        with pytest.raises(APIError):
            transport = httpx.MockTransport(lambda _: httpx.Response(200, json=token))
            async with httpx.AsyncClient(transport=transport) as client:
                value = await bounded_json(client, "POST", "http://127.0.0.1/token", True, limit=32768)
            await store_grant(AsyncMock(), AsyncMock(), None, {}, {"scopes": "read"}, {}, value)
    asyncio.run(exercise())


def test_compressed_oauth_body_is_rejected_before_decompression():
    class NeverRead(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("Compressed body must not be read")
            yield b""

    async def exercise():
        def response(request):
            assert request.headers["accept-encoding"] == "identity"
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=NeverRead())
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            with pytest.raises(APIError, match="未压缩"):
                await bounded_json(client, "GET", "http://127.0.0.1/metadata", True)
    asyncio.run(exercise())
