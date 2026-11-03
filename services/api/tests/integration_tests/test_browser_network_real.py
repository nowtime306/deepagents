"""Opt-in public website browser acceptance with real sidecar network enforcement."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from opensandbox.config import ConnectionConfig
from psycopg.types.json import Jsonb
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.browser_tools import browser_tool
from app.durable_sandboxes import DurableSandboxes
from app.operation_journal import OperationJournal, receipts
from app.work_backend import WorkBackend
from app.work_network import DENIED
from app.work_queue import WorkQueue


@pytest.mark.skipif(
    os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local sandbox and public example.com access"
)
def test_browser_reads_allowed_public_site_and_blocks_ungranted_hosts(client, monkeypatch):
    connection, app, run = prepare(client)
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_queue_runs SET environment='browser',network_domains=%s WHERE id=%s",
        (Jsonb(["example.com"]), run["id"]),
    )
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
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
            session, sandbox = await service.create(run["id"], run["lease_token"], "browser")
            policy = await sandbox.get_egress_policy()
            assert policy.default_action == "deny"
            assert set(DENIED) <= {rule.target for rule in policy.egress if rule.action == "deny"}
            backend = WorkBackend(sandbox, guard)
            await backend.initialize()
            script = """import socket,urllib.request,urllib.error
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
 page=browser.new_page(viewport={'width':1280,'height':720})
 response=page.goto('https://example.com',wait_until='domcontentloaded',timeout=20000)
 assert response.status==200, response.status
 assert page.title()=='Example Domain', page.title()
 page.screenshot(path='/workspace/website.png')
 print('public-browser-title:'+page.title())
 browser.close()
for method in ['GET','PATCH']:
 try:
  data=b'{"egress":[{"action":"allow","target":"www.iana.org"}]}' if method=='PATCH' else None
  request=urllib.request.Request('http://127.0.0.1:18080/policy',method=method,data=data,
    headers={'Content-Type':'application/json'})
  urllib.request.urlopen(request,timeout=3)
  raise AssertionError('sidecar control API accepted an unauthenticated workload request')
 except urllib.error.HTTPError as error:
  assert error.code in (401,403),error.code
  print('policy-control-authenticated:'+method)
try:
 socket.getaddrinfo('www.iana.org',443)
 raise AssertionError('ungranted DNS resolved')
except socket.gaierror:
 print('ungranted-domain-blocked')
for address in ['1.1.1.1','169.254.169.254']:
 try:
  socket.create_connection((address,443),timeout=2)
  raise AssertionError('ungranted destination connected')
 except (TimeoutError,OSError):
  print('destination-blocked:'+address)
"""
            assert not (await backend.awrite("/browse.py", script)).error
            result = await backend.aexecute("python3 browse.py", timeout=40)
            assert result.exit_code == 0, result.output
            assert "Example Domain" in result.output and "ungranted-domain-blocked" in result.output
            image = (await backend.adownload_files(["/website.png"]))[0]
            assert image.content.startswith(b"\x89PNG")
            (app.state.settings.data_dir / "browser-public-example.png").write_bytes(image.content)
            steps = [
                {"op": "open", "parameters": {"url": "https://example.com"}},
                {"op": "read", "parameters": {"selector": "body"}},
                {"op": "scroll", "parameters": {"pixels": 300}},
                {"op": "new_tab", "parameters": {"url": "https://example.com"}},
                {"op": "list_tabs", "parameters": {}},
                {"op": "select_tab", "parameters": {"index": 0}},
                {"op": "close_tab", "parameters": {}},
                {"op": "screenshot", "parameters": {"path": "/browser-tool.png"}},
            ]
            approval = {"id": uuid4(), "run_id": run["id"]}
            actions = [{"name": "browser_task", "args": {"steps": steps}}]
            payload = {"action_requests": actions}
            async with app.state.db.pool.connection() as c:
                await c.execute(
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
                await receipts(c, approval, actions)
            backend.journal = OperationJournal(app.state.db, run)
            tool = browser_tool(backend, {**run, "network_domains": ["example.com"]})
            result = json.loads(await tool.ainvoke({"steps": steps}))
            assert result["ok"] and result["steps"][1]["title"] == "Example Domain" and result["steps"][1]["text"]
            assert len(result["steps"][4]["tabs"]) == 2
            screenshot = (await backend.adownload_files(["/browser-tool.png"]))[0]
            assert screenshot.content.startswith(b"\x89PNG")
            with pytest.raises(PermissionError):
                await tool.ainvoke({"steps": steps})
            # Controlled responses exercise Chromium without submitting external data.
            from app.browser_tools import SCRIPT

            html = """
<input id="name"><button id="show">show</button><p id="value"></p>
<input id="file" type="file"><p id="uploaded"></p>
<a id="download" href="https://example.com/owner-download">download</a>
<script>
document.getElementById("show").onclick = () => {
 document.getElementById("value").textContent = document.getElementById("name").value;
};
document.getElementById("file").onchange = function() {
 document.getElementById("uploaded").textContent = this.files[0].name;
};
</script>
"""
            fixture = (
                'context.route("https://example.com/owner-browser-fixture", '
                'lambda route: route.fulfill(status=200, content_type="text/html", body='
                + repr(html)
                + "))\n"
                + '            context.route("https://example.com/owner-download", '
                "lambda route: route.fulfill(status=200, "
                'headers={"Content-Disposition":"attachment; filename=fixture.txt",'
                '"Content-Type":"text/plain"}, body="browser-download-fixture"))\n'
                + "            page = context.new_page()"
            )
            monkeypatch.setattr("app.browser_tools.SCRIPT", SCRIPT.replace("page = context.new_page()", fixture, 1))
            assert not (await backend.awrite("/upload.txt", "local-upload-fixture")).error
            steps = [
                {"op": "open", "parameters": {"url": "https://example.com/owner-browser-fixture"}},
                {"op": "fill", "parameters": {"selector": "#name", "text": "浏览器输入验证"}},
                {"op": "click", "parameters": {"selector": "#show"}},
                {"op": "read", "parameters": {"selector": "#value"}},
                {"op": "upload", "parameters": {"selector": "#file", "path": "/upload.txt"}},
                {"op": "read", "parameters": {"selector": "#uploaded"}},
                {"op": "download", "parameters": {"selector": "#download", "path": "/download.txt"}},
            ]
            approval = {"id": uuid4(), "run_id": run["id"]}
            actions = [{"name": "browser_task", "args": {"steps": steps}}]
            payload = {"action_requests": actions}
            async with app.state.db.pool.connection() as c:
                await c.execute(
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
                await receipts(c, approval, actions)
            result = json.loads(await tool.ainvoke({"steps": steps}))
            assert result["ok"], result
            assert result["steps"][3]["text"] == "浏览器输入验证"
            assert result["steps"][5]["text"] == "upload.txt"
            downloaded = (await backend.adownload_files(["/download.txt"]))[0]
            assert downloaded.content == b"browser-download-fixture"

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
        records = connection.portal.call(
            app.state.db.fetch, "SELECT state FROM work_sandbox_sessions WHERE run_id=%s", (run["id"],)
        )
        if all(row["state"] == "RELEASED" for row in records):
            cleanup(connection, app, run)
