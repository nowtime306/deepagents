"""Real isolated DB maintenance admission and coordinator recovery, without stopping production."""

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_api import DATABASE, migrate, run_async

from app.config import Settings
from app.errors import APIError
from app.main import create_app
from app.maintenance import request_slot
from app.manage import add_user
from app.operations_worker import OperationsWorker, next_due
from app.storage import Database


class Services:
    def __init__(self):
        self.calls = []

    async def snapshot(self):
        return [{"kind": "api", "slot": 0}, {"kind": "work", "slot": 1}]

    async def stop(self, services):
        self.calls.append(("stop", services))

    async def start(self, services):
        self.calls.append(("start", services))


@pytest.fixture
def environment(tmp_path):
    assert DATABASE and conninfo_to_dict(DATABASE)["dbname"].endswith("_test")
    name = "owner_agent_ops_" + uuid4().hex[:8] + "_test"
    url = make_conninfo(**{**conninfo_to_dict(DATABASE), "dbname": name})

    async def create():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as connection:
            await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        await migrate(url)

    run_async(create())
    try:
        yield Settings(database_url=url, data_dir=tmp_path)
    finally:

        async def drop():
            assert name.startswith("owner_agent_ops_") and name.endswith("_test")
            async with await AsyncConnection.connect(DATABASE, autocommit=True) as connection:
                await connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))

        run_async(drop())


async def backup_file(settings):
    target = settings.data_dir / "backups" / ("20261101T010000Z-" + uuid4().hex[:8])
    (target / "data").mkdir(parents=True)
    (target / "database.dump").write_bytes(b"isolated stand-in")
    (target / "data/model.key").write_bytes(b"isolated key")
    files = {
        str(file.relative_to(target).as_posix()): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in target.rglob("*")
        if file.is_file()
    }
    (target / "manifest.json").write_text(json.dumps({"format": 1, "files": files}))
    return target


async def verified(settings, target):
    return {"records_match": True, "files_verified": 2, "stand_in": True}


def test_backup_success_restarts_original_slots_and_only_rotates_registered_verified_copies(environment):
    async def exercise():
        db, services = Database(environment.database_url), Services()
        await db.pool.open(wait=True)
        try:
            worker = OperationsWorker(db, environment, services=services, creator=backup_file, verifier=verified)
            untouched = environment.data_dir / "backups/unregistered-manual"
            untouched.mkdir(parents=True)
            (untouched / "keep.txt").write_text("preserve")
            await db.execute("UPDATE backup_settings SET retain=2")
            for _ in range(4):
                await db.execute("INSERT INTO backup_jobs(id) VALUES(%s)", (uuid4(),))
                job = await worker.claim()
                assert job
                assert (await db.fetch("SELECT active FROM platform_maintenance"))[0]["active"]
                with pytest.raises(APIError, match="离线备份"):
                    async with request_slot(environment.database_url):
                        raise AssertionError("A request was admitted during backup")
                await worker.perform(job)
                assert not (await db.fetch("SELECT active FROM platform_maintenance"))[0]["active"]
            rows = await db.fetch("SELECT status,count(*) AS n FROM backup_jobs GROUP BY status")
            assert {row["status"]: row["n"] for row in rows} == {"SUCCEEDED": 2, "DELETED": 2}
            assert (untouched / "keep.txt").read_text() == "preserve"
            assert len(services.calls) == 8
            assert all(
                [{key: row[key] for key in ("kind", "slot")} for row in call[1]]
                == [{"kind": "api", "slot": 0}, {"kind": "work", "slot": 1}]
                for call in services.calls
            )
        finally:
            await db.pool.close()

    run_async(exercise())


def test_http_inflight_gate_and_busy_task_defer_without_cancelling_task(environment):
    async def exercise():
        db = Database(environment.database_url)
        await db.pool.open(wait=True)
        try:
            worker = OperationsWorker(db, environment, services=Services(), creator=backup_file, verifier=verified)
            await db.execute("INSERT INTO backup_jobs(id) VALUES(%s)", (uuid4(),))
            async with request_slot(environment.database_url):
                assert await worker.claim() is None
            owner, conversation, run = uuid4(), uuid4(), uuid4()
            await db.execute(
                "INSERT INTO users(id,username,password_hash) VALUES(%s,%s,'not-a-login')", (owner, str(owner))
            )
            await db.execute(
                "INSERT INTO conversations(id,owner_id,title) VALUES(%s,%s,'isolated')", (conversation, owner)
            )
            await db.execute(
                "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES(%s,%s,'none','PENDING')",
                (run, conversation),
            )
            assert await worker.claim() is None
            assert (await db.fetch("SELECT phase,reason FROM backup_jobs"))[0] == {
                "phase": "DEFERRED",
                "reason": "ACTIVE_TASKS",
            }
            assert (await db.fetch("SELECT status,cancel_requested FROM task_runs"))[0] == {
                "status": "PENDING",
                "cancel_requested": False,
            }
            assert not (await db.fetch("SELECT active FROM platform_maintenance"))[0]["active"]
        finally:
            await db.pool.close()

    run_async(exercise())


def test_partial_copy_failure_and_interrupted_host_recover_without_recopy(environment):
    async def exercise():
        db, services, copies = Database(environment.database_url), Services(), []
        await db.pool.open(wait=True)
        try:

            async def failed(settings):
                copies.append(True)
                raise RuntimeError("DISK_FULL")

            worker = OperationsWorker(db, environment, services=services, creator=failed, verifier=verified)
            first = uuid4()
            await db.execute("INSERT INTO backup_jobs(id) VALUES(%s)", (first,))
            await worker.perform(await worker.claim())
            assert (await db.fetch("SELECT status,reason FROM backup_jobs WHERE id=%s", (first,)))[0] == {
                "status": "FAILED",
                "reason": "DISK_FULL",
            }
            await db.execute("INSERT INTO backup_jobs(id) VALUES(%s)", (uuid4(),))
            abandoned = await worker.claim()
            assert abandoned
            await worker.recover()
            await worker.recover()
            assert len(copies) == 1
            assert (await db.fetch("SELECT status,reason FROM backup_jobs WHERE id=%s", (abandoned["id"],)))[0] == {
                "status": "FAILED",
                "reason": "BACKUP_INTERRUPTED_REVIEW",
            }
            assert not (await db.fetch("SELECT active FROM platform_maintenance"))[0]["active"]
        finally:
            await db.pool.close()

    run_async(exercise())


def test_daily_due_is_persistent_deduplicated_and_uses_beijing_time(environment):
    assert next_due(2, 30, datetime(2026, 11, 1, 0, 0, tzinfo=UTC)) == datetime(2026, 11, 1, 18, 30, tzinfo=UTC)

    async def exercise():
        db = Database(environment.database_url)
        await db.pool.open(wait=True)
        try:
            await db.execute("UPDATE backup_settings SET enabled=true,next_due_at=now()-interval '1 minute'")
            worker = OperationsWorker(db, environment, services=Services())
            await worker.schedule()
            await worker.schedule()
            assert len(await db.fetch("SELECT id FROM backup_jobs")) == 1
        finally:
            await db.pool.close()

    run_async(exercise())


def test_isolated_api_backup_job_fails_admission_without_service_control(environment, monkeypatch):
    settings = Settings(database_url=environment.database_url, data_dir=environment.data_dir,
                        internal_api_url="http://127.0.0.1:8139")
    username = "profile-guard-" + uuid4().hex
    run_async(add_user(settings.database_url, username, "integration-password", is_admin=True))
    app = create_app(settings)
    factory = __import__("asyncio").SelectorEventLoop if sys.platform == "win32" else None
    options = {"loop_factory": factory} if factory else {}
    launches = []

    def forbidden(*args, **kwargs):
        launches.append(True)
        raise AssertionError("Independent profile must not launch fixed production scripts")

    with TestClient(app, backend_options=options) as client:
        client.portal.call(app.state.db.execute,
            "INSERT INTO service_instances(id,kind,process_id,backend_id) VALUES(%s,'ops',1,pg_backend_pid())",
            (uuid4(),))
        origin = {"Origin": settings.web_origin}
        assert client.post("/api/v1/auth/login", headers=origin,
                           json={"username": username, "password": "integration-password"}).status_code == 200
        queued = client.post("/api/v1/operations/backups/jobs", headers=origin)
        assert queued.status_code == 202
        identifier = queued.json()["id"]
        before = set(settings.data_dir.rglob("*"))
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        worker = OperationsWorker(app.state.db, settings)
        assert client.portal.call(worker.claim) is None
        job = client.get("/api/v1/operations/backups").json()["items"][0]
        assert job["id"] == identifier and job["status"] == "FAILED" and job["phase"] == "ADMISSION"
        assert job["reason"] == "SERVICE_CONTROL_PROFILE_MISMATCH"
        assert not client.get("/api/v1/operations/backups").json()["maintenance"]["active"]
        assert client.portal.call(worker.claim) is None
        assert not launches and set(settings.data_dir.rglob("*")) == before
        assert not list(settings.data_dir.rglob("*.stop"))
