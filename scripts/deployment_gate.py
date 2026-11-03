"""Hold the existing admission gate until the deployment parent's stdin closes."""

import queue
import sys
import threading
from pathlib import Path

import psycopg


def admit(connection):
    from app.maintenance import GATE

    if not connection.execute("SELECT pg_try_advisory_lock(%s)", (GATE,)).fetchone()[0]:
        raise RuntimeError("ADMISSION_BUSY")
    known = connection.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0]
    if not known:
        if connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]:
            raise RuntimeError("UNKNOWN_SCHEMA")
        return "fresh"
    if not connection.execute("SELECT to_regclass('public.platform_maintenance')").fetchone()[0]:
        raise RuntimeError("OFFLINE_UPGRADE_REQUIRED")
    if connection.execute("SELECT active FROM platform_maintenance WHERE singleton").fetchone()[0]:
        raise RuntimeError("MAINTENANCE_ACTIVE")
    conditions = {
        "task_runs": "status='RUNNING'",
        "work_queue_runs": "status='RUNNING'",
        "workflow_runs": "status='RUNNING'",
        "resource_operations": "status='RUNNING'",
        "knowledge_experiments": "status='RUNNING'",
        "notification_deliveries": "status='SENDING'",
        "repository_actions": "status='STARTED'",
        "work_browser_controls": "status<>'CLOSED'",
        "backup_jobs": "status='RUNNING'",
        "identity_deliveries": "status='sending'",
        "knowledge_video_jobs": "holds_memory OR status IN ('RUNNING','UNKNOWN')",
    }
    for table, condition in conditions.items():
        # Table names and conditions are fixed above, never deployment input.
        if (connection.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
                and connection.execute("SELECT 1 FROM " + table + " WHERE " + condition + " LIMIT 1").fetchone()):
            raise RuntimeError("ACTIVE_OPERATIONS")
    return "existing"


def hold(connection, commands):
    from app.maintenance import GATE

    while True:
        try:
            commands.get(timeout=0.5)
            release_requested = True
        except queue.Empty:
            release_requested = False
        held = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' "
            "AND classid=0 AND objsubid=1 AND objid::bigint=%s AND mode='ExclusiveLock')", (GATE,)
        ).fetchone()[0]
        if not held:
            raise RuntimeError("ADMISSION_LOCK_LOST")
        if release_requested:
            return


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))
    from app.config import Settings

    commands = queue.Queue()
    threading.Thread(target=lambda: commands.put(sys.stdin.readline()), daemon=True).start()
    with psycopg.connect(Settings.from_env().database_url, autocommit=True, connect_timeout=5,
                          options="-c statement_timeout=3000") as connection:
        schema = admit(connection)
        print("READY:" + schema, flush=True)
        hold(connection, commands)


if __name__ == "__main__":
    try:
        main()
    except (psycopg.Error, RuntimeError, OSError, ValueError) as error:
        # Database exceptions may include credentials; expose only controlled codes/types.
        code = str(error) if type(error) is RuntimeError else type(error).__name__
        print("ERROR:" + code, flush=True)
        sys.exit(1)
