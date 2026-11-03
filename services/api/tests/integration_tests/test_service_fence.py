"""A recovered pool must not authorize work under an old, lost service lock."""

import asyncio
import re

import pytest
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from test_api import DATABASE
from test_api import client as client

from app.errors import APIError
from app.service_fence import ServiceFence, run_fenced

LOCK = 710090


def test_dedicated_disconnect_fences_healthy_pool_and_never_reacquires(client):
    connection, app = client

    async def exercise():
        database_name = conninfo_to_dict(DATABASE)["dbname"]
        assert database_name == "owner_agent_test" or re.fullmatch(r"owner_agent_[a-z0-9_]+_test", database_name)
        async with await AsyncConnection.connect(DATABASE, autocommit=True,
                                                   application_name="owner-agent-fence-test") as guard:
            await guard.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
            fence = ServiceFence(app.state.db, guard, "work", [(LOCK, False)])
            await fence.start()
            try:
                await fence.check()
                pid = guard.info.backend_pid
                async with await AsyncConnection.connect(DATABASE, autocommit=True) as admin:
                    ended = await (await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE pid=%s AND datname=current_database() AND application_name='owner-agent-fence-test'",
                        (pid,))).fetchone()
                    assert ended and ended[0]
                with pytest.raises(APIError) as error:
                    await fence.check()
                assert error.value.code == "SERVICE_RESTARTING" and fence.lost.is_set()
                assert (await app.state.db.fetch("SELECT 1 AS healthy"))[0]["healthy"] == 1
                with pytest.raises(APIError):
                    await fence.check()
                # The old registration conservatively survives until explicit cleanup/heartbeat expiry.
                assert await app.state.db.fetch("SELECT 1 FROM service_instances WHERE id=%s", (fence.id,))
            finally:
                await fence.close()
            assert not await app.state.db.fetch("SELECT 1 FROM service_instances WHERE id=%s", (fence.id,))

    connection.portal.call(exercise)


def test_unlock_without_disconnect_is_also_permanently_fenced(client):
    connection, app = client

    async def exercise():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as guard:
            await guard.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
            fence = ServiceFence(app.state.db, guard, "ask", [(LOCK, False)])
            await fence.start()
            try:
                await guard.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
                with pytest.raises(APIError):
                    await fence.check()
                await guard.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
                with pytest.raises(APIError):
                    await fence.check()
            finally:
                await fence.close()

    connection.portal.call(exercise)


def test_worker_cancelled_before_any_followup_effect_on_stop_marker(client, tmp_path):
    connection, app = client

    async def exercise():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as guard:
            await guard.execute("SELECT pg_advisory_lock_shared(%s)", (LOCK,))
            stop = tmp_path / "work.stop"
            stop.write_text("requested", encoding="utf-8")
            fence = ServiceFence(app.state.db, guard, "work", [(LOCK, True)], stop_file=stop)
            await fence.start()
            calls = []

            async def work():
                try:
                    await asyncio.sleep(1)
                    calls.append("must not send another effect")
                finally:
                    calls.append("cancelled")

            try:
                with pytest.raises(APIError):
                    await run_fenced(fence, work())
                assert fence.reason == "SERVICE_STOP_REQUESTED" and calls == ["cancelled"]
            finally:
                await fence.close()

    connection.portal.call(exercise)


def test_normal_worker_completion_closes_monitor_and_lock_modes_are_checked(client):
    connection, app = client

    async def exercise():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as guard:
            await guard.execute("SELECT pg_advisory_lock_shared(%s)", (LOCK,))
            wrong = ServiceFence(app.state.db, guard, "work", [(LOCK, False)])
            with pytest.raises(APIError):
                await wrong.start()
            assert not await app.state.db.fetch("SELECT 1 FROM service_instances WHERE id=%s", (wrong.id,))
            fence = ServiceFence(app.state.db, guard, "work", [(LOCK, True)])
            await fence.start()
            try:
                await run_fenced(fence, asyncio.sleep(0))
                assert not fence.lost.is_set()
            finally:
                await fence.close()

    connection.portal.call(exercise)


def test_api_cannot_use_recovered_pool_after_its_unique_session_dies(client):
    from app.work_queue import WorkQueue

    connection, app = client
    fence = app.state.service_fence

    async def terminate_only_this_test_guard():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as control:
            assert control.info.dbname.endswith("_test")
            row = await (await control.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE pid=%s AND datname=current_database() AND pid<>pg_backend_pid()",
                (fence.connection.info.backend_pid,))).fetchone()
            assert row and row[0]

    connection.portal.call(terminate_only_this_test_guard)
    assert connection.get("/health/live").status_code == 200
    ready = connection.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["error"]["code"] == "SERVICE_RESTARTING"
    assert connection.portal.call(app.state.db.fetch, "SELECT 1 AS healthy")[0]["healthy"] == 1
    with pytest.raises(APIError):
        connection.portal.call(WorkQueue(app.state.db).claim)
    async def freed_lock():
        # A real replacement waits for old process/file resources to close (test_service_host).
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as fresh:
            row = await (await fresh.execute("SELECT pg_try_advisory_lock(710014)")).fetchone()
            assert row[0]

    connection.portal.call(freed_lock)
    assert connection.get("/health/ready").status_code == 503


def test_dead_supervisor_heartbeat_cancels_child_work(client, tmp_path, monkeypatch):
    import json
    import time

    connection, app = client
    monkeypatch.setenv("OWNER_AGENT_SERVICE_TOKEN", "test-host-instance")
    monkeypatch.setenv("OWNER_AGENT_SERVICE_SLOT", "0")
    (tmp_path / "work-0.json").write_text(json.dumps({"token": "test-host-instance", "state": "running",
        "heartbeat": time.monotonic() - 11}), encoding="utf-8")

    async def exercise():
        async with await AsyncConnection.connect(DATABASE, autocommit=True) as guard:
            await guard.execute("SELECT pg_advisory_lock_shared(%s)", (LOCK,))
            fence = ServiceFence(app.state.db, guard, "work", [(LOCK, True)], stop_file=tmp_path / "work.stop")
            await fence.start()
            try:
                with pytest.raises(APIError):
                    await run_fenced(fence, asyncio.sleep(60))
                assert fence.reason == "HOST_HEARTBEAT_LOST"
            finally:
                await fence.close()

    connection.portal.call(exercise)


def test_service_status_is_admin_only_and_excludes_process_identifiers(client):
    connection, app = client
    assert connection.get("/api/v1/operations/services").status_code == 403
    user = connection.get("/api/v1/auth/me").json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user,))
    response = connection.get("/api/v1/operations/services")
    assert response.status_code == 200
    row = next(s for s in response.json()["services"] if s["kind"] == "api")
    assert row["online"] >= 1 and row["last_heartbeat"]
    assert all(s["event"] in {"started", "stopped", "fenced"} for s in response.json()["events"])
    assert "process_id" not in response.text and "backend_id" not in response.text
