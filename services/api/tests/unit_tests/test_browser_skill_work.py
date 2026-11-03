"""Approval refusal and uncertain effects never issue or repeat browser input."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.browser_skill_work import browser_skill_tool
from app.errors import APIError


async def nothing(*args):
    return None


class Database:
    def __init__(self):
        self.pool = self

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, *args):
        return self

    async def fetchone(self):
        return {"locked": True}

    async def fetch(self, query, params=()):
        if "browser_skill_recordings" in query:
            return []
        if "v.trajectory" in query:
            return [
                {
                    "name": "browser-fixture",
                    "trajectory": {
                        "parameters": [],
                        "steps": [
                            {"op": "navigate", "url": "https://fixture.test", "blocked": []},
                            {"op": "click", "target": {"role": "button", "name": "Save"}, "blocked": []},
                        ],
                    },
                }
            ]
        return [{"id": uuid4()}]


class Journal:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.uncertain = 0

    async def start(self, *args, **kwargs):
        if not self.allowed:
            raise PermissionError("No approval")
        return uuid4()

    async def unknown(self, *args):
        self.uncertain += 1

    async def finish(self, *args):
        pass


class Runtime:
    instance = "fixture-profile"

    def __init__(self):
        self.calls = []

    async def status(self):
        return {"ready": True}

    async def command(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("tab", "list"):
            return json.dumps({"tabs": [{"url": "https://fixture.test"}]})
        if args[:2] == ("session", "start"):
            return json.dumps({"session_id": "s1"})
        if args[:2] == ("cooperate", "sync"):
            return json.dumps(
                {
                    "state": {"mode": "ready", "editing": False},
                    "context_id": "fresh",
                    "observation": {"text": '@vom 1\n @e99 button "Save"'},
                }
            )
        if args[0] == "click":
            raise OSError("Effect unknown")
        return "{}"


@pytest.mark.parametrize("approved", [False, True])
async def test_denial_zero_calls_and_unknown_click_no_retry(monkeypatch, approved):
    monkeypatch.setattr("app.browser_skill_work.guard", nothing)
    runtime, journal = Runtime(), Journal(approved)
    identifier = str(uuid4())
    tool = browser_skill_tool(
        Database(),
        runtime,
        SimpleNamespace(journal=journal),
        {"owner_id": uuid4(), "network_domains": ["fixture.test"]},
        {"skill_ids": [identifier]},
    )
    with pytest.raises(OSError if approved else PermissionError):
        await tool.ainvoke(
            {
                "skill_id": identifier,
                "version": 1,
                "parameters": {},
                "execution_boundary": "local_authenticated_browser",
                "skill_name": "browser-fixture",
                "step_count": 2,
            }
        )
    if not approved:
        assert runtime.calls == []
    else:
        assert len([c for c in runtime.calls if c[0] == "click"]) == 1
        assert journal.uncertain == 1
        assert runtime.calls[-1][:2] == ("session", "stop")


@pytest.mark.parametrize(
    "flag,snapshot,environment,admin,allowed",
    [
        (False, {}, "browser", True, False),
        (True, {}, "browser", True, False),
        (False, {"allow_local_browser": True}, "browser", True, False),
        (True, {"allow_local_browser": True}, "coding", True, False),
        (True, {"allow_local_browser": True}, "browser", False, False),
        (True, {"allow_local_browser": True}, "browser", True, True),
    ],
)
async def test_per_step_guard_rechecks_database_consent_and_admin(
    monkeypatch, flag, snapshot, environment, admin, allowed
):
    from app.browser_skill_work import guard

    async def account(*args):
        return {"is_admin": admin}

    class ConsentDatabase:
        async def fetch(self, *args):
            return [{"allow_local_browser": flag, "agent_snapshot": snapshot, "environment": environment}]

    monkeypatch.setattr("app.browser_skill_work.require_account", account)
    backend = SimpleNamespace(guard=nothing)
    run = {"id": uuid4(), "owner_id": uuid4(), "lease_token": uuid4()}
    if allowed:
        await guard(ConsentDatabase(), backend, run)
    else:
        with pytest.raises(APIError):
            await guard(ConsentDatabase(), backend, run)


def test_consent_not_an_agent_or_task_draft_preference():
    from pydantic import ValidationError

    from app.work_run_api import StartWork
    from app.work_task_api import TaskInput

    assert StartWork(model_id="test", revision=1).allow_local_browser is False
    with pytest.raises(ValidationError):
        TaskInput(title="test", goal="test", allow_local_browser=True)


@pytest.mark.parametrize(
    "opt_in,environment,admin,expected",
    [
        (None, "browser", True, False),
        (False, "browser", True, False),
        (True, "coding", True, False),
        (True, "browser", False, False),
        (True, "browser", True, True),
    ],
)
async def test_work_context_only_provides_host_tool_after_explicit_opt_in(
    monkeypatch, opt_in, environment, admin, expected
):
    from deepagents.backends.state import StateBackend

    from app.work_engine import WorkEngine

    class Capabilities:
        async def for_owner(self, *args, **kwargs):
            return [], []

        async def skill_backend(self, *args):
            return StateBackend()

    class EmptyDatabase:
        async def fetch(self, *args):
            return []

    async def account(*args):
        return {"is_admin": admin}

    monkeypatch.setattr("app.permissions.require_account", account)
    engine = WorkEngine.__new__(WorkEngine)
    engine.database, engine.browser_skills = EmptyDatabase(), Runtime()
    engine.capabilities_factory = lambda run: Capabilities()
    snapshot = {"skill_ids": [str(uuid4())]}
    run = {"agent_snapshot": snapshot, "owner_id": uuid4(), "environment": environment}
    if opt_in is not None:
        snapshot["allow_local_browser"] = opt_in
        run["allow_local_browser"] = opt_in
    _, tools, _ = await engine.context(run, StateBackend())
    assert ("run_browser_skill" in [tool.name for tool in tools]) is expected


async def test_automatic_enqueue_cannot_inherit_local_consent():
    from app.automation_target import enqueue

    writes = []

    class Connection:
        async def execute(self, query, parameters):
            writes.append((query, parameters))

    source = {
        "snapshot": {"allow_local_browser": True, "model_id": "test", "model_revision": 1},
        "owner_id": uuid4(),
        "name": "auto",
        "mode": "work",
        "environment": "browser",
        "budget": {"model_calls": 5, "token_limit": 1000},
        "network_domains": [],
    }
    await enqueue(Connection(), source, "test")
    inserted = writes[-1][1]
    snapshots = [p.obj for p in inserted if hasattr(p, "obj") and isinstance(p.obj, dict)]
    assert snapshots[0]["allow_local_browser"] is False
    assert source["snapshot"]["allow_local_browser"] is True


def test_supervisor_connection_manifest_strict_and_reloadable(tmp_path, monkeypatch):
    from app.browser_skill_runtime import BrowserSkillRuntime

    monkeypatch.delenv("BROWSER_SKILL_EXECUTABLE", raising=False)
    monkeypatch.delenv("BROWSER_SKILL_INSTANCE", raising=False)
    runtime = BrowserSkillRuntime(None, tmp_path)
    runtime.root.mkdir()
    path = runtime.root / "connection.json"
    config = {
        "schema_version": 1,
        "executable": str((tmp_path / "bsk.exe").resolve()),
        "instance_id": "1234abcd",
        "cli_version": "0.4.1",
        "extension_version": "0.4.1",
        "protocol_version": "1.8",
    }
    path.write_text(json.dumps(config), encoding="utf-8")
    runtime.load_connection()
    assert runtime.instance == "1234abcd" and not runtime.configuration_error
    path.write_text(json.dumps({**config, "arbitrary_command": "forbidden"}), encoding="utf-8")
    runtime.load_connection()
    assert runtime.configuration_error and not runtime.instance
    path.write_text(json.dumps(config), encoding="utf-8")
    runtime.load_connection()
    assert runtime.instance == "1234abcd" and not runtime.configuration_error
    path.unlink()
    runtime.load_connection()
    assert not runtime.instance
