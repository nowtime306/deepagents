"""Approved browser groups, encrypted task state and fenced persistent sandbox reuse."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from opensandbox.config import ConnectionConfig
from psycopg.types.json import Jsonb
from test_api import client as client
from test_browser_daemon_real import fixture_script
from test_durable_sandboxes import cleanup, prepare

from app.browser_bridge import BrowserBridge
from app.browser_tools import browser_tool
from app.browser_workbench import BrowserWorkbench
from app.durable_sandboxes import DurableSandboxes
from app.operation_journal import OperationJournal, receipts
from app.work_artifacts import WorkArtifacts
from app.work_backend import WorkBackend
from app.work_queue import WorkQueue


async def approve(database, run, steps):
    approval = {"id": uuid4(), "run_id": run["id"]}
    actions = [{"name": "browser_task", "args": {"steps": steps}}]
    payload = {"action_requests": actions}
    async with database.pool.connection() as connection:
        await connection.execute(
            "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,"
            "expires_at,decision,consumed_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,now()+interval '30 minutes','approve',now())",
            (
                approval["id"],
                run["id"],
                run["owner_id"],
                str(uuid4()),
                Jsonb(payload),
                hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            ),
        )
        await receipts(connection, approval, actions)


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local Chromium and PostgreSQL")
def test_private_state_and_same_browser_after_approval_pause(client, monkeypatch):
    connection, app, run = prepare(client)
    run["environment"], run["network_domains"] = "browser", ["example.com"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_queue_runs SET environment='browser',network_domains=%s WHERE id=%s",
        (Jsonb(run["network_domains"]), run["id"]),
    )
    html = """<title>Private fixture</title><input id="value"><input id="password" type="password"
oninput="document.getElementById('private-status').textContent=this.value.length?'received':'empty'">
<button id="apply" onclick="
document.getElementById('out').textContent=document.getElementById('value').value;
document.cookie='owner_fixture=cookie-private;path=/;Secure';localStorage.setItem('private','local-private');
sessionStorage.setItem('private','session-private');history.replaceState(null,'','?access=private-query#hidden')">Apply</button>
<p id="out">initial</p><p id="private-status">empty</p>"""
    monkeypatch.setattr("app.browser_transport.DAEMON", fixture_script(html))
    monkeypatch.setattr("app.work_network.public_addresses", lambda _: ["93.184.215.14"])
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)

    async def exercise():
        service, queue = DurableSandboxes(app.state.db, config), WorkQueue(app.state.db)
        session = sandbox = None

        def guard_for(current):
            async def guard():
                if not await queue.renew(current["id"], current["lease_token"]):
                    raise PermissionError("Stale browser execution")

            return guard

        try:
            session, sandbox = await service.create(run["id"], run["lease_token"], "browser")
            backend = WorkBackend(sandbox, guard_for(run), journal=OperationJournal(app.state.db, run))
            await backend.initialize()
            bridge = BrowserBridge(app.state.db, app.state.models.vault, backend, run, session)
            tool = browser_tool(backend, run, bridge=bridge)
            steps = [
                {"op": "open", "parameters": {"url": "https://example.com/owner-fixture"}},
                {"op": "fill", "parameters": {"selector": "#value", "text": "same DOM after pause"}},
                {"op": "click", "parameters": {"selector": "#apply"}},
            ]
            with pytest.raises(PermissionError):
                await tool.ainvoke({"steps": steps})
            assert not await app.state.db.fetch(
                "SELECT 1 FROM work_browser_transports WHERE session_id=%s", (session["id"],)
            )
            await approve(app.state.db, run, steps)
            result = await tool.ainvoke({"steps": steps})
            assert json.loads(result)["ok"] and "private-query" not in result
            stored = (
                await app.state.db.fetch("SELECT * FROM work_browser_states WHERE task_id=%s", (run["task_id"],))
            )[0]
            assert "cookie-private" not in stored["state_ciphertext"]
            assert "cookie-private" in app.state.models.vault.decrypt(stored["state_ciphertext"]).get_secret_value()
            original_sandbox = sandbox.id
            await app.state.db.execute(
                "UPDATE work_sandbox_sessions SET browser_persistent=true,retention_required=true WHERE id=%s",
                (session["id"],),
            )
            await app.state.db.execute(
                "UPDATE work_queue_runs SET status='PAUSED',lease_token=NULL,lease_until=NULL WHERE id=%s",
                (run["id"],),
            )
            import httpx

            workbench = BrowserWorkbench(
                app.state.db,
                app.state.models.vault,
                config,
                WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts"),
            )
            app.state.browser_workbench = workbench
            app.state.settings.work_enabled = True
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    cookies=dict(connection.cookies.items()),
                    headers={"Origin": "http://localhost:3100"},
                ) as web:
                    response = await web.get(f"/api/v1/work-runs/{run['id']}/browser/frame")
                    assert response.status_code == 200 and response.content.startswith(b"\xff\xd8\xff")
                    response = await web.post(
                        f"/api/v1/work-runs/{run['id']}/browser/controls", json={"request_id": str(uuid4())}
                    )
                    assert response.status_code == 202, response.text
                    control = response.json()["id"]
                    assert await queue.claim() is None
                    assert (await web.post(f"/api/v1/work-runs/{run['id']}/resume")).status_code == 409
                    for kind, parameters in [
                        ("pointer", {"x": 240, "y": 15}),
                        ("type", {"text": "private-manual-input"}),
                    ]:
                        response = await web.post(
                            f"/api/v1/work-browser-controls/{control}/actions",
                            json={"request_id": str(uuid4()), "kind": kind, "parameters": parameters},
                        )
                        assert response.status_code == 200, response.text
                    await web.post(f"/api/v1/work-browser-controls/{control}/close", json={})
                    assert await workbench.tick()
                    assert (await workbench.control(control, run["owner_id"]))["status"] == "CLOSED"
                    rows = await app.state.db.fetch(
                        "SELECT row_to_json(a)::text AS data FROM work_browser_actions a WHERE control_id=%s",
                        (control,),
                    )
                    assert rows and all("private-manual-input" not in row["data"] for row in rows)
                    response = await web.post(f"/api/v1/work-runs/{run['id']}/resume")
                    assert response.status_code == 202, response.text
            finally:
                await workbench.close()
                app.state.browser_workbench = None
            await sandbox.close()
            resumed = await queue.claim()
            assert resumed["id"] == run["id"] and resumed["lease_token"] != run["lease_token"]
            session, sandbox = await service.create(resumed["id"], resumed["lease_token"], "browser")
            assert session["reused"] and sandbox.id == original_sandbox
            with pytest.raises(PermissionError):
                await bridge.transport.request({"op": "status"})
            backend = WorkBackend(sandbox, guard_for(resumed), journal=OperationJournal(app.state.db, resumed))
            bridge = BrowserBridge(app.state.db, app.state.models.vault, backend, resumed, session)
            tool = browser_tool(backend, resumed, bridge=bridge)
            steps = [
                {"op": "read", "parameters": {"selector": "#out"}},
                {"op": "screenshot", "parameters": {"path": "/browser.png"}},
            ]
            await approve(app.state.db, resumed, steps)
            result = json.loads(await tool.ainvoke({"steps": steps}))
            assert result["ok"] and result["steps"][0]["text"] == "same DOM after pause"
            checked = await bridge.transport.request(
                {"op": "read", "parameters": {"selector": "#private-status"}, "request_id": uuid4().hex}
            )
            assert checked["result"]["text"] == "received"
            assert (await backend.adownload_files(["/browser.png"]))[0].content.startswith(b"\x89PNG")
            journal = await app.state.db.fetch(
                "SELECT args,output FROM operation_executions WHERE run_id=%s", (run["id"],)
            )
            assert all(
                not any(secret in str(row) for secret in ["cookie-private", "session-private", "private-query"])
                for row in journal
            )
        finally:
            if session:
                assert await service.release(session["id"])
            if sandbox:
                await sandbox.close()
            await service.close()

    try:
        connection.portal.call(exercise)
    finally:
        connection.portal.call(app.state.db.execute, "DELETE FROM work_browser_controls WHERE run_id=%s", (run["id"],))
        cleanup(connection, app, run)
