"""Keep the real database admission gate busy until single-owner teardown completes."""

import asyncio
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client

from app.browser_skill_work import browser_skill_tool
from app.errors import APIError

PREFIX = "/api/v1/browser-skills"


class Process:
    returncode = None

    def __init__(self):
        self.exited = threading.Event()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.exited.wait(timeout):
            raise subprocess.TimeoutExpired("fake-record", timeout)
        return self.returncode

    def kill(self):
        self.returncode = -1
        self.exited.set()


@pytest.mark.parametrize("failure", [False, True, "cleanup"])
def test_cancel_and_exception_hold_busy_through_stop_and_cleanup(client, monkeypatch, failure):
    connection, app = client
    user = UUID(connection.get("/api/v1/auth/me").json()["id"])
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (user,))
    runtime = app.state.browser_skills
    runtime.executable, runtime.instance = str(app.state.settings.data_dir / "bsk.exe"), "1234abcd"
    process, stopping, release = Process(), threading.Event(), threading.Event()
    calls = []

    async def launch(*args, **kwargs):
        return process

    async def command(*args, **kwargs):
        if args[0] == "--version":
            return "bsk 0.4.1"
        if args[0] == "browsers":
            return json.dumps([{"instance_id": "1234abcd", "extension_version": "0.4.1"}])
        calls.append(args)
        stopping.set()
        await asyncio.to_thread(release.wait, 8)
        if failure == "cleanup":
            raise OSError("cannot confirm session stop")
        process.returncode = 0
        process.exited.set()
        return "{}"

    monkeypatch.setattr(runtime, "launch", launch)
    monkeypatch.setattr(runtime, "command", command)
    real_require = __import__("app.browser_skill_runtime", fromlist=["require_account"]).require_account
    checked = 0

    async def require(*args):
        nonlocal checked
        checked += 1
        if failure is True and checked >= 2:
            raise APIError(403, "REVOKED", "权限已撤销")
        return await real_require(*args)

    monkeypatch.setattr("app.browser_skill_runtime.require_account", require)
    started = connection.post(
        PREFIX + "/recordings", headers=ORIGIN, json={"title": "lifecycle", "start_url": "https://fixture.test"}
    )
    assert started.status_code == 202, started.text
    identifier = started.json()["id"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = []
        if failure is not True:
            requests = [
                executor.submit(connection.post, PREFIX + f"/recordings/{identifier}/cancel", headers=ORIGIN)
                for _ in range(2)
            ]
        try:
            assert stopping.wait(4)
            row = connection.get(PREFIX + f"/recordings/{identifier}").json()
            assert row["status"] == "stopping"
            assert connection.get(PREFIX + "/status").json()["code"] == "BROWSER_BUSY"
            assert (
                connection.post(
                    PREFIX + "/recordings", headers=ORIGIN, json={"title": "next", "start_url": "https://fixture.test"}
                ).status_code
                == 409
            )
            skill = str(uuid4())
            tool = browser_skill_tool(
                app.state.db, runtime, SimpleNamespace(journal=object()), {"owner_id": user}, {"skill_ids": [skill]}
            )
            with pytest.raises(APIError) as error:
                connection.portal.call(
                    tool.ainvoke,
                    {
                        "skill_id": skill,
                        "version": 1,
                        "parameters": {},
                        "execution_boundary": "local_authenticated_browser",
                        "skill_name": "fixture",
                        "step_count": 1,
                    },
                )
            assert error.value.code == "BROWSER_BUSY"
        finally:
            release.set()
        for pending in requests:
            assert pending.result(timeout=5).status_code == 202

    async def drained():
        for _ in range(100):
            if not runtime.tasks:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("cleanup did not complete")

    connection.portal.call(drained)
    row = connection.get(PREFIX + f"/recordings/{identifier}").json()
    if failure == "cleanup":
        assert row["status"] == "stopping"
        assert connection.get(PREFIX + "/status").json()["code"] == "BROWSER_RECOVERY_REQUIRED"
        assert connection.post(PREFIX + f"/recordings/{identifier}/cancel", headers=ORIGIN).status_code == 202
        connection.portal.call(runtime.recover)
        assert connection.get(PREFIX + f"/recordings/{identifier}").json()["status"] == "stopping"
        # Explicit test-only operator confirmation: the controlled process is gone, then discard this test record.
        assert process.poll() is not None
        runtime.clean(UUID(identifier))
        connection.portal.call(app.state.db.execute, "DELETE FROM browser_skill_recordings WHERE id=%s", (identifier,))
        (runtime.root / "recovery-required").unlink()
    else:
        assert row["status"] == ("failed" if failure else "cancelled")
    assert len(calls) == 1
    assert not (runtime.root / "recordings" / identifier).exists()
