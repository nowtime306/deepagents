"""Named local-only browser UI/backup fixture; never calls a model or a real login page."""

import argparse
import asyncio
import json
from pathlib import Path
from time import time_ns
from uuid import UUID, uuid4

import httpx
from opensandbox.config import ConnectionConfig
from psycopg.types.json import Jsonb

from app import browser_transport
from app.browser_bridge import BrowserBridge
from app.browser_workbench import BrowserWorkbench
from app.config import Settings
from app.durable_sandboxes import DurableSandboxes
from app.models import CredentialVault
from app.storage import Database
from app.work_artifacts import WorkArtifacts
from app.work_backend import WorkBackend

PATH = Path(__file__).parents[2] / ".local/browser-ui-fixture.json"
HTML = """<!doctype html><meta charset="utf-8"><title>本机浏览器接管验收</title>
<style>body{font:24px sans-serif;margin:32px}input{display:block;width:500px;height:48px;font:24px sans-serif}
button{margin-top:20px;padding:10px;font:24px sans-serif}</style>
<h1>本机浏览器接管验收</h1><form onsubmit="event.preventDefault();
document.getElementById('out').textContent=document.getElementById('value').value;
document.cookie='owner_ui_fixture=private-fixture;path=/;Secure';localStorage.setItem('fixture','private-fixture')">
<label>测试输入<input id="value"></label><button>显示测试输入</button></form><p id="out">等待输入</p>"""


async def run(command):
    settings = Settings.from_env()
    from psycopg.conninfo import conninfo_to_dict

    assert conninfo_to_dict(settings.database_url)["dbname"] == "owner_agent"
    values = dict(
        line.split("=", 1) for line in (PATH.parents[1] / ".env.admin").read_text().splitlines() if "=" in line
    )
    database = Database(settings.database_url)
    await database.pool.open(wait=True)
    vault = CredentialVault(settings.data_dir / "model.key", create=False)
    manager_key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=manager_key, use_server_proxy=True)
    service = DurableSandboxes(database, config)
    artifacts = WorkArtifacts(database, settings.data_dir / "work-artifacts")
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8100/api/v1", headers={"Origin": "http://localhost:3100"}, timeout=60
        ) as client:
            response = await client.post(
                "/auth/login", json={"username": values["ADMIN_USERNAME"], "password": values["ADMIN_PASSWORD"]}
            )
            response.raise_for_status()
            owner = response.json()["id"]
            if command == "create":
                if PATH.exists():
                    raise ValueError("Recorded browser fixture already exists; verify it before cleanup")
                data = {"name": "浏览器接管验收-" + str(time_ns()), "task": None, "run": None}
                with PATH.open("x", encoding="utf-8") as output:
                    json.dump(data, output, ensure_ascii=False)
                response = await client.post(
                    "/work-tasks", json={"title": data["name"], "goal": "仅本机页面接管验收，无模型调用"}
                )
                response.raise_for_status()
                data["task"] = response.json()["id"]
                PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                model = (await client.get("/models")).json()["items"][0]["id"]
                identifier, token = uuid4(), uuid4()
                rows = await database.fetch(
                    "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,model_revision,"
                    "environment,network_domains,status,lease_token,lease_until) "
                    "SELECT %s,t.id,t.owner_id,t.title,t.goal,t.revision,m.id,m.revision,'browser',%s,"
                    "'RUNNING',%s,now()+interval '120 seconds' FROM work_tasks t CROSS JOIN model_configs m "
                    "WHERE t.id=%s AND t.owner_id=%s AND m.id=%s RETURNING *",
                    (identifier, Jsonb(["example.com"]), token, data["task"], owner, model),
                )
                current = rows[0]
                data["run"] = str(identifier)
                PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

                async def guard():
                    if not await database.fetch(
                        "SELECT 1 FROM work_queue_runs WHERE id=%s AND owner_id=%s AND status='RUNNING' "
                        "AND lease_token=%s AND lease_until>now()",
                        (identifier, owner, token),
                    ):
                        raise PermissionError("Fixture lease expired")

                browser_transport.DAEMON = browser_transport.DAEMON.replace(
                    'self.context.on("page", self.new_page)',
                    'self.context.on("page", self.new_page)\n'
                    + '        self.context.route("https://example.com/owner-fixture*", lambda route: '
                    + 'route.fulfill(status=200, content_type="text/html", body='
                    + repr(HTML)
                    + "))",
                )
                session, sandbox = await service.create(identifier, token, "browser")
                try:
                    backend = WorkBackend(sandbox, guard)
                    await backend.initialize()
                    assert not (await backend.aupload_files([("/browser-fixture.txt", data["name"].encode())]))[0].error
                    bridge = BrowserBridge(database, vault, backend, current, session)
                    await bridge.initialize()
                    result = await bridge.transport.request(
                        {
                            "op": "open",
                            "parameters": {"url": "https://example.com/owner-fixture"},
                            "request_id": uuid4().hex,
                        }
                    )
                    assert result["ok"]
                    await bridge.save()
                    await artifacts.capture(current, backend)
                    await database.execute(
                        "UPDATE work_sandbox_sessions SET browser_persistent=true,retention_required=true WHERE id=%s",
                        (session["id"],),
                    )
                    await database.execute(
                        "UPDATE work_queue_runs SET status='PAUSED',lease_token=NULL,lease_until=NULL WHERE id=%s",
                        (identifier,),
                    )
                finally:
                    await sandbox.close()
                print(json.dumps({"fixture_created": True, "model_calls": 0}))
            else:
                data = json.loads(PATH.read_text(encoding="utf-8"))
                rows = await database.fetch(
                    "SELECT r.* FROM work_queue_runs r JOIN work_tasks t ON t.id=r.task_id "
                    "WHERE r.id=%s AND r.task_id=%s AND r.owner_id=%s AND t.title=%s "
                    "AND t.title LIKE '浏览器接管验收-%%'",
                    (data["run"], data["task"], owner, data["name"]),
                )
                if len(rows) != 1:
                    raise ValueError("Recorded browser fixture identity mismatch")
                current = rows[0]
                if command == "check":
                    workbench = BrowserWorkbench(database, vault, config, artifacts)
                    try:
                        async with workbench.connect(current) as bridge:
                            result = await bridge.transport.request(
                                {
                                    "op": "read",
                                    "parameters": {"selector": "#out"},
                                    "request_id": uuid4().hex,
                                }
                            )
                        assert result["ok"] and result["result"]["text"] == "本机接管验证通过"
                        states = await database.fetch(
                            "SELECT state_ciphertext FROM work_browser_states WHERE task_id=%s", (current["task_id"],)
                        )
                        assert "private-fixture" in vault.decrypt(states[0]["state_ciphertext"]).get_secret_value()
                        assert "private-fixture" not in states[0]["state_ciphertext"]
                        assert not await database.fetch("SELECT 1 FROM model_usage WHERE run_id=%s", (current["id"],))
                        print(
                            json.dumps({"same_page_verified": True, "private_state_encrypted": True, "model_calls": 0})
                        )
                    finally:
                        await workbench.close()
                else:
                    sessions = await database.fetch(
                        "SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (current["id"],)
                    )
                    for item in sessions:
                        if not await service.release(item["id"]):
                            raise RuntimeError("Fixture environment release remains unconfirmed")
                    files = await database.fetch("SELECT id FROM work_artifacts WHERE run_id=%s", (current["id"],))
                    await database.execute("DELETE FROM work_browser_controls WHERE run_id=%s", (current["id"],))
                    await database.execute("DELETE FROM work_sandbox_sessions WHERE run_id=%s", (current["id"],))
                    await database.execute("DELETE FROM work_queue_runs WHERE id=%s", (current["id"],))
                    for item in files:
                        artifacts.path(item["id"]).unlink(missing_ok=True)
                    response = await client.delete(f"/work-tasks/{UUID(data['task'])}")
                    response.raise_for_status()
                    PATH.unlink()
                    print("Removed only the recorded browser fixture and its local files")
    finally:
        await service.close()
        await database.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "check", "cleanup"])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(run(parser.parse_args().command))
