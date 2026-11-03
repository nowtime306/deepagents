"""BUG-EXP006 routing: local callbacks stay within their configured site."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.ask_automations import automation_tools
from app.ask_resources import AskKnowledge
from app.automation_types import ScheduleInput
from app.config import DEFAULT_INTERNAL_API_URL, Settings, validate_internal_api_url
from app.errors import APIError
from app.work_resources import WorkKnowledge, WorkWorkflows


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8139", "http://example.com:8139", "http://localhost:8139", "http://10.0.0.1:8139",
    "http://127.0.0.1", "http://127.0.0.1:80", "http://127.0.0.1:65536", "http://127.0.0.1:8139/path",
    "http://u:p@127.0.0.1:8139", "http://@127.0.0.1:8139", "http://127.0.0.1:8139?x=1",
    "http://127.0.0.1:8139?", "http://127.0.0.1:8139#", " http://127.0.0.1:8139", "http://[::1%lo]:8139",
])
def test_internal_url_rejects_nonlocal_or_ambiguous_targets(url):
    with pytest.raises(ValueError):
        Settings(internal_api_url=url)
    with pytest.raises(ValueError):
        automation_tools("http://localhost:3100", uuid4(), uuid4(), url)


def test_settings_internal_url_default_and_environment(monkeypatch):
    assert Settings().internal_api_url == DEFAULT_INTERNAL_API_URL
    monkeypatch.setenv("PLATFORM_INTERNAL_API_URL", "http://127.0.0.1:8139/")
    assert Settings.from_env().internal_api_url == "http://127.0.0.1:8139"
    assert validate_internal_api_url("http://[::1]:8139") == "http://[::1]:8139"


async def all_callbacks(base=None, token=None):
    owner, identifier, version, knowledge = uuid4(), uuid4(), uuid4(), uuid4()
    run = {"id": identifier, "owner_id": owner, "lease_token": token or uuid4(),
           "agent_snapshot": {"workflow_version_ids": [str(version)]}}
    db = SimpleNamespace(fetch=AsyncMock(return_value=[{}]))
    extra = () if base is None else (base,)
    ask = AskKnowledge(db, run, "http://localhost:3100", *extra)
    work = WorkKnowledge(db, run, "http://localhost:3100", *extra)
    await ask.search(knowledge, owner, "local")
    await work.search(knowledge, owner, "local")
    targets, draft = automation_tools("http://localhost:3100", identifier, run["lease_token"], *extra)
    await targets.coroutine()
    schedule = ScheduleInput(name="local", goal="local", agent_version_id=version,
                             starts_at=datetime.now(UTC) + timedelta(days=1))
    await draft.coroutine(schedule, SimpleNamespace(tool_call_id="local-call"))
    return await WorkWorkflows(run, "http://localhost:3100", *extra).tool().coroutine(str(version), "local")


@pytest.mark.parametrize("base", [None, "http://127.0.0.1:8139"])
def test_all_paths_use_configured_port_and_secure_client_options(monkeypatch, base):
    requests = []
    original = httpx.AsyncClient
    workflow_id = str(uuid4())

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/status"):
            return httpx.Response(200, json={"status": "SUCCEEDED", "output": "local success"})
        if path.endswith("/workflows"):
            return httpx.Response(202, json={"id": workflow_id})
        return httpx.Response(200, json={"items": [], "created": False})

    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    assert asyncio.run(all_callbacks(base)) == "local success"
    assert len(requests) == 6
    assert {r.url.port for r in requests} == {8100 if base is None else 8139}
    assert sum(r.url.path.endswith("/knowledge") for r in requests) == 2
    assert any(r.url.path.endswith("/automation-targets") for r in requests)
    assert any(r.url.path.endswith("/automation-drafts") for r in requests)


@pytest.mark.parametrize("kind", ["ask", "work", "automation", "workflow"])
def test_redirect_does_not_forward_lease(monkeypatch, kind):
    requests = []
    original = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://127.0.0.1:9999/steal"}, json={"error": {}})

    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "AsyncClient", factory)

    async def exercise():
        owner, version = uuid4(), uuid4()
        run = {"id": uuid4(), "owner_id": owner, "lease_token": uuid4(),
               "agent_snapshot": {"workflow_version_ids": [str(version)]}}
        db = SimpleNamespace(fetch=AsyncMock(return_value=[{}]))
        base = "http://127.0.0.1:8139"
        if kind == "automation":
            assert not (await automation_tools("local", run["id"], run["lease_token"], base)[0].coroutine())["created"]
        else:
            with pytest.raises((APIError, RuntimeError)):
                if kind == "workflow":
                    await WorkWorkflows(run, "local", base).tool().coroutine(str(version), "local")
                else:
                    cls = AskKnowledge if kind == "ask" else WorkKnowledge
                    await cls(db, run, "local", base).search(uuid4(), owner, "local")
    asyncio.run(exercise())
    assert len(requests) == 1 and requests[0].url.port == 8139


def test_two_real_local_sites_accept_own_leases_and_reject_cross_site_leases():
    counts = [0, 0]
    servers, threads, tokens = [], [], [uuid4(), uuid4()]

    def handler(site):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                valid = body["token"] == str(tokens[site])
                counts[site] += 1
                status, result = (200, {"items": [], "created": False}) if valid else (403, {"error": {}})
                if valid and self.path.endswith("/workflows"):
                    status, result = 202, {"id": str(uuid4())}
                if valid and self.path.endswith("/status"):
                    result = {"status": "SUCCEEDED", "output": "site-" + str(site)}
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
        return Handler

    try:
        for site in range(2):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler(site))
            assert server.server_port != 8100
            servers.append(server)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        bases = [f"http://127.0.0.1:{s.server_port}" for s in servers]
        for site in range(2):
            assert asyncio.run(all_callbacks(bases[site], tokens[site])) == "site-" + str(site)
            with pytest.raises(APIError, match="知识检索服务暂不可用"):
                asyncio.run(all_callbacks(bases[site], tokens[1 - site]))
        assert counts == [7, 7]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(5)
