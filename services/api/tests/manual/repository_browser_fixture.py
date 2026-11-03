"""Named zero-model native Git UI/restore fixture; never contacts a real GitHub repository."""

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from time import time_ns
from uuid import UUID

import httpx
from psycopg.types.json import Jsonb

from app.config import Settings
from app.durable_sandboxes import DurableSandboxes
from app.models import CredentialVault
from app.repository_provider import object_sha
from app.storage import Database
from app.work_artifacts import WorkArtifacts

META = Path(__file__).parents[2] / ".local/repository-browser-fixture.json"
CODE = ("from pathlib import Path\nimport subprocess\np=Path('/workspace/repo')\n"
        "(p/'added-then-deleted.txt').write_text('local commit')\n"
        "subprocess.run(['git','add','--all'],cwd=p,check=True)\n"
        "subprocess.run(['git','-c','core.hooksPath=/dev/null','commit','-m','local fixture commit'],"
        "cwd=p,check=True)\n"
        "(p/'added-then-deleted.txt').unlink()\n(p/'delete.txt').unlink()\n"
        "(p/'main.py').write_text(\"print('git-workspace-ok')\\n\")\n"
        "Path('/workspace/git-fixture-counter.txt').write_text('1')\nprint('git-workspace-ok')")


async def wait(client, run, expected):
    for _ in range(120):
        row = (await client.get(f"/work-runs/{run}")).json()
        assert row.get("model_calls") == 0, "This fixture must not invoke any model"
        if row["status"] == expected:
            return row
        assert row["status"] in {"PENDING", "RUNNING"}, row
        await asyncio.sleep(1)
    raise AssertionError("Native fixture did not reach " + expected)


async def create(client, db):
    assert not META.exists(), "Check or remove the previous recorded fixture first"
    for _ in range(60):
        if await db.fetch("SELECT 1 FROM work_worker_instances WHERE heartbeat_at>now()-interval '15 seconds'"):
            break
        await asyncio.sleep(0.5)
    else:
        raise RuntimeError("Native fixture requires a ready Work consumer before creating records")
    owner = (await client.get("/auth/me")).json()["id"]
    name = "本机Git恢复验收-" + str(time_ns())
    result = await client.post("/repositories", json={"name": name, "slug": "example/offline-fixture", "ref": "main",
        "token": "fake-private-native-git-fixture-only"})
    assert result.status_code == 201, result.text
    data = {"name": name, "repository": result.json()["id"], "owner": owner,
            "code_digest": hashlib.sha256(CODE.encode()).hexdigest()}
    META.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    model = (await client.get("/models")).json()["items"][0]["id"]
    nodes = [{"id": "start", "kind": "start", "input": "input", "parameters": {}},
             {"id": "code", "kind": "code", "input": "start", "parameters": {"code": CODE, "timeout": 15}},
             {"id": "end", "kind": "end", "input": "code", "parameters": {}}]
    result = await client.post("/workflows", json={"name": name, "model_id": model, "definition": {
        "execution": "work", "nodes": nodes, "edges": [{"source": "start", "target": "code"},
                                                       {"source": "code", "target": "end"}],
        "node_budget": 5, "model_calls": 1, "token_limit": 1000}})
    assert result.status_code == 201, result.text
    data["workflow"] = result.json()["id"]
    META.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # A first code approval creates no effects. Only then attach an explicitly labeled offline source;
    # real GitHub admission is separately covered by the isolated API/object-protocol tests.
    result = await client.post(f"/workflows/{data['workflow']}/work-runs",
                               json={"revision": 1, "text": "local fixture"})
    assert result.status_code == 202, result.text
    data.update(run=result.json()["run_id"], task=result.json()["task_id"])
    META.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    await wait(client, data["run"], "WAITING_APPROVAL")
    files = {"main.py": b"print('baseline')\n", "delete.txt": b"delete fixture\n"}
    source = {"sha": "a" * 40, "tree": "b" * 40, "slug": "example/offline-fixture", "ref": "main", "subdirectory": "",
              "files": [{"path": name, "mode": "100644", "sha": object_sha(content),
                         "data": base64.b64encode(content).decode()} for name, content in files.items()]}
    async with db.pool.connection() as connection:
        row = await (await connection.execute("SELECT owner_id,status,model_calls FROM work_queue_runs "
            "WHERE id=%s FOR UPDATE", (data["run"],))).fetchone()
        assert str(row["owner_id"]) == owner and row["status"] == "WAITING_APPROVAL" and row["model_calls"] == 0
        await connection.execute("INSERT INTO work_run_repositories(run_id,repository_id,revision,source,branch) "
            "VALUES(%s,%s,1,%s,%s)", (data["run"], data["repository"], Jsonb(source), "owner-agent/" + data["run"]))
    return {"prepared": True, "model_calls": 0, "name": name}


async def verify(client, db, data):
    run = await wait(client, data["run"], "COMPLETED")
    assert run["task_id"] == data["task"] and run["title"] == data["name"] + " · 流程任务"
    binding = await db.fetch("SELECT b.repository_id,w.workflow_id FROM work_run_repositories b "
        "JOIN work_run_workflows f ON f.run_id=b.run_id JOIN workflow_runs w ON w.id=f.workflow_run_id "
        "WHERE b.run_id=%s", (data["run"],))
    assert len(binding) == 1 and str(binding[0]["repository_id"]) == data["repository"]
    assert str(binding[0]["workflow_id"]) == data["workflow"]
    assert run["result"]["verification"]["successful_code_nodes"] == 1
    rows = await db.fetch("SELECT 1 FROM model_usage WHERE run_id=%s", (data["run"],))
    assert not rows
    review = (await client.get(f"/work-runs/{data['run']}/repository")).json()["repository"]
    assert {c["path"] for c in review["changes"]} == {"delete.txt", "main.py"}
    assert next(c for c in review["changes"] if c["path"] == "delete.txt")["kind"] == "deleted"
    assert "git-workspace-ok" in next(c for c in review["changes"] if c["path"] == "main.py")["diff"]
    artifacts = (await client.get(f"/work-runs/{data['run']}/artifacts")).json()["items"]
    counter = next(f for f in artifacts if f["path"] == "/git-fixture-counter.txt")
    assert (await client.get(f"/work-artifacts/{counter['id']}/download")).content == b"1"
    bundles = await db.fetch("SELECT g.* FROM work_git_snapshots g JOIN work_snapshots s ON s.id=g.snapshot_id "
                            "WHERE s.run_id=%s", (data["run"],))
    assert bundles and "added-then-deleted.txt" in bundles[-1]["metadata"]["deleted"]
    storage = WorkArtifacts(db, Settings.from_env().data_dir / "work-artifacts")
    for bundle in bundles:
        content = storage.path(bundle["id"]).read_bytes()
        assert len(content) == bundle["size"] and hashlib.sha256(content).hexdigest() == bundle["digest"]
    vault = CredentialVault(Settings.from_env().data_dir / "model.key", create=False)
    row = (await db.fetch("SELECT token_ciphertext FROM repositories WHERE id=%s", (data["repository"],)))[0]
    assert vault.decrypt(row["token_ciphertext"]).get_secret_value() == "fake-private-native-git-fixture-only"
    return {"completed_native_git": True, "code_executions": 1, "model_calls": 0, "bundles": len(bundles)}


async def execute(command):
    from opensandbox.config import ConnectionConfig
    from psycopg.conninfo import conninfo_to_dict

    settings = Settings.from_env()
    assert conninfo_to_dict(settings.database_url)["dbname"] == "owner_agent"
    values = dict(line.split("=", 1) for line in (META.parents[1] / ".env.admin").read_text().splitlines())
    db = Database(settings.database_url)
    await db.pool.open(wait=True)
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8100/api/v1", timeout=30,
                                     headers={"Origin": settings.web_origin}) as client:
            response = await client.post("/auth/login", json={"username": values["ADMIN_USERNAME"],
                                                             "password": values["ADMIN_PASSWORD"]})
            assert response.status_code == 200
            if command == "create":
                return await create(client, db)
            data = json.loads(META.read_text(encoding="utf-8"))
            row = await db.fetch("SELECT owner_id,name FROM repositories WHERE id=%s", (data["repository"],))
            assert row and str(row[0]["owner_id"]) == data["owner"] and row[0]["name"] == data["name"]
            assert data["name"].startswith("本机Git恢复验收-")
            if command == "check":
                return await verify(client, db, data)
            await verify(client, db, data)
            assert not await db.fetch("SELECT 1 FROM repository_actions WHERE run_id=%s", (data["run"],))
            key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
            sandboxes = DurableSandboxes(db, ConnectionConfig(
                domain="127.0.0.1:18180", api_key=key, use_server_proxy=True))
            try:
                sessions = await db.fetch("SELECT id FROM work_sandbox_sessions WHERE run_id=%s AND state<>'RELEASED'",
                                          (data["run"],))
                for session in sessions:
                    assert await sandboxes.release(session["id"])
            finally:
                await sandboxes.close()
            files = await db.fetch("SELECT id FROM work_artifacts WHERE run_id=%s UNION ALL SELECT g.id "
                "FROM work_git_snapshots g JOIN work_snapshots s ON s.id=g.snapshot_id WHERE s.run_id=%s",
                (data["run"], data["run"]))
            storage = WorkArtifacts(db, settings.data_dir / "work-artifacts")
            async with db.pool.connection() as connection:
                await connection.execute("DELETE FROM work_sandbox_sessions WHERE run_id=%s", (data["run"],))
                await connection.execute("DELETE FROM work_queue_runs WHERE id=%s AND owner_id=%s",
                                         (data["run"], data["owner"]))
            for file in files:
                storage.path(UUID(str(file["id"]))).unlink(missing_ok=True)
            for path in (f"/work-tasks/{data['task']}", f"/workflows/{data['workflow']}",
                         f"/repositories/{data['repository']}"):
                result = await client.delete(path)
                assert result.status_code == 200, result.text
            META.unlink()
            return {"removed_recorded_native_git_fixture": True}
    finally:
        await db.pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["create", "check", "cleanup"])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        print(json.dumps(runner.run(execute(parser.parse_args().command)), ensure_ascii=False))
