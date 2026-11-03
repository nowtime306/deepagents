"""Actual hidden supervisor restarts an API after its dedicated DB session is lost."""

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_api import DATABASE, migrate, run_async
from test_api import client as client

from app.models import CredentialVault
from app.storage import WORK_MAINTENANCE_LOCK


async def active_backend(url, kind):
    lock, mode = (710014, "ExclusiveLock") if kind == "api" else (
        (710033, "ExclusiveLock") if kind == "ops" else (WORK_MAINTENANCE_LOCK, "ShareLock"))
    async with await AsyncConnection.connect(url) as connection:
        return await (await connection.execute(
            "SELECT s.backend_id,s.id FROM service_instances s WHERE s.kind=%s "
            "AND s.heartbeat_at>now()-interval '5 seconds' AND EXISTS(SELECT 1 FROM pg_locks l "
            "WHERE l.pid=s.backend_id AND l.locktype='advisory' AND l.classid=0 AND l.objsubid=1 "
            "AND l.objid::bigint=%s AND l.mode=%s AND l.granted "
            "AND l.database=(SELECT oid FROM pg_database WHERE datname=current_database())) "
            "ORDER BY s.started_at DESC LIMIT 1", (kind, lock, mode))).fetchone()


@pytest.mark.parametrize("kind", ["api", "ask", "work", "ops"])
def test_actual_supervisor_session_loss_restart_unique_host_and_explicit_stop(tmp_path, kind):
    if kind == "work" and os.getenv("RUN_LOCAL_SANDBOX") != "1":
        pytest.skip("Work host acceptance requires the explicitly enabled local manager")
    assert DATABASE and conninfo_to_dict(DATABASE)["dbname"].endswith("_test")
    name = "owner_agent_service_" + uuid4().hex[:8] + "_test"
    assert re.fullmatch("owner_agent_service_[0-9a-f]{8}_test", name)
    url = make_conninfo(**{**conninfo_to_dict(DATABASE), "dbname": name})

    async def create():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as connection:
            await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        await migrate(url)

    async def current_backend():
        return await active_backend(url, kind)

    async def terminate(pid):
        async with await AsyncConnection.connect(url, autocommit=True) as connection:
            assert connection.info.dbname == name
            result = await (await connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid=%s AND pid<>pg_backend_pid()", (pid,))).fetchone()
            assert result and result[0]

    async def interrupt_all_connections():
        async with await AsyncConnection.connect(url, autocommit=True) as connection:
            assert connection.info.dbname == name
            rows = await (await connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid()")).fetchall()
            assert rows and all(row[0] for row in rows)

    run_async(create())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    directory = tmp_path / "data"
    CredentialVault(directory / "model.key")
    environment = {**os.environ, "DATABASE_URL": url, "PLATFORM_DATA_DIR": str(directory),
                   "WORK_ENABLED": "true" if kind == "work" else "false",
                   "AUTOMATION_ENABLED": "false", "EVALUATION_ENABLED": "false"}
    for key in ("OWNER_AGENT_SERVICE_TOKEN", "OWNER_AGENT_SERVICE_SLOT"):
        environment.pop(key, None)
    arguments = [sys.executable, "-m", "app.service_host", "--service", kind, "--start"]
    if kind == "api":
        arguments += ["--port", str(port)]
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    output = (tmp_path / "host.log").open("wb")
    process = subprocess.Popen(arguments, cwd=Path(__file__).parents[2], env=environment,
                               stdout=output, stderr=output, **flags)
    extra = None
    endpoint = f"http://127.0.0.1:{port}/health/ready"

    def ready():
        if kind != "api":
            return run_async(current_backend()) is not None
        try:
            with httpx.Client(timeout=1, trust_env=False) as client:
                return client.get(endpoint).status_code == 200
        except httpx.HTTPError:
            return False

    def wait_until(predicate):
        for _ in range(120):
            if predicate():
                return
            assert process.poll() is None, (tmp_path / "host.log").read_text(errors="replace")
            time.sleep(0.5)
        raise AssertionError("Isolated service did not become healthy; see " + str(tmp_path))

    try:
        wait_until(ready)
        first = run_async(current_backend())
        assert first
        duplicate = subprocess.run(arguments, cwd=Path(__file__).parents[2], env=environment,
                                   capture_output=True, timeout=15, check=False, **flags)
        assert duplicate.returncode == 1
        assert run_async(current_backend())[1] == first[1]
        run_async(terminate(first[0]))

        def restarted():
            current = run_async(current_backend())
            return current is not None and current[1] != first[1] and ready()

        wait_until(restarted)
        second = run_async(current_backend())
        run_async(interrupt_all_connections())

        def all_restarted():
            current = run_async(current_backend())
            return current is not None and current[1] not in {first[1], second[1]} and ready()

        wait_until(all_restarted)
        manifest = json.loads((directory / f"services/{kind}-0.json").read_text(encoding="utf-8"))
        assert manifest["restarts"] >= 2
        if kind == "work":
            extra = subprocess.Popen([*arguments, "--slot", "1"], cwd=Path(__file__).parents[2],
                                      env=environment, stdout=output, stderr=output, **flags)

            async def online_workers():
                async with await AsyncConnection.connect(url) as connection:
                    return (await (await connection.execute("SELECT count(*) FROM service_instances "
                        "WHERE kind='work' AND heartbeat_at>now()-interval '5 seconds' AND EXISTS("
                        "SELECT 1 FROM pg_locks l WHERE l.pid=backend_id AND l.classid=0 AND l.objsubid=1 "
                        "AND l.locktype='advisory' AND l.objid=%s AND l.mode='ShareLock' AND l.granted)",
                        (WORK_MAINTENANCE_LOCK,))).fetchone())[0]

            wait_until(lambda: run_async(online_workers()) == 2)
        (directory / f"services/{kind}.stop").write_text("requested by isolated test", encoding="utf-8")
        assert process.wait(timeout=35) == 0
        if extra:
            assert extra.wait(timeout=35) == 0
        assert not ready()
        assert json.loads((directory / f"services/{kind}-0.json").read_text(encoding="utf-8"))["state"] == "stopped"
    finally:
        (directory / "services").mkdir(parents=True, exist_ok=True)
        (directory / f"services/{kind}.stop").write_text("isolated test cleanup", encoding="utf-8")
        for instance in (process, extra):
            if instance and instance.poll() is None:
                try:
                    instance.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/PID", str(instance.pid), "/T", "/F"], capture_output=True,
                                       timeout=10, check=False)
                    else:
                        instance.kill()
                    instance.wait(timeout=10)
        output.close()

        async def drop():
            async with await AsyncConnection.connect(DATABASE, autocommit=True) as connection:
                await connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))

        run_async(drop())


def test_fresh_heartbeat_without_its_admission_lock_is_not_ready(client):
    c, app = client
    identifier = uuid4()
    async def exercise():
        async with await AsyncConnection.connect(app.state.settings.database_url, autocommit=True) as backend:
            try:
                await backend.execute(
                    "INSERT INTO service_instances(id,kind,process_id,backend_id) VALUES(%s,'ops',%s,pg_backend_pid())",
                    (identifier, os.getpid()))
                assert (await (await backend.execute("SELECT count(*) FROM service_instances WHERE id=%s "
                    "AND heartbeat_at>now()-interval '5 seconds'", (identifier,))).fetchone())[0] == 1
                assert await active_backend(app.state.settings.database_url, "ops") is None
                await backend.execute("SELECT pg_advisory_lock(710033)")
                assert (await active_backend(app.state.settings.database_url, "ops"))[1] == identifier
                await backend.execute("SELECT pg_advisory_unlock(710033)")
                assert await active_backend(app.state.settings.database_url, "ops") is None
            finally:
                await backend.execute("DELETE FROM service_instances WHERE id=%s", (identifier,))
    c.portal.call(exercise)
