"""Actual isolated Chromium: persistent DOM, private login state and attested transport."""

import asyncio
import os
import shlex
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from opensandbox.config import ConnectionConfig
from opensandbox.models.execd import RunCommandOpts

from app.browser_transport import DAEMON, BrowserTransport
from app.errors import APIError
from app.sandbox_service import SandboxService


def fixture_script(html):
    return DAEMON.replace(
        'self.context.on("page", self.new_page)',
        'self.context.on("page", self.new_page)\n'
        + '        self.context.route("https://example.com/owner-fixture*", lambda route: '
        + 'route.fulfill(status=200, content_type="text/html", body='
        + repr(html)
        + "))",
    )


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires the local browser sandbox")
async def test_actual_daemon_keeps_state_and_hides_private_material(monkeypatch):
    html = """<title>Private browser fixture</title><input id="value"><input id="password" type="password">
<button id="apply" onclick="document.getElementById('out').textContent=document.getElementById('value').value;
document.cookie='owner_fixture=cookie-private;path=/;Secure';localStorage.setItem('private','local-private');
sessionStorage.setItem('private','session-private')">Apply</button><p id="out">initial</p>"""
    script = fixture_script(html)
    monkeypatch.setattr("app.browser_transport.DAEMON", script)
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    service = SandboxService(ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True))
    lease = None

    async def guard():
        pass

    try:
        lease = await service.create("browser")
        transport = BrowserTransport(lease.sandbox, guard)
        await transport.start(["example.com"])

        async def perform(op, **parameters):
            result = await transport.request({"op": op, "parameters": parameters, "request_id": uuid4().hex})
            assert result["ok"], result
            return result["result"]

        await perform("open", url="https://example.com/owner-fixture")
        await perform("fill", selector="#value", text="persistent across two groups")
        await perform("fill", selector="#password", text="private-password-marker")
        await perform("click", selector="#apply")
        assert (await perform("read", selector="#out"))["text"] == "persistent across two groups"
        state = await transport.request({"op": "export"})
        assert state["storage"]["cookies"][0]["value"] == "cookie-private"
        assert any(row["value"] == "local-private" for row in state["storage"]["origins"][0]["localStorage"])
        assert state["session"]["https://example.com"]["private"] == "session-private"
        assert "private-password-marker" not in str(state)
        assert (await transport.request(frame=True)).startswith(b"\xff\xd8\xff")
        private_attempt = (
            "from pathlib import Path\ntry:\n Path('/var/lib/owner-agent-browser/token').read_bytes()\n"
            " raise AssertionError('private state readable')\nexcept PermissionError:\n print('private-state-denied')"
        )
        denied = await lease.sandbox.commands.run(
            shlex.join(["python3", "-c", private_attempt]),
            opts=RunCommandOpts(uid=1000, gid=1000, timeout=timedelta(seconds=5)),
        )
        assert denied.exit_code == 0 and "private-state-denied" in denied.text
        impostor = BrowserTransport(lease.sandbox, guard, "0" * 64)
        with pytest.raises(APIError) as error:
            await impostor.request({"op": "restore", "state": {}})
        assert error.value.code == "BROWSER_IDENTITY_FAILED"
        await transport.request({"op": "restore", "state": state})
        restored = await transport.request({"op": "export"})
        assert restored["storage"]["cookies"] == state["storage"]["cookies"]
        assert restored["session"] == state["session"]
        # Replay a completed id returns its receipt instead of applying a different action.
        identifier = uuid4().hex
        first = await transport.request(
            {"op": "fill", "parameters": {"selector": "#value", "text": "first"}, "request_id": identifier}
        )
        assert first["ok"]
        with pytest.raises(APIError):
            await transport.request(
                {"op": "fill", "parameters": {"selector": "#value", "text": "changed"}, "request_id": identifier}
            )
        assert (await perform("read", selector="body"))["url"] == "https://example.com/owner-fixture"
        assert not (
            await transport.request(
                {"op": "open", "parameters": {"url": "https://ungranted.invalid"}, "request_id": uuid4().hex}
            )
        )["ok"]
    finally:
        if lease:
            await asyncio.shield(lease.destroy())
            await lease.sandbox.close()
