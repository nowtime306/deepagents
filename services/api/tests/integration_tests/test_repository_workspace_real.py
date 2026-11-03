"""Real native Git, dirty/deleted files and bundles survive replacement task containers."""

import asyncio
import base64
import os
import shlex
from pathlib import Path
from uuid import uuid4

import pytest
from opensandbox.config import ConnectionConfig
from psycopg.types.json import Jsonb
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.durable_sandboxes import DurableSandboxes
from app.repository_provider import object_sha
from app.work_artifacts import WorkArtifacts
from app.work_backend import WorkBackend
from app.work_queue import WorkQueue


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires actual Coding sandbox")
def test_native_git_commit_dirty_delete_ignore_bundle_and_process_quiesce(client, tmp_path):
    connection, app, run = prepare(client)
    identifier = uuid4()
    files = {"main.py": b"print('base')\n", ".gitignore": b"node_modules/\n", "delete.txt": b"delete me\n"}
    source = {"sha": "a" * 40, "slug": "example/project", "ref": "main", "subdirectory": "", "files": [
        {"path": name, "mode": "100644", "sha": object_sha(data), "data": base64.b64encode(data).decode()}
        for name, data in files.items()]}
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (run["owner_id"],))
    connection.portal.call(app.state.db.execute, "INSERT INTO repositories(id,owner_id,name,slug,ref) "
        "VALUES(%s,%s,'native-test','example/project','main')", (identifier, run["owner_id"]))
    connection.portal.call(app.state.db.execute, "INSERT INTO work_run_repositories VALUES(%s,%s,1,%s,%s,now())",
                            (run["id"], identifier, Jsonb(source), "owner-agent/" + str(run["id"])))
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)

    async def exercise():
        service, queue = DurableSandboxes(app.state.db, config), WorkQueue(app.state.db)
        artifacts = WorkArtifacts(app.state.db, tmp_path)
        session = sandbox = None

        async def guard():
            if not await queue.renew(run["id"], run["lease_token"]):
                raise RuntimeError("Lease lost")

        async def pulse():
            while True:
                await guard()
                await asyncio.sleep(10)

        heartbeat = asyncio.create_task(pulse())
        try:
            created = await service.create(run["id"], run["lease_token"], "coding")
            assert created is not None
            session, sandbox = created
            backend = WorkBackend(sandbox, guard, repository_paths=list(files))
            await backend.initialize()
            await artifacts.restore(run, backend)
            result = await backend.aexecute("cd /workspace/repo && printf 'committed' > added.txt "
                "&& git add --all && git -c core.hooksPath=/dev/null commit -m local-change "
                "&& rm added.txt delete.txt && printf 'dirty' > main.py && chmod +x main.py "
                "&& printf 'staged' > 暂存.txt && git add 暂存.txt && mkdir node_modules "
                "&& printf 'ignored' > node_modules/cache && mkdir -p .git/info cache "
                "&& printf 'cache/\\n' > .git/info/exclude && printf 'ignored' > cache/entry "
                "&& mkdir /workspace/outputs "
                "&& printf 'test evidence' > /workspace/outputs/test.txt")
            assert result.exit_code == 0, result.output
            # Detached task child must not keep writing during export. The private/root RPC survives.
            spawn = "import subprocess;subprocess.Popen(['sleep','60'],start_new_session=True,stdout=-3,stderr=-3)"
            assert (await backend.aexecute("python3 -c " + shlex.quote(spawn))).exit_code == 0
            # A task-controlled fsmonitor/config include must never execute in trusted Git snapshots.
            poison = await backend.aexecute("cd /workspace/repo && printf 'invalid config' > /workspace/poison.conf "
                "&& git config core.fsmonitor 'touch /workspace/unapproved-hook' "
                "&& git config include.path /workspace/poison.conf")
            assert poison.exit_code == 0
            snapshot = await artifacts.capture(run, backend)
            assert (await backend.adownload_files(["/unapproved-hook"]))[0].content is None
            paths = {row["path"] for row in await app.state.db.fetch(
                "SELECT path FROM work_artifacts WHERE snapshot_id=%s", (snapshot,))}
            assert "/repo/暂存.txt" in paths and "/repo/main.py" in paths and "/outputs/test.txt" in paths
            assert not any(".git/" in p or "node_modules" in p or "/repo/cache/" in p for p in paths)
            assert await service.release(session["id"])
            await sandbox.close()
            session = sandbox = None
            created = await service.create(run["id"], run["lease_token"], "coding")
            assert created is not None
            session, sandbox = created
            backend = WorkBackend(sandbox, guard, repository_paths=list(files))
            await backend.initialize()
            await artifacts.restore(run, backend)
            restored = await backend.aexecute("cd /workspace/repo && test ! -e added.txt && test ! -e delete.txt "
                "&& test ! -e node_modules/cache && test -x main.py && test \"$(cat main.py)\" = dirty "
                "&& test \"$(cat 暂存.txt)\" = staged && git log --format=%s -1 "
                "&& git branch --show-current && cat /workspace/outputs/test.txt")
            assert restored.exit_code == 0, restored.output
            assert "local-change" in restored.output and str(run["id"]) in restored.output
            assert "test evidence" in restored.output
            branches = await backend.aexecute("cd /workspace/repo && git branch --format='%(refname:short)'")
            assert branches.output.strip() == "owner-agent/" + str(run["id"])
            exclude = await backend.aexecute("cat /workspace/repo/.git/info/exclude")
            assert exclude.output.strip() == "cache/"
            config_text = await backend.aexecute("cat /workspace/repo/.git/config")
            assert "poison.conf" not in config_text.output and "fsmonitor" not in config_text.output
            # A second snapshot must preserve the committed-then-deleted file tombstone too.
            await artifacts.capture(run, backend)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if session:
                assert await service.release(session["id"])
            if sandbox:
                await sandbox.close()
            await service.close()

    try:
        connection.portal.call(exercise)
    finally:
        cleanup(connection, app, run)
        connection.portal.call(app.state.db.execute, "DELETE FROM repositories WHERE id=%s", (identifier,))


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires actual Coding sandbox")
def test_frozen_repository_fixed_workflow_code_and_approval_restore(client):
    from test_api import ORIGIN
    from test_repositories import create as create_repo
    from test_repositories import grant
    from test_workflow_composition import create, definition
    from test_workflow_tasks import enable

    from app.work_engine import WorkEngine

    connection, app = client
    grant(connection, app)
    enable(connection, app)
    repository = create_repo(connection, token="only-server-test-credential").json()["id"]
    code = ("from pathlib import Path\nPath('/workspace/repo/代码.py').write_text(\"print('updated')\\n\")\n"
            "print('done')")
    identifier = create(connection, {**definition("code", code=code, timeout=5), "execution": "work"})
    response = connection.post(f"/api/v1/workflows/{identifier}/work-runs", headers=ORIGIN,
        json={"text": "input", "revision": 1, "repository_id": repository})
    assert response.status_code == 202, response.text
    root = response.json()
    frozen = connection.portal.call(app.state.db.fetch,
        "SELECT source FROM work_run_repositories WHERE run_id=%s", (root["run_id"],))[0]["source"]
    assert "only-server-test-credential" not in str(frozen)
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)
    service = DurableSandboxes(app.state.db, config)
    artifacts = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")

    def tick():
        engine = WorkEngine(app.state.db, app.state.models, service, artifacts, app.state.runtime.saver,
                           app.state.runtime.model_factory, capabilities_factory=lambda _: app.state.capabilities)
        assert connection.portal.call(engine.events.tick)
        return connection.get(f"/api/v1/work-runs/{root['run_id']}").json()

    try:
        state = tick()
        assert state["status"] == "WAITING_APPROVAL", state
        approvals = connection.get(f"/api/v1/work-runs/{root['run_id']}/approvals").json()["items"]
        approval = next(a for a in approvals if not a["decision"])
        assert connection.post(f"/api/v1/work-approvals/{approval['id']}/decisions", headers=ORIGIN,
            json={"digest": approval["digest"], "decision": "approve"}).status_code == 200
        state = tick()
        assert state["status"] == "COMPLETED", state
        assert state["result"]["text"].strip() == "done" and state["model_calls"] == 0
        review = connection.get(f"/api/v1/work-runs/{root['run_id']}/repository").json()["repository"]
        assert review["changes"][0]["path"] == "代码.py" and "updated" in review["changes"][0]["diff"]
        assert state["result"]["verification"]["successful_code_nodes"] == 1
    finally:
        sessions = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_sandbox_sessions WHERE run_id=%s AND state<>'RELEASED'", (root["run_id"],))
        for session in sessions:
            assert connection.portal.call(service.release, session["id"])
        connection.portal.call(service.close)
        files = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_artifacts WHERE run_id=%s UNION ALL SELECT g.id FROM work_git_snapshots g "
            "JOIN work_snapshots s ON g.snapshot_id=s.id WHERE s.run_id=%s", (root["run_id"], root["run_id"]))
        for file in files:
            artifacts.path(file["id"]).unlink(missing_ok=True)
        connection.portal.call(app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s",
                               (root["run_id"],))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (root["run_id"],))
        connection.delete(f"/api/v1/work-tasks/{root['task_id']}", headers=ORIGIN)
        connection.delete(f"/api/v1/workflows/{identifier}", headers=ORIGIN)
        connection.delete(f"/api/v1/repositories/{repository}", headers=ORIGIN)
