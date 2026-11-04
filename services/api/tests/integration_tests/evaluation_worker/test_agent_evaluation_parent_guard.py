"""Parent guard, marker and API/SDK substitutes. No target, PG or provider executes."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import evaluation_parent_guard as parent
import evaluation_platform_effects as effects
import httpx
import pytest
from fastapi import FastAPI
from langchain_core.tools import StructuredTool
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
    ToolAnnotations,
)

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())


def settings(tmp_path, monkeypatch):
    data = tmp_path / "isolated_data_test"
    data.mkdir()
    owner, environment = str(uuid4()), str(uuid4())
    value = SimpleNamespace(
        data_dir=data,
        database_url="dbname=unit_test",
        evaluation_platform_commit="a" * 40,
        evaluation_instance_id=environment,
    )
    binding = {
        "schema_version": 1,
        "contract": parent.CONTRACT,
        "parent_run_id": str(uuid4()),
        "parent_owner_id": str(uuid4()),
        "case_id": "AET-A02",
        "candidate_commit": "a" * 40,
        "environment_id": environment,
        "owner_id": owner,
    }
    (data / parent.MARKER).write_text(json.dumps(binding), encoding="utf-8")
    (data / "evaluation-instance.json").write_text(
        json.dumps({"environment_id": environment, "owner_id": owner}), encoding="utf-8"
    )
    monkeypatch.setenv(effects.ORIGIN, "http://127.0.0.1:23456")
    monkeypatch.setenv(effects.SECRET, "s" * 40)
    return value, binding


@pytest.mark.parametrize("lost", [(effects.ORIGIN,), (effects.SECRET,), (effects.ORIGIN, effects.SECRET)])
def test_required_marker_never_falls_back_after_one_or_both_env_values_are_lost(tmp_path, monkeypatch, lost):
    config, _ = settings(tmp_path, monkeypatch)
    for name in lost:
        monkeypatch.delenv(name)
    with pytest.raises((ValueError, RuntimeError)):
        parent.ParentGuard(config)


def test_live_guard_keeps_required_identity_even_if_all_env_values_disappear_after_startup(tmp_path, monkeypatch):
    config, _ = settings(tmp_path, monkeypatch)
    guard = parent.ParentGuard(config)
    calls = []
    monkeypatch.setattr(parent, "require_permit", lambda *args, **kwargs: calls.append(args))
    monkeypatch.delenv(effects.ORIGIN)
    monkeypatch.delenv(effects.SECRET)
    with pytest.raises(RuntimeError):
        guard.provider("AET-A02")
    assert calls == [] and guard.required is True


def test_parent_provider_cannot_borrow_a_different_case_identity(tmp_path, monkeypatch):
    config, _ = settings(tmp_path, monkeypatch)
    guard = parent.ParentGuard(config)
    calls = []
    monkeypatch.setattr(parent, "require_permit", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError):
        guard.provider("AET-A03")
    assert calls == []


def test_target_middleware_denies_new_mutation_but_keeps_read_and_formal_cancel_available(tmp_path, monkeypatch):
    config, _ = settings(tmp_path, monkeypatch)
    guard = parent.ParentGuard(config)
    counter = []

    def deny(*args, **kwargs):
        raise RuntimeError("Parent revoked")

    monkeypatch.setattr(parent, "require_permit", deny)
    app = FastAPI()
    guard.attach(app)

    @app.post("/api/v1/owned")
    async def mutate():
        counter.append("effect")
        return {"ok": True}

    @app.get("/api/v1/owned")
    async def read():
        return {"read": True}

    @app.post("/api/v1/runs/{identifier}/cancel")
    async def cancel(identifier):
        return {"cancel_requested": True}

    async def probe():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/api/v1/owned")).status_code == 503
            assert (await client.get("/api/v1/owned")).status_code == 200
            assert (await client.post("/api/v1/runs/" + str(uuid4()) + "/cancel")).status_code == 200

    asyncio.run(probe())
    assert counter == []


def test_uninstalled_hook_modules_cannot_self_report_a_ready_parent_bridge(tmp_path, monkeypatch):
    config, _ = settings(tmp_path, monkeypatch)
    guard = parent.ParentGuard(config)
    monkeypatch.setattr(parent, "import_module", lambda _: SimpleNamespace())
    app = FastAPI()
    guard.attach(app)
    app.state.db = SimpleNamespace()
    guard.install_runtime(app, SimpleNamespace())
    result = asyncio.run(guard.capability())
    assert result["ready"] is False and result["boundaries"] == {
        "provider": False,
        "tool": False,
        "sandbox": False,
        "submission": True,
    }


def test_wrapped_real_structured_tool_keeps_schema_and_no_effect_after_denial(tmp_path, monkeypatch):
    config, _ = settings(tmp_path, monkeypatch)
    guard = parent.ParentGuard(config)
    calls = []

    async def lookup(value: int) -> str:
        """Fixed substitute tool."""
        calls.append(value)
        return str(value)

    tool = StructuredTool.from_function(coroutine=lookup)
    before = tool.args_schema.model_json_schema()

    def deny(*args, **kwargs):
        raise RuntimeError("Parent revoked")

    monkeypatch.setattr(parent, "require_permit", deny)
    guarded = guard.wrap_tools([tool])[0]
    assert guarded.args_schema.model_json_schema() == before
    with pytest.raises(RuntimeError):
        asyncio.run(guarded.ainvoke({"value": 1}))
    assert calls == []


def test_actual_mcp_function_checks_parent_after_remote_list_before_real_call(tmp_path, monkeypatch):
    from app import mcp_service as module

    declaration = {
        "name": "lookup",
        "enabled": True,
        "read_only": True,
        "schema": {"type": "object"},
    }
    server = {"id": str(uuid4()), "tools": [declaration]}
    called = []
    checks = []

    class DB:
        async def fetch(self, *args):
            return [deepcopy(server)]

        async def evaluation_effect_guard(self, boundary):
            checks.append(boundary)
            raise RuntimeError("Revoked during remote await")

    class Session:
        async def list_tools(self):
            return ListToolsResult(
                tools=[
                    Tool(
                        name="lookup",
                        input_schema={"type": "object"},
                        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
                    )
                ]
            )

        async def call_tool(self, *args, **kwargs):
            called.append("effect")
            return CallToolResult(content=[TextContent(type="text", text="result")])

    @asynccontextmanager
    async def session(*args):
        yield Session()

    service = module.MCPService(DB(), None)
    service.session = session
    with pytest.raises(RuntimeError):
        asyncio.run(service.invoke(server["id"], "lookup", {}))
    assert called == [] and checks == ["tool"]


def test_actual_sandbox_create_denied_before_sdk_and_releases_only_known_unused_reservation(
    monkeypatch,
):
    from app import durable_sandboxes as module

    session = uuid4()
    updates = []
    remote = []

    async def network(*args):
        return None

    monkeypatch.setattr(module, "policy", network)

    async def create(*args, **kwargs):
        remote.append("create")
        raise AssertionError("Must not create")

    monkeypatch.setattr(module.Sandbox, "create", create)

    class DB:
        async def fetch(self, sql, args=()):
            if "network_domains" in sql:
                return [{"network_domains": []}]
            if "work_sandbox_namespace" in sql:
                return [{"id": uuid4()}]
            raise AssertionError("Unexpected SQL")

        async def evaluation_effect_guard(self, boundary):
            raise RuntimeError("Parent lease expired")

        async def execute(self, sql, args):
            updates.append((sql, args))

    class Service(module.DurableSandboxes):
        async def reserve(self, *args):
            return {"id": session}

    service = Service(DB(), SimpleNamespace(api_key="unit-only", use_server_proxy=True))
    with pytest.raises(RuntimeError):
        asyncio.run(service.create(uuid4(), uuid4(), "coding"))
    assert remote == [] and len(updates) == 1 and updates[0][1] == (session,)
    assert "state='RELEASED'" in updates[0][0] and "sandbox_id IS NULL" in updates[0][0]
