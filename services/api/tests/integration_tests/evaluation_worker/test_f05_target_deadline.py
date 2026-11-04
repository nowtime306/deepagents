"""Real guard/hooks and ASGI with controlled permit/DB/SDK edges; no actual target or provider."""

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import evaluation_f05_deadline_guard as guard_module
import evaluation_parent_guard as parent_module
import evaluation_platform_effects as parent_configuration
import evaluation_target as target_module
import f05_deadline_contract as proof
import httpx
import pytest
from app.resource_runtime import ResourceRun, active_resources
from evaluation_runner import fixed_case_deadline as parent_clock
from evaluation_runner import fixed_run
from evaluation_runner.journal import atomic_json
from f05_support import target
from fastapi import FastAPI
from langchain_core.tools import StructuredTool


@pytest.fixture
def boot(tmp_path, monkeypatch):
    clock = SimpleNamespace(ms=1_700_000_000_000, mono=0)
    monkeypatch.setattr(guard_module.time, "time_ns", lambda: clock.ms * 1_000_000)
    monkeypatch.setattr(guard_module.time, "monotonic", lambda: clock.mono)
    child, value, namespace = tmp_path / "AET-F05-r1-synthetic", target(), str(uuid4())
    state = child / "target"
    data = proof.data_directory(child, value.environment_id)
    data.mkdir(parents=True)
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-F05")
    context = fixed_run.suite_context(None, specimen, fixed_run.catalog(), value)
    context = {
        key: context[key]
        for key in (
            "schema_version",
            "suite_id",
            "catalog_digest",
            "case_definition_digest",
            "repeat_index",
            "case_id",
            "candidate_commit",
            "rules_id",
        )
    }
    atomic_json(child / "suite-context.json", context)
    atomic_json(state / "target-manifest.json", value.model_dump())
    pins = {name: context[name] for name in ("catalog_digest", "case_definition_digest", "rules_id")} | {
        "definition_version": 2
    }
    required = proof.initialize_target(
        child, value.environment_id, value.owner_id, namespace, value.platform_commit, pins
    )
    actual = {
        "environment_id": value.environment_id,
        "owner_id": value.owner_id,
        "sandbox_namespace": namespace,
        "platform_commit": value.platform_commit,
        **required,
    }
    atomic_json(state / "target-state.json", actual)
    atomic_json(data / "evaluation-instance.json", actual)
    settings = SimpleNamespace(
        data_dir=data,
        database_url="dbname=owner_agent_evaluation_" + UUID(value.environment_id).hex + "_test",
        evaluation_instance_id=value.environment_id,
        evaluation_platform_commit=value.platform_commit,
    )
    parent = parent_module.ParentGuard(settings)
    guard = guard_module.create(settings, required["f05_anchor_sha256"])
    app = FastAPI()
    app.state.db = SimpleNamespace()
    models = target_module.ProfileState()
    parent.attach(app, before_effect=guard.before_submission)
    guard.registered.add("submission")
    parent.install_runtime(app, models)
    guard.install_runtime(app, models, parent)
    token = active_resources.set(ResourceRun(app.state.db, UUID(value.owner_id), "work", uuid4()))
    result = SimpleNamespace(
        clock=clock,
        child=child,
        state=state,
        data=data,
        target=value,
        namespace=namespace,
        context=context,
        required=required,
        actual=actual,
        settings=settings,
        parent=parent,
        guard=guard,
        app=app,
        models=models,
    )

    def activate():
        parent_clock.seal(child, value, context, namespace)
        assert guard.capability()["phase"] == "ACTIVE"

    result.activate = activate
    try:
        yield result
    finally:
        active_resources.reset(token)


@pytest.mark.parametrize("boundary", ["provider", "tool", "sandbox", "submission"])
def test_standalone_installed_before_record_never_allows_business(boot, boundary):
    result = boot.guard.capability()
    assert not boot.parent.required and result["installed"] is True
    assert result["phase"] == "AWAITING_RECORD" and result["dispatch_allowed"] is False
    assert result["record_sha256"] is None and result["expires_at_ms"] is None
    with pytest.raises(RuntimeError):
        boot.guard.check(boundary)


def test_same_authenticated_record_active_then_sticky_expired_after_restart(boot):
    boot.activate()
    original = proof.read(boot.child / "case-deadline.json")
    assert boot.guard.capability()["expires_at_ms"] == original["expires_at_ms"]
    for boundary in ("provider", "tool", "sandbox", "submission"):
        boot.guard.check(boundary)
    boot.clock.ms += 300_000
    assert boot.guard.capability()["phase"] == "EXPIRED"
    boot.clock.ms -= 200_000
    clone = guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    assert clone.capability()["phase"] == "BLOCKED"
    assert proof.read(boot.child / "case-deadline.json") == original


@pytest.mark.parametrize("failure", ["activation", "expiry"])
def test_double_storage_failure_then_rollback_restart_never_grants(boot, monkeypatch, failure):
    original_once = proof.write_once
    if failure == "expiry":
        boot.activate()
        original_binding = proof.bounded(boot.data / proof.BINDING)
        boot.clock.ms += 300_000
        boot.clock.mono += 300

    def unavailable(path, *args, **kwargs):
        if path.name in {proof.POISON, proof.BINDING}:
            raise OSError("synthetic storage unavailable")
        return original_once(path, *args, **kwargs)

    def unavailable_replace(*args, **kwargs):
        raise OSError("synthetic storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(proof, "write_once", unavailable)
        patch.setattr(proof, "replace", unavailable_replace)
        if failure == "activation":
            parent_clock.seal(boot.child, boot.target, boot.context, boot.namespace)
        assert boot.guard.capability()["phase"] == "BLOCKED"
        assert not (boot.data / proof.POISON).exists()
    if failure == "expiry":
        assert proof.bounded(boot.data / proof.BINDING) == original_binding
        boot.clock.ms -= 200_000
    clone = guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    clone.registered = {"provider", "tool", "sandbox", "submission"}
    assert clone.capability()["phase"] == "BLOCKED"
    effects = []
    for boundary in sorted(clone.registered):
        with pytest.raises(RuntimeError):
            clone.check(boundary)
            effects.append(boundary)
    assert effects == []


def test_active_restart_reads_original_proof_without_acquiring_window(boot):
    boot.activate()
    original_binding = proof.bounded(boot.data / proof.BINDING)
    original_record = proof.bounded(boot.child / "case-deadline.json")
    clone = guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    value = clone.capability()
    assert value["phase"] == "BLOCKED" and value["dispatch_allowed"] is False
    assert value["deadline_bound"] is True
    assert value["record_sha256"] == boot.guard.capability()["record_sha256"]
    assert value["expires_at_ms"] == boot.guard.capability()["expires_at_ms"]
    assert proof.bounded(boot.data / proof.BINDING) == original_binding
    assert proof.bounded(boot.child / "case-deadline.json") == original_record
    assert boot.guard.capability()["phase"] == "ACTIVE"


def test_clean_awaiting_rebuild_is_blocked_but_first_live_instance_keeps_its_only_window(boot):
    clone = guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    assert clone.capability()["phase"] == "BLOCKED"
    assert boot.guard.capability()["phase"] == "AWAITING_RECORD"
    boot.activate()
    assert clone.capability()["phase"] == "BLOCKED"
    assert boot.guard.capability()["phase"] == "ACTIVE"


@pytest.mark.parametrize("boundary", ["provider", "tool", "sandbox"])
def test_composed_existing_guard_wait_cannot_grant_after_expiry(boot, boundary):
    boot.activate()
    calls = []

    def prior_provider(case_id):
        calls.append(case_id)
        boot.clock.ms += 300_000

    async def prior_effect(value):
        calls.append(value)
        await asyncio.sleep(0)
        boot.clock.ms += 300_000

    app, models = FastAPI(), target_module.ProfileState()
    app.state.db = SimpleNamespace(evaluation_effect_guard=prior_effect)
    models.parent_effect_guard = prior_provider
    boot.guard.install_runtime(app, models, boot.parent)
    with pytest.raises(RuntimeError):
        if boundary == "provider":
            models.parent_effect_guard("AET-F05")
        else:
            asyncio.run(app.state.db.evaluation_effect_guard(boundary))
    assert calls == ["AET-F05" if boundary == "provider" else boundary]


def required_parent(boot, monkeypatch, permit):
    atomic_json(
        boot.data / parent_module.MARKER,
        {
            "schema_version": 1,
            "contract": parent_module.CONTRACT,
            "parent_run_id": str(uuid4()),
            "parent_owner_id": str(uuid4()),
            "case_id": "AET-F05",
            "candidate_commit": boot.target.platform_commit,
            "environment_id": boot.target.environment_id,
            "owner_id": boot.target.owner_id,
        },
    )
    monkeypatch.setenv(parent_configuration.ORIGIN, "http://127.0.0.1:23456")
    monkeypatch.setenv(parent_configuration.SECRET, "synthetic-local-only-" * 3)
    monkeypatch.setattr(parent_module, "require_permit", permit)
    return parent_module.ParentGuard(boot.settings)


def test_real_structured_tool_checks_after_original_parent_authorization(boot, monkeypatch):
    boot.activate()
    effects, grants = [], []

    def permit(*args, **kwargs):
        grants.append(True)
        boot.clock.ms += 300_000

    parent = required_parent(boot, monkeypatch, permit)

    async def tool(value: int):
        """Controlled original native tool."""
        effects.append(value)
        return value

    wrapped = parent.wrap_tools([StructuredTool.from_function(coroutine=tool)], before_effect=boot.guard.before_tool)[0]
    with pytest.raises(RuntimeError):
        asyncio.run(wrapped.ainvoke({"value": 1}))
    assert grants == [True] and effects == []


@pytest.mark.parametrize("required", [False, True])
def test_actual_asgi_submission_after_wait_is_denied_but_read_remains(boot, monkeypatch, required):
    boot.activate()
    app, effects = FastAPI(), []

    def permit(*args, **kwargs):
        boot.clock.ms += 300_000

    parent = required_parent(boot, monkeypatch, permit) if required else boot.parent
    parent.attach(app, before_effect=boot.guard.before_submission)

    @app.post("/api/v1/owned")
    async def mutate():
        effects.append(True)
        return {"ok": True}

    @app.get("/api/v1/owned")
    async def read():
        return {"ok": True}

    if not required:
        boot.clock.ms += 300_000

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://controlled") as client:
            response = await client.post("/api/v1/owned")
            assert response.status_code == 503 and boot.data.as_posix() not in response.text
            assert (await client.get("/api/v1/owned")).status_code == 200

    asyncio.run(requests())
    assert effects == []


def test_missing_required_parent_permission_never_becomes_allowed_by_valid_deadline(boot, monkeypatch):
    boot.activate()
    parent = required_parent(boot, monkeypatch, lambda *args, **kwargs: None)
    monkeypatch.delenv(parent_configuration.ORIGIN)
    monkeypatch.delenv(parent_configuration.SECRET)
    with pytest.raises(RuntimeError):
        parent.provider("AET-F05")
    assert boot.guard.capability()["phase"] == "ACTIVE"


@pytest.mark.parametrize("name", [proof.ANCHOR, proof.KEY, proof.BINDING])
def test_active_private_proof_loss_blocks_current_and_restart(boot, name):
    boot.activate()
    (boot.data / name).unlink()
    assert boot.guard.capability()["phase"] == "BLOCKED"
    if name in {proof.ANCHOR, proof.KEY}:
        with pytest.raises((OSError, ValueError)):
            guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    else:
        assert guard_module.create(boot.settings, boot.required["f05_anchor_sha256"]).capability()["phase"] == "BLOCKED"


@pytest.mark.parametrize("name", ["case-deadline.json", proof.AUTH])
def test_active_parent_proof_loss_cannot_return_to_awaiting(boot, name):
    boot.activate()
    (boot.child / name).unlink()
    assert boot.guard.capability()["phase"] == "BLOCKED"


@pytest.mark.parametrize("field,value", [("schema_version", True), ("mac", "0" * 64), ("record_sha256", "1" * 64)])
def test_bad_auth_poisoned_before_first_effect(boot, field, value):
    parent_clock.seal(boot.child, boot.target, boot.context, boot.namespace)
    auth = proof.read(boot.child / proof.AUTH)
    auth[field] = value
    atomic_json(boot.child / proof.AUTH, auth)
    assert boot.guard.capability()["phase"] == "BLOCKED"
    assert (boot.data / proof.CLAIM).exists()
    assert guard_module.create(boot.settings, boot.required["f05_anchor_sha256"]).capability()["phase"] == "BLOCKED"


@pytest.mark.parametrize(
    "field,value", [("limit_seconds", 600), ("expires_at_ms", 9_999_999_999_999), ("schema_version", True)]
)
def test_even_authenticated_bad_record_never_activates(boot, field, value):
    parent_clock.seal(boot.child, boot.target, boot.context, boot.namespace)
    record = proof.read(boot.child / "case-deadline.json")
    record[field] = value
    atomic_json(boot.child / "case-deadline.json", record)
    (boot.child / proof.AUTH).unlink()
    checksum = hashlib.sha256((boot.child / "case-deadline.json").read_bytes()).hexdigest()
    proof.sign_record(boot.child, record, checksum)
    assert boot.guard.capability()["phase"] == "BLOCKED"


def test_activation_write_failure_is_poisoned_across_restart(boot, monkeypatch):
    parent_clock.seal(boot.child, boot.target, boot.context, boot.namespace)
    original = proof.write_once

    def fail(path, *args, **kwargs):
        if path.name == proof.BINDING:
            raise OSError("controlled commit failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(proof, "write_once", fail)
    assert boot.guard.capability()["phase"] == "BLOCKED"
    assert (boot.data / proof.CLAIM).exists()
    assert guard_module.create(boot.settings, boot.required["f05_anchor_sha256"]).capability()["phase"] == "BLOCKED"


def test_expiry_persistence_failure_cannot_revive_after_clock_rollback(boot, monkeypatch):
    boot.activate()
    boot.clock.ms += 300_000

    def fail(*args):
        raise OSError("controlled terminal persistence failure")

    monkeypatch.setattr(proof, "replace", fail)
    assert boot.guard.capability()["phase"] == "BLOCKED"
    boot.clock.ms -= 100_000
    assert guard_module.create(boot.settings, boot.required["f05_anchor_sha256"]).capability()["phase"] == "BLOCKED"


def test_expired_bit_tampering_is_authenticated_and_never_reopens(boot):
    boot.activate()
    boot.clock.ms += 300_000
    assert boot.guard.capability()["phase"] == "EXPIRED"
    value = proof.read(boot.data / proof.BINDING)
    value["expired"] = False
    atomic_json(boot.data / proof.BINDING, value)
    boot.clock.ms -= 100_000
    assert guard_module.create(boot.settings, boot.required["f05_anchor_sha256"]).capability()["phase"] == "BLOCKED"


def test_parent_record_partial_write_awaits_but_cannot_allow_business(boot):
    record = {"schema_version": 1}
    atomic_json(boot.child / "case-deadline.json", record)
    assert boot.guard.capability()["phase"] == "AWAITING_RECORD"
    with pytest.raises(RuntimeError):
        boot.guard.check("submission")


@pytest.mark.parametrize("mode,owner", [("ask", None), ("work", "foreign")])
def test_foreign_execution_scope_is_denied_without_poisoning_valid_instance(boot, mode, owner):
    boot.activate()
    identifier = uuid4() if owner else UUID(boot.target.owner_id)
    token = active_resources.set(ResourceRun(boot.app.state.db, identifier, mode, uuid4()))
    try:
        with pytest.raises(RuntimeError):
            boot.guard.check("tool")
    finally:
        active_resources.reset(token)
    assert boot.guard.capability()["phase"] == "ACTIVE"


def test_capability_never_contains_local_key_auth_or_paths(boot):
    boot.activate()
    value = json.dumps(boot.guard.capability())
    assert str(boot.data) not in value and str(boot.child) not in value
    assert proof.bounded(boot.data / proof.KEY).hex() not in value
    assert proof.read(boot.child / proof.AUTH)["mac"] not in value


def test_startup_required_state_and_non_f05_default_never_fall_back(boot):
    assert proof.startup_anchor(boot.child, boot.actual, boot.required) == boot.required["f05_anchor_sha256"]
    with pytest.raises(ValueError):
        proof.startup_anchor(boot.child, boot.actual, {})
    with pytest.raises(ValueError):
        guard_module.create(boot.settings, None)


@pytest.mark.parametrize(
    "field", ["owner_id", "environment_id", "namespace", "suite", "repeat", "candidate", "target_digest"]
)
def test_target_independently_rejects_authenticated_wrong_scope(boot, field):
    parent_clock.seal(boot.child, boot.target, boot.context, boot.namespace)
    record = proof.read(boot.child / "case-deadline.json")
    if field in {"owner_id", "environment_id", "namespace"}:
        record["scope"][field] = str(uuid4())
    elif field == "suite":
        record["scope"]["suite_context"]["suite_id"] = str(uuid4())
    elif field == "repeat":
        record["scope"]["suite_context"]["repeat_index"] = True
    elif field == "candidate":
        record["scope"]["suite_context"]["candidate_commit"] = "b" * 40
    else:
        record["scope"]["target_digest"] = "0" * 64
    atomic_json(boot.child / "case-deadline.json", record)
    checksum = hashlib.sha256((boot.child / "case-deadline.json").read_bytes()).hexdigest()
    auth = proof.read(boot.child / proof.AUTH)
    auth.update(
        record_sha256=checksum,
        mac=proof.authentication(proof.bounded(boot.data / proof.KEY), boot.required["f05_anchor_sha256"], checksum),
    )
    atomic_json(boot.child / proof.AUTH, auth)
    assert boot.guard.capability()["phase"] == "BLOCKED"


def test_concurrent_activation_pins_the_same_record_once(boot):
    parent_clock.seal(boot.child, boot.target, boot.context, boot.namespace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: boot.guard.capability(), range(2)))
    assert all(value["phase"] == "ACTIVE" for value in values)
    assert values[0]["record_sha256"] == values[1]["record_sha256"]
    assert proof.read(boot.data / proof.CLAIM)["anchor_sha256"] == boot.required["f05_anchor_sha256"]


def test_auth_is_published_atomically_inside_only_this_private_child(boot, monkeypatch):
    original = proof.os.link
    observed = []

    def publish(source, destination):
        assert source.parent == destination.parent == boot.child
        assert destination.name == proof.AUTH and source.name == proof.AUTH + ".tmp"
        assert not destination.exists()
        observed.append(boot.guard.capability()["phase"])
        return original(source, destination)

    monkeypatch.setattr(proof.os, "link", publish)
    boot.activate()
    assert observed == ["AWAITING_RECORD"]


def test_existing_auth_cannot_be_replaced_by_second_publication(boot):
    boot.activate()
    original = proof.bounded(boot.child / proof.AUTH)
    record = proof.read(boot.child / "case-deadline.json")
    checksum = hashlib.sha256(proof.bounded(boot.child / "case-deadline.json")).hexdigest()
    with pytest.raises(FileExistsError):
        proof.sign_record(boot.child, record, checksum)
    assert proof.bounded(boot.child / proof.AUTH) == original


@pytest.mark.parametrize("f05", [False, True])
def test_actual_capabilities_route_exposes_only_f05_installed_vs_bound_state(boot, monkeypatch, f05):
    import evaluation_observers as observers

    async def environment(request):
        return {"owner_id": boot.target.owner_id, "environment_id": boot.target.environment_id}

    monkeypatch.setattr(observers, "environment", environment)
    app = FastAPI()
    if f05:
        app.state.evaluation_f05_deadline_guard = boot.guard
    observers.attach_observers(app)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://controlled") as client:
            response = await client.get("/api/v1/agent-evaluations/profiles/capabilities")
            assert response.status_code == 200
            value = response.json()
            if f05:
                assert value["f05_deadline_guard"]["phase"] == "AWAITING_RECORD"
                assert value["f05_deadline_guard"]["dispatch_allowed"] is False
            else:
                assert "f05_deadline_guard" not in value

    asyncio.run(request())


def test_real_sandbox_create_after_expiry_never_calls_sdk_and_retains_known_local_release(boot, monkeypatch):
    from app import durable_sandboxes as module

    boot.activate()
    boot.clock.ms += 300_000
    session, updates, remote = uuid4(), [], []

    async def network(*args):
        return None

    async def fetch(sql, args=()):
        if "network_domains" in sql:
            return [{"network_domains": []}]
        return [{"id": UUID(boot.namespace)}]

    async def execute(sql, args):
        updates.append((sql, args))

    async def create(*args, **kwargs):
        remote.append(True)
        pytest.fail("Expired target must not create a sandbox")

    boot.app.state.db.fetch, boot.app.state.db.execute = fetch, execute
    monkeypatch.setattr(module, "policy", network)
    monkeypatch.setattr(module.Sandbox, "create", create)

    class Service(module.DurableSandboxes):
        async def reserve(self, *args):
            return {"id": session}

    service = Service(boot.app.state.db, SimpleNamespace(api_key="synthetic-only", use_server_proxy=True))
    with pytest.raises(RuntimeError):
        asyncio.run(service.create(uuid4(), uuid4(), "coding"))
    assert remote == [] and len(updates) == 1 and updates[0][1] == (session,)
    assert "state='RELEASED'" in updates[0][0] and "sandbox_id IS NULL" in updates[0][0]


def test_real_mcp_invoke_waiting_for_list_rechecks_expiry_before_remote_call(boot, monkeypatch):
    from app import mcp_service, resource_tools
    from mcp.types import ListToolsResult, Tool, ToolAnnotations

    boot.activate()
    declaration = {"name": "lookup", "enabled": True, "read_only": True, "schema": {"type": "object"}}
    server = {"id": str(uuid4()), "tools": [declaration]}
    calls = []

    async def fetch(*args):
        return [deepcopy(server)]

    async def frozen(value, vault):
        return value

    async def used(*args):
        return None

    class Session:
        async def list_tools(self):
            boot.clock.ms += 300_000
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
            calls.append(True)
            pytest.fail("No MCP call after expiry")

    @asynccontextmanager
    async def session(*args):
        yield Session()

    boot.app.state.db.fetch = fetch
    monkeypatch.setattr(resource_tools, "frozen_server", frozen)
    monkeypatch.setattr(resource_tools, "used", used)
    service = mcp_service.MCPService(boot.app.state.db, None)
    service.session = session
    with pytest.raises(RuntimeError):
        asyncio.run(service.invoke(server["id"], "lookup", {}))
    assert calls == []


def test_real_work_workflow_authority_denies_background_root_after_cli_is_gone(boot):
    from app.workflow_work import WorkWorkflow
    from evaluation_runner import fixed_code_contract

    boot.activate()
    boot.clock.ms += 300_000
    root = {"id": uuid4(), "owner_id": UUID(boot.target.owner_id)}
    binding = {
        "workflow_run_id": uuid4(),
        "version_id": None,
        "snapshot": {"definition": fixed_code_contract.definition(), "model_id": "fixed"},
    }
    engine = SimpleNamespace(
        database=boot.app.state.db, models=None, model_factory=None, capabilities_factory=lambda _: None
    )
    flow = WorkWorkflow(engine, root, binding, None, lambda: boot.app.state.db.evaluation_effect_guard("tool"))
    with pytest.raises(RuntimeError):
        asyncio.run(flow.authority())
