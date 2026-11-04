"""Real authenticated guard + fixed sandbox admission with DB/SDK edges substituted."""

from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import evaluation_flow_code as code
import pytest
import test_f05_target_deadline as fence

boot = fence.boot


def limits():
    return {
        "schema_version": 1,
        "enforcement": "controlled-model-factory-v1",
        "scope": "resource-run-root",
        "profiles": {"AET-F05": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}},
    }


def reviewed(boot):
    return {
        "sandbox_bounds": {"AET-F05": 10},
        "f05_deadline": {
            "definition_version": 2,
            **{key: boot.context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id")},
        },
    }


def service(boot, monkeypatch):
    state = SimpleNamespace(active=0, roots=3, owner=boot.target.owner_id, definition=code.definition())
    profiles = SimpleNamespace(
        lock=Lock(), models={"fixed": {"case_id": "AET-F05", "role": "lead", "owner_id": state.owner}}
    )

    class DB:
        async def fetch(self, query, args=()):
            if query.startswith("SELECT count(*)"):
                return [{"count": state.roots if "work_queue_runs" in query else state.active}]
            return [{"owner_id": state.owner, "model_id": "fixed", "snapshot": {"definition": state.definition}}]

    async def reserve(*args):
        return {"id": uuid4()}

    monkeypatch.setattr(code.DurableSandboxes, "reserve", reserve)
    value = code.CodeSandboxes(
        DB(),
        SimpleNamespace(api_key="synthetic-private-only", use_server_proxy=True),
        boot.child / "capacity.json",
        profiles,
    )
    return value, state


async def test_authentic_awaiting_can_prepare_but_no_reserve_before_same_record_active(boot, monkeypatch):
    value, _state = service(boot, monkeypatch)
    assert value.capability()["fixed_cases"]["AET-F05"]["enabled"] is False
    assert value.activate(boot.guard, limits(), reviewed=reviewed(boot)) is True
    with pytest.raises(RuntimeError, match="not active"):
        await value.reserve(uuid4(), uuid4(), "coding")
    assert value.attempts == 0
    boot.activate()
    for _ in range(10):
        assert await value.reserve(uuid4(), uuid4(), "coding")
    with pytest.raises(RuntimeError, match="budget"):
        await value.reserve(uuid4(), uuid4(), "coding")
    assert value.attempts == 10 and value.capability()["max_created"] == 2


@pytest.mark.parametrize(
    "fault", ["missing_guard", "bool_schema", "nonzero_model", "bool_model", "wrong_scope", "missing_hook"]
)
async def test_missing_or_wrong_activation_never_enables(boot, monkeypatch, fault):
    value, _state = service(boot, monkeypatch)
    proof, guard = limits(), boot.guard
    if fault == "missing_guard":
        guard = None
    elif fault == "bool_schema":
        proof["schema_version"] = True
    elif fault == "nonzero_model":
        proof["profiles"]["AET-F05"]["max_model_calls"] = 1
    elif fault == "bool_model":
        proof["profiles"]["AET-F05"]["max_model_calls"] = False
    elif fault == "wrong_scope":
        proof["scope"] = "unbounded"
    else:
        guard.registered.remove("sandbox")
    assert value.activate(guard, proof, reviewed=reviewed(boot)) is False
    with pytest.raises(RuntimeError, match="blocked"):
        await value.reserve(uuid4(), uuid4(), "coding")
    assert value.attempts == 0


@pytest.mark.parametrize("fault", ["root4", "wrong_graph", "foreign_owner", "expiry", "restart"])
async def test_real_runtime_scope_and_material_still_gate_each_reservation(boot, monkeypatch, fault):
    value, state = service(boot, monkeypatch)
    assert value.activate(boot.guard, limits(), reviewed=reviewed(boot))
    boot.activate()
    if fault == "root4":
        state.roots = 4
    elif fault == "wrong_graph":
        state.definition = deepcopy(state.definition)
        state.definition["node_budget"] = 6
    elif fault == "foreign_owner":
        state.owner = str(uuid4())
        value.profiles.models["fixed"]["owner_id"] = state.owner
    elif fault == "expiry":
        boot.clock.ms += 300_000
    else:
        guard = fence.guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
        guard.registered = set(boot.guard.registered)
        assert value.activate(guard, limits(), reviewed=reviewed(boot)) is False
    with pytest.raises((ValueError, RuntimeError)):
        await value.reserve(uuid4(), uuid4(), "coding")
    assert value.attempts == 0


async def test_one_active_sandbox_never_admits_concurrent_second(boot, monkeypatch):
    value, state = service(boot, monkeypatch)
    value.activate(boot.guard, limits(), reviewed=reviewed(boot))
    boot.activate()
    state.active = 1
    assert await value.reserve(uuid4(), uuid4(), "coding") is None
    assert value.attempts == 0


def test_bare_enabled_boolean_is_not_target_authority(boot, monkeypatch):
    value, _state = service(boot, monkeypatch)
    copied = code.CodeSandboxes(
        value.database, value.config, boot.child / "second-capacity.json", value.profiles, enabled=True
    )
    assert copied.capability()["fixed_cases"]["AET-F05"]["enabled"] is False


@pytest.mark.parametrize("boundary", ["provider", "tool", "sandbox", "submission"])
def test_owned_case_cancel_fences_every_later_effect_before_expiry(boot, boundary):
    boot.activate()
    original = (boot.child / "case-deadline.json").read_bytes()
    result = boot.child / "result"
    result.mkdir()
    (result / "cancel.request").write_bytes(b"synthetic owned cancellation")
    effects = []
    with pytest.raises(RuntimeError, match="not active"):
        boot.guard.check(boundary)
        effects.append(boundary)
    assert effects == [] and boot.guard.capability()["phase"] == "BLOCKED"
    clone = fence.guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    assert clone.capability()["phase"] == "BLOCKED"
    assert (boot.child / "case-deadline.json").read_bytes() == original


@pytest.mark.parametrize("same_suite", [False, True])
def test_shared_cancel_requires_exact_authenticated_suite_and_child(boot, same_suite):
    import json

    boot.activate()
    context = boot.context
    suite = {
        "schema_version": 1,
        "suite_id": context["suite_id"] if same_suite else str(uuid4()),
        "candidate_commit": context["candidate_commit"],
        "dataset_digest": context["catalog_digest"],
        "attempts": [{"child": boot.child.name, "case_id": "AET-F05", "repeat_ordinal": context["repeat_index"]}],
    }
    (boot.child.parent / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
    (boot.child.parent / "cancel.request").write_bytes(b"synthetic same-directory marker")
    assert boot.guard.capability()["phase"] == ("BLOCKED" if same_suite else "ACTIVE")


def parent_suite(boot):
    context = boot.context
    return {
        "schema_version": 1,
        "suite_id": context["suite_id"],
        "candidate_commit": context["candidate_commit"],
        "dataset_digest": context["catalog_digest"],
        "attempts": [{"child": boot.child.name, "case_id": "AET-F05", "repeat_ordinal": context["repeat_index"]}],
    }


def rejected_link(monkeypatch, path):
    """Substitute the existing safe-path rejection, not a physical symlink fixture."""
    original = fence.proof.safe

    def safe(value):
        if value == path:
            raise ValueError("Synthetic existing safe-path link rejection")
        return original(value)

    monkeypatch.setattr(fence.proof, "safe", safe)


@pytest.mark.parametrize(
    "fault",
    ["missing", "invalid", "foreign", "wrong_candidate", "wrong_child", "bool_repeat", "meta_link", "marker_link"],
)
def test_unproven_parent_cancellation_has_no_authority_over_owned_target(boot, monkeypatch, fault):
    import json

    boot.activate()
    suite = parent_suite(boot)
    metadata, marker = boot.child.parent / "suite.json", boot.child.parent / "cancel.request"
    if fault == "foreign":
        suite["suite_id"] = str(uuid4())
    elif fault == "wrong_candidate":
        suite["candidate_commit"] = "0" * 40
    elif fault == "wrong_child":
        suite["attempts"][0]["child"] = "foreign-child"
    elif fault == "bool_repeat":
        suite["attempts"][0]["repeat_ordinal"] = True
    if fault == "meta_link":
        metadata.write_text(json.dumps(suite), encoding="utf-8")
        rejected_link(monkeypatch, metadata)
    elif fault not in {"missing", "marker_link"}:
        metadata.write_text("not JSON" if fault == "invalid" else json.dumps(suite), encoding="utf-8")
    if fault == "marker_link":
        marker.write_bytes(b"not an authenticated writer")
        rejected_link(monkeypatch, marker)
    else:
        marker.write_bytes(b"unproven parent marker")
    original = (boot.child / "case-deadline.json").read_bytes()
    for boundary in ("provider", "tool", "sandbox", "submission"):
        boot.guard.check(boundary)
    assert boot.guard.capability()["phase"] == "ACTIVE"
    assert not (boot.data / fence.proof.POISON).exists()
    assert (boot.child / "case-deadline.json").read_bytes() == original


@pytest.mark.parametrize("fault", ["missing", "invalid", "foreign", "meta_link"])
def test_established_parent_authority_damage_remains_fail_closed_without_marker(boot, monkeypatch, fault):
    import json

    boot.activate()
    metadata = boot.child.parent / "suite.json"
    metadata.write_text(json.dumps(parent_suite(boot)), encoding="utf-8")
    assert boot.guard.capability()["phase"] == "ACTIVE"
    metadata.unlink()
    if fault == "invalid":
        metadata.write_bytes(b"bad JSON")
    elif fault == "foreign":
        value = parent_suite(boot)
        value["suite_id"] = str(uuid4())
        metadata.write_text(json.dumps(value), encoding="utf-8")
    elif fault == "meta_link":
        metadata.write_text(json.dumps(parent_suite(boot)), encoding="utf-8")
        rejected_link(monkeypatch, metadata)
    assert boot.guard.capability()["phase"] == "BLOCKED"
    for boundary in ("provider", "tool", "sandbox", "submission"):
        with pytest.raises(RuntimeError, match="not active"):
            boot.guard.check(boundary)
    assert (boot.data / fence.proof.POISON).exists()


def test_established_parent_marker_link_rejection_blocks_after_authority(boot, monkeypatch):
    import json

    boot.activate()
    (boot.child.parent / "suite.json").write_text(json.dumps(parent_suite(boot)), encoding="utf-8")
    assert boot.guard.capability()["phase"] == "ACTIVE"
    marker = boot.child.parent / "cancel.request"
    marker.write_bytes(b"not a supported writer")
    rejected_link(monkeypatch, marker)
    assert boot.guard.capability()["phase"] == "BLOCKED"


def test_cancel_before_activation_survives_all_later_proof_write_failures_and_marker_removal(boot, monkeypatch):
    from evaluation_runner import fixed_case_deadline

    result = boot.child / "result"
    result.mkdir()
    marker = result / "cancel.request"
    marker.write_bytes(b"synthetic cancellation before any binding")
    original = fence.proof.write_once

    def unavailable(path, *args, **kwargs):
        if path.name in {fence.proof.POISON, fence.proof.CLAIM}:
            raise OSError("synthetic persistence unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(fence.proof, "write_once", unavailable)
    assert boot.guard.capability()["phase"] == "BLOCKED"
    assert not (boot.data / fence.proof.POISON).exists() and not (boot.data / fence.proof.CLAIM).exists()
    marker.unlink()
    fixed_case_deadline.seal(boot.child, boot.target, boot.context, boot.namespace)
    boot.clock.ms -= 100
    clone = fence.guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])
    clone.registered = set(boot.guard.registered)
    assert clone.capability()["phase"] == "BLOCKED"
    for boundary in clone.registered:
        with pytest.raises(RuntimeError):
            clone.check(boundary)
    assert (boot.data / fence.guard_module.STARTED).exists()


def test_startup_occupancy_must_persist_before_guard_can_be_constructed(boot, monkeypatch):
    (boot.data / fence.guard_module.STARTED).unlink()
    original = fence.proof.write_once

    def unavailable(path, *args, **kwargs):
        if path.name == fence.guard_module.STARTED:
            raise OSError("synthetic startup persistence unavailable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(fence.proof, "write_once", unavailable)
    with pytest.raises(OSError):
        fence.guard_module.create(boot.settings, boot.required["f05_anchor_sha256"])


def test_unrecognized_child_direct_marker_is_not_a_cancel_writer(boot):
    boot.activate()
    (boot.child / "cancel.request").write_bytes(b"no such supported writer")
    assert boot.guard.capability()["phase"] == "ACTIVE"


def test_nonregular_owned_result_marker_blocks_without_following_external_data(boot):
    boot.activate()
    (boot.child / "result" / "cancel.request").mkdir(parents=True)
    assert boot.guard.capability()["phase"] == "BLOCKED"


@pytest.mark.parametrize("boundary", ["provider", "tool", "sandbox"])
async def test_cancel_during_existing_permission_wait_is_rechecked_before_effect(boot, boundary):
    from fastapi import FastAPI
    from langchain_core.messages import HumanMessage

    boot.activate()
    calls = []

    def cancel():
        result = boot.child / "result"
        result.mkdir()
        (result / "cancel.request").write_bytes(b"cancel during existing permit")

    def provider(case):
        calls.append(case)
        cancel()

    async def effect(value):
        calls.append(value)
        cancel()

    app, models = FastAPI(), fence.target_module.ProfileState()
    app.state.db = SimpleNamespace(evaluation_effect_guard=effect)
    models.parent_effect_guard = provider
    boot.guard.install_runtime(app, models, boot.parent)
    with pytest.raises(RuntimeError, match="not active"):
        if boundary == "provider":
            models.reserve("AET-F05", "lead", [HumanMessage(content="no effect")])
        else:
            await app.state.db.evaluation_effect_guard(boundary)
    assert calls == ["AET-F05" if boundary == "provider" else boundary] and models.roots == {}


@pytest.mark.parametrize("mode", ["owned", "foreign", "wrong_binding"])
async def test_owned_code_observer_retains_scope_permission_and_profile_boundaries(boot, monkeypatch, mode):
    import evaluation_java_observer
    from app.errors import APIError

    run, task, workflow, workflow_run = (uuid4() for _ in range(4))
    proof = {
        "owner_id": boot.target.owner_id if mode != "foreign" else str(uuid4()),
        "environment_id": boot.target.environment_id,
    }
    row = {
        "id": run,
        "task_id": task,
        "owner_id": boot.target.owner_id,
        "model_id": "fixed",
        "status": "COMPLETED",
        "lease_until": None,
        "lease_token": None,
        "cancel_requested": False,
        "workflow_id": workflow,
        "workflow_run_id": workflow_run,
        "workflow_status": "SUCCEEDED",
        "snapshot_id": uuid4(),
        "namespace": boot.namespace,
        "snapshot": {"definition": code.definition()},
    }
    permissions, inventories = [], []

    async def environment(request):
        return proof

    async def permission(db, owner, name):
        permissions.append((owner, name))

    async def inventory(*args):
        inventories.append(True)
        return {"complete": True, "items": []}

    class DB:
        async def fetch(self, query, args):
            if query.startswith("SELECT r.id"):
                assert args == (run, proof["owner_id"])
                return [row] if str(args[1]) == row["owner_id"] else []
            if query.startswith("SELECT id,run_id"):
                assert args == (proof["owner_id"],)
                return []
            return [
                dict.fromkeys(("work_runs", "workflow_runs", "model_calls", "unknown_operations", "active_runs"), 0)
            ]

    profiles = SimpleNamespace(
        lock=Lock(),
        models={
            "fixed": {
                "case_id": "AET-W05" if mode == "wrong_binding" else "AET-F05",
                "role": "lead",
                "owner_id": boot.target.owner_id,
            }
        },
        case_summary=lambda *args: {
            "observed": True,
            "complete": True,
            "calls": 0,
            "tokens": 0,
            "pending": 0,
            "unknown_roots": 0,
        },
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                db=DB(),
                evaluation_profiles=profiles,
                evaluation_bounded_sandboxes=SimpleNamespace(attempts=0, storage_failed=False),
            )
        )
    )
    monkeypatch.setattr(code, "environment", environment)
    monkeypatch.setattr(code, "require_account", permission)
    monkeypatch.setattr(evaluation_java_observer, "inventory", inventory)
    if mode != "owned":
        with pytest.raises(APIError) as error:
            await code.observe(request, run)
        assert error.value.status == (404 if mode == "foreign" else 403)
        assert inventories == []
    else:
        result = await code.observe(request, run)
        assert result["owner_id"] == proof["owner_id"] and result["run_id"] == run
        assert (
            result["task_id"] == task
            and result["workflow_id"] == workflow
            and result["workflow_run_id"] == workflow_run
        )
        assert result["provider"]["calls"] == 0 and result["sessions_complete"] is True
        assert inventories == [True]
    assert permissions == [(proof["owner_id"], "workflows.read"), (proof["owner_id"], "work.read")]
