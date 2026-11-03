"""Client cancellation must not corrupt the dedicated service-lock connection."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import anyio
import httpx
import pytest
import uvicorn
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from psycopg import AsyncConnection
from psycopg.pq import TransactionStatus
from test_api import DATABASE, ORIGIN, TEST_DATA, TestModel
from test_api import client as client
from test_run_streams import ask

from app.service_fence import ServiceFence


@pytest.mark.parametrize("operation", ["check", "heartbeat"])
def test_cancelled_probe_finishes_connection_cleanup_before_unlocking(client, operation):
    connection, app = client

    async def exercise():
        guard = await AsyncConnection.connect(DATABASE, autocommit=True)
        await guard.execute("SELECT pg_advisory_lock(710090)")
        started, cancelled = asyncio.Event(), []

        class SlowProbe:
            async def execute(self, sql, params=()):
                slow = operation == "check" or sql.startswith("INSERT INTO service_instances")
                if slow and not started.is_set():
                    started.set()
                    await guard.execute("SELECT pg_sleep(0.2)")
                return await guard.execute(sql, params)

        fence = ServiceFence(app.state.db, SlowProbe(), "ask", [(710090, False)])
        try:
            async def probe():
                try:
                    await getattr(fence, operation)()
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            async with anyio.create_task_group() as group:
                group.start_soon(probe)
                await started.wait()
                for _ in range(500):
                    if guard.pgconn.transaction_status == TransactionStatus.ACTIVE:
                        break
                    await asyncio.sleep(.001)
                else:
                    pytest.fail("The real lock probe must be in flight before cancellation")
                group.cancel_scope.cancel()
            assert cancelled == [True]
            assert not fence.mutex.locked()
            assert guard.pgconn.transaction_status == TransactionStatus.IDLE
            await fence.check()
            await fence.heartbeat()
            assert not fence.lost.is_set()
        finally:
            await guard.close()
            await fence.close()

    connection.portal.call(exercise)


def test_cancelled_sse_read_returns_clean_transaction_to_pool(client, caplog):
    from app.run_streams import reading

    connection, app = client

    async def exercise():
        started, used, cancelled = asyncio.Event(), [], []

        async def read():
            try:
                async with reading(), app.state.db.pool.connection() as sql:
                    used.append(sql)
                    started.set()
                    await sql.execute("SELECT pg_sleep(0.2)")
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        async with anyio.create_task_group() as group:
            group.start_soon(read)
            await started.wait()
            for _ in range(500):
                if used[0].pgconn.transaction_status == TransactionStatus.ACTIVE:
                    break
                await asyncio.sleep(.001)
            else:
                pytest.fail("The real pooled read must be in flight before cancellation")
            group.cancel_scope.cancel()
        assert cancelled == [True]
        assert not used[0].closed and used[0].pgconn.transaction_status == TransactionStatus.IDLE
        assert await app.state.db.fetch("SELECT 1 AS healthy") == [{"healthy": 1}]

    connection.portal.call(exercise)
    assert "another command is already in progress" not in caplog.text
    assert "discarding closed connection" not in caplog.text


@pytest.mark.parametrize("disconnect", [False, True])
def test_sse_read_deadline_cancels_real_long_query_and_returns_pool_connection(client, disconnect):
    from app.errors import APIError
    from app.run_streams import reading

    connection, app = client

    async def exercise():
        started, used, cancelled, errors = asyncio.Event(), [], [], []

        async def read():
            try:
                async with reading(), app.state.db.pool.connection() as sql:
                    used.append(sql)
                    started.set()
                    await sql.execute("SELECT pg_sleep(20)")
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            except APIError as error:
                errors.append((error.status, error.code))

        began = time.monotonic()
        async with anyio.create_task_group() as group:
            group.start_soon(read)
            await started.wait()
            for _ in range(500):
                if used[0].pgconn.transaction_status == TransactionStatus.ACTIVE:
                    break
                await asyncio.sleep(.001)
            else:
                pytest.fail("The deadline must interrupt a real PostgreSQL query")
            if disconnect:
                group.cancel_scope.cancel()
        assert 4.9 <= time.monotonic() - began < 10
        assert cancelled == ([True] if disconnect else [])
        assert errors == ([] if disconnect else [(503, "STREAM_READ_TIMEOUT")])
        assert not used[0].closed and used[0].pgconn.transaction_status == TransactionStatus.IDLE
        await app.state.service_fence.check()
        assert not app.state.service_fence.lost.is_set()
        assert await app.state.db.fetch("SELECT 1 AS healthy") == [{"healthy": 1}]

    connection.portal.call(exercise)
    _, run = ask(connection)
    assert "stream.end" in connection.get(f"/api/v1/runs/{run}/events").text


@pytest.mark.parametrize("initial", [True, False])
def test_sse_read_timeout_returns_initial_json_or_stream_error_then_allows_reconnect(client, initial):
    connection, app = client
    _, run = ask(connection)
    url = f"/api/v1/runs/{run}/events"
    original = app.state.db.fetch
    reads = []

    async def delayed_identity(sql, params=()):
        if sql.startswith("SELECT u.id FROM auth_sessions"):
            reads.append(True)
            if len(reads) == (1 if initial else 2):
                await original("SELECT pg_sleep(20)")
        return await original(sql, params)

    app.state.db.fetch = delayed_identity
    try:
        response = connection.get(url)
        if initial:
            assert response.status_code == 503 and response.json()["error"]["code"] == "STREAM_READ_TIMEOUT"
        else:
            assert response.status_code == 200
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert frames == [{"event": "stream.error", "data": {
                "status_code": 503, "code": "STREAM_READ_TIMEOUT", "message": "读取运行状态超时，请稍后重试",
            }}]
    finally:
        app.state.db.fetch = original
    assert connection.get("/health/ready").status_code == 200
    assert "stream.end" in connection.get(url).text


def test_terminal_first_frame_disconnects_keep_api_healthy_through_next(client, caplog):
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={"title": "取消订阅"}, headers=ORIGIN).json()["id"]
    ask = connection.post(f"/api/v1/conversations/{conversation}/runs",
        json={"message": "完整执行后只取消订阅", "model_id": "test"}, headers=ORIGIN).json()["run_id"]
    assert "stream.end" in connection.get(f"/api/v1/runs/{ask}/events").text
    path = f"/api/v1/runs/{ask}/events"
    ports = []
    for _ in range(2):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
    api, web = [f"http://127.0.0.1:{port}" for port in ports]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports[0], lifespan="off", log_level="error"))
    serving = connection.portal.start_task_soon(server.serve)
    root = Path(__file__).resolve().parents[4]
    web_root = root / "apps/web"
    next_cli = web_root / "node_modules/next/dist/bin/next"
    output = web_root / (".next-sse-cancel-" + uuid4().hex[:8])
    environment = {**os.environ, "PLATFORM_WEB_DIST_DIR": output.name, "PLATFORM_API_URL": api,
                   "NODE_OPTIONS": "--require ./scripts/use-typescript6.cjs"}
    next_process = None
    guard, fetch = app.state.service_fence.connection, app.state.db.fetch
    original_model, release = app.state.runtime.model_factory, None

    class SlowProbe:
        async def execute(self, sql, params=()):
            if sql.startswith("SELECT EXISTS(SELECT 1 FROM pg_locks"):
                await guard.execute("SELECT pg_sleep(0.005)")
            return await guard.execute(sql, params)

    async def slow_read(sql, params=()):
        await fetch("SELECT pg_sleep(0.005)")
        return await fetch(sql, params)

    try:
        app.state.service_fence.connection = SlowProbe()
        app.state.db.fetch = slow_read
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        TEST_DATA.mkdir(parents=True, exist_ok=True)
        with (TEST_DATA / "next-disconnect-build.log").open("wb") as log:
            built = subprocess.run(["node", str(next_cli), "build", "--webpack"], cwd=web_root, env=environment,
                stdout=log, stderr=log, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        assert built.returncode == 0, "See next-disconnect-build.log"
        with (TEST_DATA / "next-disconnect-start.log").open("wb") as log:
            next_process = subprocess.Popen(
                ["node", str(next_cli), "start", "--hostname", "127.0.0.1", "--port", str(ports[1])],
                cwd=web_root, env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        cookies = dict(connection.cookies)

        def first_only(base, path):
            with httpx.Client(base_url=base, cookies=cookies, trust_env=False, timeout=15) as network:
                with network.stream("GET", path, headers={"Accept-Encoding": "gzip"}) as response:
                    assert response.status_code == 200
                    assert "no-transform" in response.headers["cache-control"]
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            return json.loads(line[6:])
                    raise AssertionError("A completed run must replay a first frame")

        for _ in range(100):
            try:
                if httpx.get(web + "/api/v1/auth/registration", trust_env=False, timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            pytest.fail("The isolated production Next proxy did not become ready")

        async def gated_model():
            ready = asyncio.Event()

            class GatedStream(TestModel):
                async def _astream(self, *args, **kwargs):
                    yield ChatGenerationChunk(message=AIMessageChunk(content="第一段"))
                    await ready.wait()
                    yield ChatGenerationChunk(message=AIMessageChunk(content="第二段"))

            app.state.runtime.model_factory = lambda _: GatedStream(responses=["禁止整段备用输出"])
            return ready

        release = connection.portal.call(gated_model)
        streaming = connection.post(f"/api/v1/conversations/{conversation}/runs", headers=ORIGIN,
            json={"message": "验证真实聊天流", "model_id": "test"}).json()["run_id"]
        visible = []
        with httpx.Client(base_url=web, cookies=cookies, trust_env=False, timeout=15) as network:
            with network.stream("GET", f"/api/v1/runs/{streaming}/events") as response:
                assert response.status_code == 200
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["event"] == "message.delta":
                        visible.append(event["data"]["text"])
                        if visible == ["第一段"]:
                            assert not release.is_set()
                            assert network.get(f"/api/v1/runs/{streaming}").json()["status"] == "RUNNING"
                            connection.portal.call(release.set)
                    if event["event"] == "stream.end":
                        assert event["data"]["status"] == "SUCCEEDED"
                        break
        assert "".join(visible) == "第一段第二段"
        # Eighteen sequential Ask closes plus six concurrent ones; never cancel business execution.
        for _ in range(9):
            for base in (api, web):
                assert "event_id" in first_only(base, path)
        with ThreadPoolExecutor(max_workers=6) as workers:
            futures = [workers.submit(first_only, base, path) for _ in range(3) for base in (api, web)]
            assert all("event_id" in future.result(timeout=20) for future in futures)
        for base in (api, web):
            with httpx.Client(base_url=base, cookies=cookies, trust_env=False, timeout=20) as network:
                response = network.get(path)
                assert response.status_code == 200 and '"event": "stream.end"' in response.text
        assert connection.get("/health/ready").status_code == 200
        assert not app.state.service_fence.lost.is_set()

        async def healthy_guard():
            async with app.state.service_fence.mutex:
                assert not guard.closed and guard.pgconn.transaction_status == TransactionStatus.IDLE

        connection.portal.call(healthy_guard)
        assert "another command is already in progress" not in caplog.text
        assert "discarding closed connection" not in caplog.text
    finally:
        if release:
            connection.portal.call(release.set)
        if next_process:
            next_process.terminate()
            try:
                next_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                next_process.kill()
                next_process.wait(timeout=5)
        server.should_exit = True
        try:
            serving.result(timeout=15)
        finally:
            app.state.service_fence.connection, app.state.db.fetch = guard, fetch
            app.state.runtime.model_factory = original_model
            assert output.resolve().parent == web_root.resolve() and output.name.startswith(".next-sse-cancel-")
            config_path = web_root / "tsconfig.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            own_types = {f"{output.name}/types/**/*.ts", f"{output.name}/dev/types/**/*.ts"}
            retained = [item for item in config["include"] if item not in own_types]
            if retained != config["include"]:
                config["include"] = retained
                config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            declarations = web_root / "next-env.d.ts"
            text = declarations.read_text(encoding="utf-8")
            if f"./{output.name}/" in text:
                declarations.write_text(text.replace(f"./{output.name}/", "./.next/"), encoding="utf-8")
            if output.exists():
                shutil.rmtree(output)
