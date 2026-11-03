"""Retained workspace rescue uses the real manager without replaying model or commands."""

import asyncio
import os
from pathlib import Path

import pytest
from opensandbox.config import ConnectionConfig
from test_api import ORIGIN
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.durable_sandboxes import DurableSandboxes
from app.work_artifacts import WorkArtifacts
from app.work_backend import WorkBackend
from app.work_queue import WorkQueue
from app.work_recovery import WorkRecovery


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local real sandbox")
def test_rescue_stops_stale_workspace_and_persists_files(client):
    connection, app, run = prepare(client)
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)
    artifacts = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")
    service = DurableSandboxes(app.state.db, config)
    session = None

    async def setup():
        nonlocal session
        for _ in range(10):
            created = await service.create(run["id"], run["lease_token"], "coding")
            if created is not None:
                session, sandbox = created
                break
            assert await WorkQueue(app.state.db).renew(run["id"], run["lease_token"])
            await asyncio.sleep(5)
        else:
            pytest.fail("Actual host did not admit the isolated rescue fixture within 50 seconds")

        async def guard():
            assert await WorkQueue(app.state.db).renew(run["id"], run["lease_token"])

        backend = WorkBackend(sandbox, guard)
        try:
            await backend.initialize()
            assert not (await backend.awrite("/rescued.txt", "保留工作区唯一文件")).error
            await app.state.db.execute(
                "UPDATE work_sandbox_sessions SET retention_required=true WHERE id=%s", (session["id"],)
            )
            await app.state.db.execute(
                "UPDATE work_queue_runs SET status='NEEDS_REVIEW',lease_token=NULL,lease_until=NULL WHERE id=%s",
                (run["id"],),
            )
        finally:
            await sandbox.close()

    try:
        connection.portal.call(setup)
        url = f"/api/v1/work-runs/{run['id']}/recovery"
        assert connection.post(url, headers=ORIGIN).status_code == 202
        # The real endpoint sets a 90-second safety window; fast-forward only this isolated fixture.
        connection.portal.call(
            app.state.db.execute,
            "UPDATE work_recovery_jobs SET not_before=now()-interval '1 second' WHERE run_id=%s",
            (run["id"],),
        )
        recovery = WorkRecovery(app.state.db, service, artifacts)
        assert connection.portal.call(recovery.tick)
        state = connection.get(url).json()
        assert state["job"]["status"] == "COMPLETED"
        assert all(row["state"] == "RELEASED" for row in state["sessions"])
        files = connection.get(f"/api/v1/work-runs/{run['id']}/artifacts").json()["items"]
        assert len(files) == 1
        assert (
            connection.get(f"/api/v1/work-artifacts/{files[0]['id']}/download").content.decode() == "保留工作区唯一文件"
        )
        assert connection.get(f"/api/v1/work-runs/{run['id']}").json()["status"] == "NEEDS_REVIEW"
        assert connection.get(f"/api/v1/work-runs/{run['id']}/operations").json()["items"] == []
    finally:
        if session:
            connection.portal.call(service.release, session["id"])
        connection.portal.call(service.close)
        files = connection.portal.call(
            app.state.db.fetch, "SELECT id FROM work_artifacts WHERE run_id=%s", (run["id"],)
        )
        for file in files:
            artifacts.path(file["id"]).unlink(missing_ok=True)
        records = connection.portal.call(
            app.state.db.fetch, "SELECT state FROM work_sandbox_sessions WHERE run_id=%s", (run["id"],)
        )
        if all(row["state"] == "RELEASED" for row in records):
            cleanup(connection, app, run)
