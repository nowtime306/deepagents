"""Private source-copy import + MockTransport only. Does not execute an Agent/Java/target."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from test_fixed_java_client import report

import evaluation_runner
from evaluation_runner import fixed_audit
from evaluation_runner import fixed_java_client as java
from evaluation_runner.client import Platform
from evaluation_runner.journal import atomic_json


@pytest.fixture
def linked(monkeypatch):
    path = Path(evaluation_runner.__file__).with_name("fixed_run.py")
    name = "evaluation_runner._private_w02_fixed_run"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setitem(sys.modules, "evaluation_runner.fixed_java_client", java)
    monkeypatch.setattr(evaluation_runner, "fixed_java_client", java, raising=False)
    spec.loader.exec_module(module)
    # The ready reader imports the reviewed fixed_run entry point. Only this test process sees the copy.
    monkeypatch.setitem(sys.modules, "evaluation_runner.fixed_run", module)
    monkeypatch.setattr(evaluation_runner, "fixed_run", module, raising=False)
    monkeypatch.setattr(java, "PROGRAM_SHA", "a" * 64)
    return module


def fixture(tmp_path, monkeypatch, module, *, fault=None, functional_fail=False, unchanged=False, routine_events=False):
    from evaluation_runner.contracts import Target

    ids = {name: str(uuid4()) for name in ("environment", "owner", "namespace", "task", "verifier-task", "source", "verifier", "artifact", "snapshot", "session-source", "session-verifier", "sandbox-source", "sandbox-verifier", "input")}
    ids["model"] = "sealed-w02-model"
    target = Target(environment_id=ids["environment"], owner_id=ids["owner"], api_base_url="http://127.0.0.1:1",
                    platform_commit="a" * 40, required_migrations=[], fixture_profile={"id": "controlled-v1", "version": 1})
    caps = {"observers": ["root-model-context-v1"], "work_manager": {"ready": True},
        "sandbox_limits": {"enforcement": "controlled-sandbox-reserve-v1", "scope": "test-environment", "max_created": 2, "attempts": 0, "storage_healthy": True},
        "java_verifier": {"protocol": "java-max-v2", "initialized": True, "program_sha256": "a" * 64,
            "launcher_sha256": java.LAUNCHER_SHA, "tests_sha256": java.TESTS_SHA, "observer": "java-verifier-owned-v1"},
        "case_limits": {"schema_version": 1, "enforcement": "controlled-case-reserve-v1", "scope": "evaluation-owner-and-case",
                        "profiles": {"AET-W02": {"max_model_calls": 6, "max_tokens": 90, "max_roots": 2}}}}
    environment = {"environment_id": ids["environment"], "owner_id": ids["owner"], "platform_commit": target.platform_commit,
        "fixture_profile": target.fixture_profile.model_dump(), "database_is_test": True, "schema_migrations": [],
        "sandbox_namespace": ids["namespace"], "permissions": ["evaluation.read", "evaluation.run", "evaluation.manage", "work.read", "work.manage", "work.execute"]}
    source = (java.ORIGINAL if unchanged else java.ORIGINAL.replace("return a;", "return a>b?a:b;")).encode()
    checksum = java.sha(source)
    protocol = report()
    protocol["source_sha256"] = checksum
    if functional_fail:
        protocol["cases"][0].update(exit_code=125, passed=False)
        protocol["passed"] = False
    source_result = {"snapshot_id": ids["snapshot"]}
    final_result = {"case_id": "AET-W02", "verifier_run_id": ids["verifier"], "source_run_id": ids["source"],
                    "artifact_id": ids["artifact"], "source_sha256": checksum, "verification": protocol}
    state, requests, output = {"created": 0, "verifier": False}, [], tmp_path / "output"

    def counts(calls):
        return {"calls": calls, "reported_calls": calls, "unknown_calls": 0, "pending_calls": 0, "input_tokens": calls * 10, "output_tokens": calls * 5}

    def observation():
        final = state["verifier"]
        value = {"schema_version": 1, "case_id": "AET-W02", "environment_id": ids["environment"], "owner_id": ids["owner"],
            "namespace": ids["namespace"], "phase": "COMPLETED" if final else "REGISTERED", "complete": True,
            "binding": {"source_run_id": ids["source"], "source_task_id": ids["task"], "source_snapshot_id": ids["snapshot"],
                "source_model_id": ids["model"], "artifact_id": ids["artifact"], "source_sha256": checksum,
                "verifier_task_id": ids["verifier-task"], "verifier_run_id": ids["verifier"] if final else None,
                "program_sha256": "a" * 64, "launcher_sha256": java.LAUNCHER_SHA, "tests_sha256": java.TESTS_SHA},
            "zero_verifier": {"observed": final, "confirmed": final}, "native": {"source": counts(3), "verifier": counts(0) if final else None},
            "case_provider": {"observed": True, "complete": True, "calls": 3, "tokens": 45, "pending": 0, "unknown_roots": 0},
            "namespace_inventory": {"complete": True, "count": 0}, "sessions": [{"session_id": ids["session-source"],
                "run_id": ids["source"], "owner_id": ids["owner"], "namespace": ids["namespace"], "sandbox_id": ids["sandbox-source"], "state": "RELEASED"}]}
        if final:
            value["sessions"].append({"session_id": ids["session-verifier"], "run_id": ids["verifier"], "owner_id": ids["owner"],
                "namespace": ids["namespace"], "sandbox_id": ids["sandbox-verifier"], "state": "RELEASED"})
        if fault == "missing-zero" and final:
            value["zero_verifier"] = {"observed": False, "confirmed": False}
        return value

    def server(request):
        path, method = request.url.path, request.method
        if path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json=caps)
        if path.endswith("/work-capabilities"):
            return httpx.Response(200, json={"enabled": True})
        if method == "POST":
            requests.append((path, request.content))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": ids["model"]})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/work-tasks":
                task = ids["task"] if state["created"] == 0 else ids["verifier-task"]
                state["created"] += 1
                return httpx.Response(201, json={"id": task})
            if path.endswith("/files"):
                assert request.content == java.ORIGINAL.encode() and dict(request.url.params) == {"path": "/Max.java", "revision": "1"}
                return httpx.Response(201, json={"id": ids["input"], "revision": 2})
            body = json.loads(request.content)
            if path == java.PATH:
                saved = json.loads((output / "journal.json").read_text(encoding="utf-8"))["attempts"][0]
                assert saved["java_candidate_ref"]
                assert body == {"task_id": ids["verifier-task"], "source_run_id": ids["source"], "artifact_id": ids["artifact"], "source_sha256": checksum}
                if fault == "unknown-register":
                    raise httpx.ReadError("Receiver may have sealed the task; no replay")
                return httpx.Response(200, json={"task_id": ids["verifier-task"], "sealed": True, "source_sha256": checksum,
                    "program_sha256": "a" * 64, "tests_sha256": java.TESTS_SHA})
            if path.endswith("/runs"):
                is_verifier = ids["verifier-task"] in path
                assert body["model_id"] == ids["model"] and body["revision"] == (1 if is_verifier else 2)
                if is_verifier:
                    saved = json.loads((output / "journal.json").read_text(encoding="utf-8"))["attempts"][0]
                    assert saved["java_registration_ref"]
                    state["verifier"] = True
                    if fault == "unknown-verifier":
                        raise httpx.ReadError("Second run may exist; never submit again")
                return httpx.Response(202, json={"run_id": ids["verifier" if is_verifier else "source"], "status": "PENDING"})
            raise AssertionError(path)
        if path == f"/api/v1/work-tasks/{ids['verifier-task']}/files":
            return httpx.Response(200, json={"revision": 1, "items": []})
        if path == java.PATH + "/" + ids["verifier-task"] + "/observations":
            value = observation()
            state["observations"] = state.get("observations", 0) + 1
            if fault == "late-unknown" and state["observations"] >= 2:
                value["complete"] = False
                value["case_provider"].update(complete=False, pending=1)
            return httpx.Response(200, json=value)
        if path.endswith("/download"):
            return httpx.Response(200, content=source)
        child = ids["verifier"] in path
        run, task = ids["verifier" if child else "source"], ids["verifier-task" if child else "task"]
        if path.endswith("/artifacts"):
            return httpx.Response(200, json={"items": [{"id": ids["artifact"], "path": "/Max.java", "size": len(source), "digest": checksum}]})
        if path.endswith("/operations"):
            return httpx.Response(200, json={"items": []})
        if path.endswith("/usage"):
            if fault == "unknown-source" and not child:
                return httpx.Response(200, json={"total": {**counts(3), "reported_calls": 2, "unknown_calls": 1, "input_tokens": None, "output_tokens": None}})
            return httpx.Response(200, json={"total": counts(0 if child else 3)})
        if path.endswith("/observations"):
            return httpx.Response(200, json={"sandbox_sessions": {"complete": True, "states": {"RELEASED": 1}}})
        if path.endswith("/configurations"):
            return httpx.Response(200, json={"configuration_scope": {"mode": "work", "run_id": run}, "items": [{"resource_kind": "model",
                "resource_id": ids["model"], "snapshot_id": str(uuid5(UUID(run), "model")), "revision": "1", "used_at": None if child else "2026-11-04T10:00:00+00:00",
                "calls": [] if child else [{"call_id": str(uuid5(UUID(run), str(index))), "run_id": run, "mode": "work", "model_id": ids["model"],
                    "model_revision": 1, "status": "KNOWN", "input_tokens": 10, "output_tokens": 5} for index in range(3)]}]} )
        if path.endswith("/events"):
            rows = [{"event": "run.started", "data": {}}]
            if not child:
                if routine_events:
                    rows.extend({"event": name, "data": {"text": "private-routine-event-payload"}}
                                for name in ("message.delta", "step.updated", "plan.updated"))
                for index, name in enumerate(("read_file", "edit_file"), 1):
                    for event in ("tool.requested", "tool.completed"):
                        rows.append({"event": event, "data": {"name": name, "call_id": "ae-" + str(index) * 16,
                                                             "output": "private dummy tool response", "truncated": False}})
            rows.append({"event": "run.completed", "data": {}})
            rows = [dict(row, event_id=index) for index, row in enumerate(rows, 1)]
            cursor = int(request.url.params["after"])
            following = [row for row in rows if row["event_id"] > cursor]
            return httpx.Response(200, json={"items": following, "next_after": rows[-1]["event_id"]})
        if path == "/api/v1/work-runs/" + run:
            return httpx.Response(200, json={"id": run, "task_id": task, "owner_id": ids["owner"], "model_id": ids["model"],
                "task_revision": 1 if child else 2, "status": "COMPLETED", "environment": "coding", "result": final_result if child else source_result})
        raise AssertionError((method, path))

    monkeypatch.setattr(module, "Platform", lambda value: Platform(value, transport=httpx.MockTransport(server)))
    monkeypatch.setattr(Platform, "login", lambda _: None)
    manifest = tmp_path / "target.json"
    atomic_json(manifest, target.model_dump())

    def command(action):
        monkeypatch.setattr(sys, "argv", ["private-w02", action, "--case-id", "AET-W02", "--output", str(output), "--target-manifest", str(manifest)])
        return module.main()

    return command, output, requests, module, ids


def test_private_full_source_copy_pipeline_confirmed_intents_zero_model_and_ready(tmp_path, monkeypatch, linked):
    command, output, requests, module, ids = fixture(tmp_path, monkeypatch, linked, routine_events=True)
    assert command("init") == command("run") == command("cleanup") == command("report") == 0
    assert fixed_audit.validate_ready(output)["gate"] == "PASS"
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    assert attempt["usage"] == {"calls": 3, "reported_calls": 3, "unknown_calls": 0, "pending_calls": 0, "input_tokens": 30, "output_tokens": 15}
    assert attempt["cleanup_status"] == "CLEAN" and attempt["fixture"]["revision"] == 2
    assert [row["registered_id"] for row in attempt["fact_results"]] == ["protected_tests"]
    assert attempt["java_verifier_root"]["fixture_id"] == ids["verifier-task"]
    assert len([path for path, _ in requests if path.endswith("/runs")]) == 2
    assert java.cleanup_ready(output, data, attempt, next(case for case in module.catalog().cases if case.case_id == "AET-W02"))
    specimen = next(case for case in module.catalog().cases if case.case_id == "AET-W02")
    snapshot = java.verified(output, data, attempt, specimen)
    assert {"message.delta", "step.updated", "plan.updated"}.issubset({row["event"] for row in snapshot["candidate"]["root"]["events"]})
    assert "private-routine-event-payload" not in json.dumps(snapshot)


@pytest.mark.parametrize("fault", ["unknown-register", "unknown-verifier", "missing-zero"])
def test_private_unknown_or_unproven_zero_never_replays_or_claims_clean(tmp_path, monkeypatch, linked, fault):
    command, output, requests, _, _ = fixture(tmp_path, monkeypatch, linked, fault=fault)
    assert command("init") == 0
    assert command("run") != 0 and command("run") != 0 and command("cleanup") != 0
    assert len([path for path, _ in requests if path == java.PATH]) == 1
    assert len([path for path, _ in requests if path.endswith("/runs")]) == (1 if fault == "unknown-register" else 2)
    with pytest.raises(ValueError):
        fixed_audit.validate_ready(output)


def test_private_known_functional_failure_keeps_fail_but_allows_settled_cleanup(tmp_path, monkeypatch, linked):
    command, output, _, _, _ = fixture(tmp_path, monkeypatch, linked, functional_fail=True)
    assert command("init") == 0 and command("run") == 1 and command("cleanup") == 0 and command("report") == 1
    assert fixed_audit.validate_ready(output)["gate"] == "FAIL"


def test_private_source_change_is_required_even_if_a_claimed_receipt_says_pass(tmp_path, monkeypatch, linked):
    command, output, _, _, _ = fixture(tmp_path, monkeypatch, linked, unchanged=True)
    assert command("init") == 0 and command("run") == 1 and command("cleanup") == 0 and command("report") == 1
    assert fixed_audit.validate_ready(output)["gate"] == "FAIL"


@pytest.mark.parametrize("change", ["intent", "model", "zero", "session", "native", "inventory", "source"])
def test_private_offline_clean_requires_original_intents_and_same_source_zero_and_sessions(tmp_path, monkeypatch, linked, change):
    command, output, _, module, _ = fixture(tmp_path, monkeypatch, linked)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in module.catalog().cases if row.case_id == "AET-W02")
    snapshot = deepcopy(java.verified(output, data, attempt, specimen))
    if change == "intent":
        attempt["submission_intents"]["submit-java-verifier"]["status"] = "UNKNOWN"
    elif change == "model":
        snapshot["verifier"]["model_id"] = "different-alias-same-model"
    elif change == "zero":
        snapshot["observer"]["zero_verifier"]["observed"] = False
    elif change == "session":
        snapshot["observer"]["sessions"][1]["session_id"] = snapshot["observer"]["sessions"][0]["session_id"]
    elif change == "native":
        snapshot["verifier"]["usage"]["input_tokens"] = None
    elif change == "inventory":
        snapshot["observer"]["namespace_inventory"]["complete"] = False
    else:
        snapshot["candidate"]["source_utf8"] += "//changed"
    with pytest.raises((ValueError, module.adapters.Blocked)):
        java.evaluate(snapshot, specimen, attempt)


@pytest.mark.parametrize("intent", ["input-0", "java-verifier-task", "register-java-verifier", "submit-run", "submit-java-verifier"])
def test_private_every_unknown_original_intent_invalidates_offline_clean(tmp_path, monkeypatch, linked, intent):
    command, output, _, module, _ = fixture(tmp_path, monkeypatch, linked)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    collection = "submission_intents" if intent.startswith("submit-") else "fixture_intents"
    attempt[collection][intent]["status"] = "UNKNOWN"
    specimen = next(row for row in module.catalog().cases if row.case_id == "AET-W02")
    with pytest.raises(module.adapters.Blocked):
        module.validate_offline_report(data, specimen, module.catalog(), directory=output)
    with pytest.raises(module.adapters.Blocked):
        java.cleanup_ready(output, data, attempt, specimen)


def test_private_evidence_save_failure_never_dispatches_second_root_or_reuses_pending_audit(tmp_path, monkeypatch, linked):
    from evaluation_runner.journal import Journal

    command, _, requests, _, _ = fixture(tmp_path, monkeypatch, linked)
    assert command("init") == 0
    original = Journal.save

    def broken(self):
        if self.data["attempts"][0].get("java_registration_ref"):
            raise OSError("private simulated storage failure")
        original(self)

    with monkeypatch.context() as patch:
        patch.setattr(Journal, "save", broken)
        with pytest.raises(OSError):
            command("run")
    assert len([path for path, _ in requests if path.endswith("/runs")]) == 1
    count = len(requests)
    assert command("run") != 0
    assert len(requests) == count  # The previous unsealed audit is rejected before any new mutation.


@pytest.mark.parametrize("fault", ["late-unknown", "unknown-source"])
def test_private_unknown_case_or_native_usage_stops_before_second_work_dispatch(tmp_path, monkeypatch, linked, fault):
    command, _, requests, _, _ = fixture(tmp_path, monkeypatch, linked, fault=fault)
    assert command("init") == 0 and command("run") != 0
    assert len([path for path, _ in requests if path.endswith("/runs")]) == 1


def test_private_known_source_failure_survives_later_unknown_resource_proof(tmp_path, monkeypatch, linked):
    command, output, _, _, _ = fixture(tmp_path, monkeypatch, linked, fault="late-unknown", unchanged=True)
    assert command("init") == 0 and command("run") != 0
    attempt = json.loads((output / "journal.json").read_text(encoding="utf-8"))["attempts"][0]
    assert attempt["task_outcome"] == "FAIL" and attempt["fact_results"][0]["status"] == "FAIL"
    assert attempt["cleanup_status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("root_name", ["candidate", "verifier"])
@pytest.mark.parametrize("effect", ["operation", "event"])
def test_fixed_two_roots_reject_extra_known_success_or_unregistered_event(tmp_path, monkeypatch, linked, root_name, effect):
    command, output, _, module, _ = fixture(tmp_path, monkeypatch, linked)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in module.catalog().cases if row.case_id == "AET-W02")
    snapshot = deepcopy(java.verified(output, data, attempt, specimen))
    root = snapshot["candidate"]["root"] if root_name == "candidate" else snapshot["verifier"]
    if effect == "operation":
        root["operations"].append({"id": str(uuid4()), "name": "execute", "status": "SUCCEEDED"})
    else:
        root["events"].insert(-1, {"event_id": len(root["events"]), "event": "repository.pushed"})
        root["events"][-1]["event_id"] += 1
    with pytest.raises((ValueError, module.adapters.Blocked)):
        java.evaluate(snapshot, specimen, attempt)


@pytest.mark.parametrize("change", ["completed-before-requested", "edit-before-read-completed", "duplicate-call-id"])
def test_fixed_serial_read_edit_requires_real_id_order_and_unique_calls(tmp_path, monkeypatch, linked, change):
    command, output, _, module, _ = fixture(tmp_path, monkeypatch, linked)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in module.catalog().cases if row.case_id == "AET-W02")
    snapshot = deepcopy(java.verified(output, data, attempt, specimen))
    events = snapshot["candidate"]["root"]["events"]
    if change == "completed-before-requested":
        events[1], events[2] = events[2], events[1]
    elif change == "edit-before-read-completed":
        events[2], events[3] = events[3], events[2]
    else:
        events[3]["call_id"] = events[4]["call_id"] = events[1]["call_id"]
    for index, row in enumerate(events, 1):
        row["event_id"] = index
    with pytest.raises((ValueError, module.adapters.Blocked)):
        java.evaluate(snapshot, specimen, attempt)
