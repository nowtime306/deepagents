"""Deployment admission runs only against disposable databases ending in _test."""

import importlib.util
import os
import queue
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.maintenance import GATE

HELPER = Path(__file__).resolve().parents[4] / "scripts" / "deployment_gate.py"
spec = importlib.util.spec_from_file_location("deployment_gate", HELPER)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture
def isolated_url():
    base = os.getenv("TEST_DATABASE_URL", "")
    if not base:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated database ending in _test")
    settings = conninfo_to_dict(base)
    assert settings["dbname"].endswith("_test")
    name = "owner_agent_deploy_" + uuid4().hex[:8] + "_test"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(**{**settings, "dbname": name})
    try:
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            assert name.startswith("owner_agent_deploy_") and name.endswith("_test")
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def prepare(connection):
    connection.execute("CREATE TABLE schema_migrations(name text)")
    connection.execute("CREATE TABLE platform_maintenance(singleton boolean,active boolean)")
    connection.execute("INSERT INTO platform_maintenance VALUES(true,false)")
    for table in ("task_runs", "work_queue_runs", "workflow_runs", "resource_operations", "knowledge_experiments",
                  "notification_deliveries", "repository_actions", "work_browser_controls", "backup_jobs"):
        connection.execute(sql.SQL("CREATE TABLE {} (status text)").format(sql.Identifier(table)))


def test_gate_fences_shared_admission_and_preserves_pending(isolated_url):
    with (psycopg.connect(isolated_url, autocommit=True) as owner,
          psycopg.connect(isolated_url, autocommit=True) as claimant):
        prepare(owner)
        owner.execute("INSERT INTO task_runs VALUES('PENDING')")
        owner.execute("INSERT INTO work_queue_runs VALUES('PENDING')")
        assert gate.admit(owner) == "existing"
        assert not claimant.execute("SELECT pg_try_advisory_lock_shared(%s)", (GATE,)).fetchone()[0]
        assert owner.execute("SELECT status FROM task_runs").fetchone()[0] == "PENDING"
        assert not owner.execute("SELECT active FROM platform_maintenance").fetchone()[0]
    with psycopg.connect(isolated_url, autocommit=True) as claimant:
        assert claimant.execute("SELECT pg_try_advisory_lock_shared(%s)", (GATE,)).fetchone()[0]


@pytest.mark.parametrize("table,status", [
    ("task_runs", "RUNNING"), ("work_queue_runs", "RUNNING"), ("workflow_runs", "RUNNING"),
    ("resource_operations", "RUNNING"), ("knowledge_experiments", "RUNNING"),
    ("notification_deliveries", "SENDING"), ("repository_actions", "STARTED"),
    ("work_browser_controls", "OPEN"), ("backup_jobs", "RUNNING"),
])
def test_active_operations_refuse_without_mutation(isolated_url, table, status):
    with psycopg.connect(isolated_url, autocommit=True) as owner:
        prepare(owner)
        owner.execute(sql.SQL("INSERT INTO {} VALUES(%s)").format(sql.Identifier(table)), (status,))
        with pytest.raises(RuntimeError, match="ACTIVE_OPERATIONS"):
            gate.admit(owner)
        assert owner.execute(sql.SQL("SELECT status FROM {}").format(sql.Identifier(table))).fetchone()[0] == status


def test_fresh_unknown_existing_maintenance_and_busy_lock(isolated_url):
    with (psycopg.connect(isolated_url, autocommit=True) as owner,
          psycopg.connect(isolated_url, autocommit=True) as claimant):
        claimant.execute("SELECT pg_advisory_lock_shared(%s)", (GATE,))
        with pytest.raises(RuntimeError, match="ADMISSION_BUSY"):
            gate.admit(owner)
        claimant.execute("SELECT pg_advisory_unlock_shared(%s)", (GATE,))
        assert gate.admit(owner) == "fresh"
        owner.execute("CREATE TABLE unknown_table(id integer)")
        with pytest.raises(RuntimeError, match="UNKNOWN_SCHEMA"):
            gate.admit(owner)
        owner.execute("DROP TABLE unknown_table")
        owner.execute("CREATE TABLE schema_migrations(name text)")
        with pytest.raises(RuntimeError, match="OFFLINE_UPGRADE_REQUIRED"):
            gate.admit(owner)
        owner.execute("CREATE TABLE platform_maintenance(singleton boolean,active boolean)")
        owner.execute("INSERT INTO platform_maintenance VALUES(true,true)")
        with pytest.raises(RuntimeError, match="MAINTENANCE_ACTIVE"):
            gate.admit(owner)


def test_lock_loss_detected_without_reacquisition(isolated_url):
    with psycopg.connect(isolated_url, autocommit=True) as owner:
        assert gate.admit(owner) == "fresh"
        owner.execute("SELECT pg_advisory_unlock_all()")
        with pytest.raises(RuntimeError, match="ADMISSION_LOCK_LOST"):
            gate.hold(owner, queue.Queue())
        assert not owner.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                                 "AND locktype='advisory')").fetchone()[0]


def test_eof_after_lock_loss_refuses_success_without_reacquisition(isolated_url):
    with psycopg.connect(isolated_url, autocommit=True) as owner:
        assert gate.admit(owner) == "fresh"
        owner.execute("SELECT pg_advisory_unlock_all()")
        commands = queue.Queue()
        commands.put("")
        with pytest.raises(RuntimeError, match="ADMISSION_LOCK_LOST"):
            gate.hold(owner, commands)
        assert not owner.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                                 "AND locktype='advisory')").fetchone()[0]


@pytest.mark.parametrize("mode", ["eof", "disconnect", "disconnect_eof"])
def test_real_helper_stdin_eof_or_connection_loss_releases_gate(isolated_url, mode):
    environment = {**os.environ, "DATABASE_URL": isolated_url}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    process = subprocess.Popen([sys.executable, "-u", str(HELPER)], env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, **flags)
    try:
        # Poll the redirected pipe through a thread so a broken handshake cannot hang the suite.
        import threading

        replies = queue.Queue()
        threading.Thread(target=lambda: replies.put(process.stdout.readline()), daemon=True).start()
        assert replies.get(timeout=10).strip() == "READY:fresh"
        with psycopg.connect(isolated_url, autocommit=True) as probe:
            assert not probe.execute("SELECT pg_try_advisory_lock_shared(%s)", (GATE,)).fetchone()[0]
            if mode != "eof":
                probe.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                              "WHERE datname=current_database() AND pid<>pg_backend_pid()")
            if mode != "disconnect":
                process.stdin.close()
            assert process.wait(timeout=7) == (0 if mode == "eof" else 1)
            assert probe.execute("SELECT pg_try_advisory_lock_shared(%s)", (GATE,)).fetchone()[0]
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            deadline = time.monotonic() + 7
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert process.poll() is not None, "Helper did not release after parent stdin closed"
        process.stdout.close()
        process.stderr.close()
