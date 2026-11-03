import json
import sys
from copy import deepcopy
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from test_core import attestation, target

from evaluation_runner import fixed_audit, fixed_automation_evidence, fixed_evidence, fixed_run
from evaluation_runner.client import Platform
from evaluation_runner.journal import atomic_json


def harness(tmp_path, monkeypatch, *, lost_step=False, profile_fault=None):
    value = target().model_copy(update={"resource_bindings": {"AET-A01": "bootstrap"}})
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    identifiers = {key: str(uuid4()) for key in ("model", "agent", "version", "conversation", "run", "child", "child-conversation", "occurrence", "namespace")}
    identifiers["automation"] = str(uuid5(UUID(identifiers["run"]), "ae-2222222222222222"))
    output = tmp_path / "output"
    calls, state = [], {"model_revision": 1, "enabled": False, "stepped": False}
    proof = attestation(value)
    proof.update(sandbox_namespace=identifiers["namespace"], permissions=["evaluation.read", "evaluation.manage", "evaluation.run",
        "agents.manage", "chat.read", "chat.manage", "chat.run", "automation.read", "automation.manage", "automation.execute"])
    proof["execution_limits"]["profiles"]["AET-A01"] = {"mode": "ask", "max_model_calls": 4, "max_tokens": 60}
    server_time = "2026-11-04T01:00:05+00:00"
    schedule = {"id": identifiers["automation"], "agent_version_id": identifiers["version"], "mode": "ask", "kind": "once",
        "goal": "整理虚拟日报", "timezone": "Asia/Shanghai", "starts_at": "2026-11-04T01:00:00+00:00", "enabled": False,
        "revision": 1, "next_due_at": "2026-11-04T01:00:00+00:00", "completed_at": None,
        "budget": {"model_calls": 4, "token_limit": 10000}, "missed_policy": "once", "overlap_policy": "skip",
        "notify_channel_ids": [], "network_domains": []}

    def total(count):
        return {"calls": count, "reported_calls": count, "unknown_calls": 0, "pending_calls": 0,
                "input_tokens": count * 10, "output_tokens": count * 5}

    def occurrence():
        return {"id": identifiers["occurrence"], "status": "DISPATCHED", "ask_run_id": identifiers["child"], "work_run_id": None,
                "target_id": identifiers["child-conversation"], "result_status": "SUCCEEDED", "error_code": None}

    def messages(child):
        root = identifiers["child" if child else "run"]
        rows = [{"role": "user", "run_id": root, "content": {"text": ("计划时刻：2026-11-04T01:00:00+00:00；任务时区：Asia/Shanghai。\n\n整理虚拟日报" if child else specimen.input)}}]
        if not child:
            for name, call_id, result in [("list_automation_agents", "ae-1111111111111111", {"items": [{"id": identifiers["version"], "version": 1}], "current_time": server_time}),
                ("create_automation_draft", "ae-2222222222222222", {"type": "automation_draft", "id": identifiers["automation"], "enabled": False})]:
                rows.extend([{"role": "assistant", "run_id": root, "content": {"data": {"content": "", "tool_calls": [{"id": call_id, "name": name}]}}},
                    {"role": "tool", "run_id": root, "content": {"data": {"content": json.dumps(result), "name": name, "tool_call_id": call_id, "status": "success"}}}])
        rows.append({"role": "assistant", "run_id": root, "content": {"data": {"content": "虚拟日报已整理。" if child else "草稿已创建，等待启用。", "tool_calls": []}}})
        return {"items": rows, "next_before": None}

    def profile():
        roots = [{"mode": "ask", "run_id": identifiers["run"], "phase": "draft", "calls": 3, "tokens": 45}]
        native = [{"run_id": identifiers["run"], "status": "SUCCEEDED", **total(3)}]
        if state["stepped"]:
            roots.append({"mode": "ask", "run_id": identifiers["child"], "phase": "occurrence", "calls": 1, "tokens": 15})
            native.append({"run_id": identifiers["child"], "status": "SUCCEEDED", **total(1)})
        return {"schema_version": 1, "environment_id": value.environment_id, "owner_id": value.owner_id,
            "namespace": identifiers["namespace"], "automation_id": identifiers["automation"], "source_run_id": identifiers["run"],
            "agent_version_id": identifiers["version"], **{key: schedule[key] for key in ("enabled", "next_due_at", "completed_at", "revision", "budget")},
            "occurrences": [{key: val for key, val in occurrence().items() if key != "work_run_id"}] if state["stepped"] else [],
            "native": native, "provider": {"observed": True, "complete": True, "calls": 4 if state["stepped"] else 3,
                "tokens": 60 if state["stepped"] else 45, "pending": 0, "unknown_roots": 0, "roots": roots},
            "counts": {"active_ask_runs": 0, "work_runs": 0, "unsettled_usage": 0}}

    def transport(request):
        path, method = request.url.path, request.method
        if path.endswith("/environment"):
            return httpx.Response(200, json=proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1", "single-automation-lifecycle-v1"], "case_limits": {
                "schema_version": 1, "enforcement": "controlled-case-reserve-v1", "scope": "evaluation-owner-and-case",
                "profiles": {"AET-A01": {"max_model_calls": 8, "max_tokens": 120, "max_roots": 2}}}})
        if method in {"POST", "PUT"}:
            body = json.loads(request.content)
            calls.append((method, path, body))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": identifiers["model"]})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/agents":
                return httpx.Response(201, json={"id": identifiers["agent"]})
            if path.endswith("/publish"):
                assert body == {"revision": 1} and state["model_revision"] == 1
                return httpx.Response(201, json={"id": identifiers["version"]})
            if method == "PUT" and path == "/api/v1/admin/models/" + identifiers["model"]:
                assert body["revision"] == 1 and body["temperature"] == 0.4
                state["model_revision"] = 2
                return httpx.Response(200, json={"revision": 2})
            if path == "/api/v1/conversations":
                assert body["agent_version_id"] == identifiers["version"] and len(body) == 3
                return httpx.Response(201, json={"id": identifiers["conversation"]})
            if path.endswith("/runs"):
                assert body["message"] == specimen.input
                return httpx.Response(202, json={"run_id": identifiers["run"]})
            if path.endswith("/state"):
                data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
                assert data["attempts"][0]["automation_draft_ref"]  # Retained before a new mutation.
                schedule.update(enabled=True, revision=2)
                return httpx.Response(200, json={"enabled": True})
            if path.endswith("/step"):
                data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
                assert data["attempts"][0]["automation_enabled_ref"]
                state["stepped"] = True
                schedule.update(enabled=False, next_due_at=None, completed_at=server_time)
                if lost_step:
                    raise httpx.ReadError("Known receiver committed but the caller cannot know")
                return httpx.Response(200, json=profile())
            raise AssertionError((method, path))
        if path.endswith("/versions"):
            return httpx.Response(200, json={"items": [{"id": identifiers["version"], "version": 1,
                "snapshot": {"model_id": identifiers["model"], "model_revision": 1}}]})
        if path == "/api/v1/admin/models":
            return httpx.Response(200, json={"items": [{"id": identifiers["model"], "revision": state["model_revision"], "temperature": 0.4}]})
        if path == "/api/v1/automations":
            return httpx.Response(200, json={"items": [schedule]})
        if path.endswith("/occurrences"):
            return httpx.Response(200, json={"items": [occurrence()] if state["stepped"] else [], "next_cursor": None})
        if "/profiles/automations/" in path:
            body = profile()
            if profile_fault and not state["stepped"] and schedule["enabled"] == (profile_fault[0] == "enabled"):
                body["provider"][profile_fault[1]] = 999 if profile_fault[1] == "tokens" else 1
            return httpx.Response(200, json=body)
        child = identifiers["child"] in path or identifiers["child-conversation"] in path
        root = identifiers["child" if child else "run"]
        if path.endswith("/messages"):
            return httpx.Response(200, json=messages(child))
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": total(1 if child else 3)})
        if path.endswith("/configurations"):
            return httpx.Response(200, json={"mode": "ask", "run_id": root, "configuration_scope": {"mode": "ask", "run_id": root},
                "business_version": {"published_version_id": identifiers["version"], "published_version": 1}, "items": [{
                    "resource_kind": "model", "resource_id": identifiers["model"], "snapshot_id": str(uuid5(UUID(root), "snapshot")),
                    "revision": "2", "used_at": server_time, "snapshot": {"temperature": 0.4}, "calls": [{
                        "call_id": str(uuid5(UUID(root), str(index))), "run_id": root, "mode": "ask", "model_id": identifiers["model"],
                        "model_revision": 2, "status": "KNOWN", "input_tokens": 10, "output_tokens": 5} for index in range(1 if child else 3)]}]} )
        if path.endswith("/events"):
            rows = [{"event": "run.started", "data": {}}]
            if not child:
                for name, call_id in [("list_automation_agents", "ae-1111111111111111"), ("create_automation_draft", "ae-2222222222222222")]:
                    rows.extend({"event": event, "data": {"name": name, "call_id": call_id}} for event in ("tool.started", "tool.completed"))
            rows.append({"event": "run.completed", "data": {}})
            frames = [dict(row, run_id=root, event_id=index) for index, row in enumerate(rows, 1)]
            frames.append({"event": "stream.end", "data": {"status": "SUCCEEDED"}})
            return httpx.Response(200, text="".join("data: " + json.dumps(row) + "\n\n" for row in frames))
        if path == "/api/v1/runs/" + root:
            return httpx.Response(200, json={"id": root, "status": "SUCCEEDED", "conversation_id": identifiers["child-conversation" if child else "conversation"],
                "model_id": identifiers["model"], "origin": "schedule" if child else "interactive", "automation_occurrence_id": identifiers["occurrence"] if child else None})
        raise AssertionError((method, path))

    monkeypatch.setattr(fixed_run, "IMPLEMENTED", fixed_run.IMPLEMENTED | {"AET-A01"})
    monkeypatch.setattr(fixed_run, "Platform", lambda val: Platform(val, transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(Platform, "login", lambda _: None)
    manifest = tmp_path / "target.json"
    atomic_json(manifest, value.model_dump())

    def command(action):
        monkeypatch.setattr(sys, "argv", ["fixed_run", action, "--case-id", "AET-A01", "--output", str(output), "--target-manifest", str(manifest)])
        return fixed_run.main()

    return command, output, calls, identifiers, proof


def test_a01_formal_http_lifecycle_retains_recomputable_ask_and_occurrence_before_clean(tmp_path, monkeypatch):
    command, output, calls, identifiers, _ = harness(tmp_path, monkeypatch)
    assert command("init") == 0
    assert command("run") == 0
    assert command("cleanup") == 0
    assert command("report") == 0
    ready = fixed_audit.validate_ready(output)
    assert ready["gate"] == "PASS"
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    assert attempt["usage"]["calls"] == 4 and attempt["usage"]["input_tokens"] + attempt["usage"]["output_tokens"] == 60
    assert attempt["scheduled_root"]["id"] == identifiers["child"]
    assert attempt["metric_results"] == [] and attempt["cleanup_status"] == "CLEAN"
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    snapshot = fixed_automation_evidence.verified(output, data, attempt, specimen)
    assert fixed_automation_evidence.evaluate(snapshot, specimen, attempt)[0][0].status == "PASS"
    assert type(snapshot["body"]["draft"]["source"]["model"]["revision"]) is str
    assert snapshot["body"]["draft"]["source"]["model"]["revision"] == "2"
    assert all(type(row["model_revision"]) is int and row["model_revision"] == 2
               for row in snapshot["body"]["draft"]["source"]["model"]["calls"])
    assert len(calls) == 9 and sum(path.endswith("/step") for _, path, _ in calls) == 1
    audit = [fixed_evidence.load(output, ref, "network-audit") for ref in ready["network_audit_refs"]]
    assert {phase["name"] for phase in audit[1]["phases"]} == {"execution", "export"}
    assert identifiers["automation"] in json.dumps(snapshot, ensure_ascii=False)


def test_unknown_dispatch_is_not_reposted_and_cannot_clean_or_publish_pass(tmp_path, monkeypatch):
    command, output, calls, _, _ = harness(tmp_path, monkeypatch, lost_step=True)
    assert command("init") == 0
    assert command("run") != 0
    assert command("run") != 0
    assert command("cleanup") != 0
    assert sum(path.endswith("/step") for _, path, _ in calls) == 1
    with pytest.raises(ValueError):
        fixed_audit.validate_ready(output)  # A later failed command invalidates the earlier export receipt.


def test_namespace_drift_between_init_and_submit_has_zero_new_business_posts(tmp_path, monkeypatch):
    command, _, calls, _, proof = harness(tmp_path, monkeypatch)
    assert command("init") == 0
    count = len(calls)
    proof["sandbox_namespace"] = str(uuid4())
    assert command("run") != 0
    assert len(calls) == count


@pytest.mark.parametrize("change", ["missing_events", "no_used_snapshot", "wrong_revision", "boolean_revision", "extra_raw_payload", "wrong_child_root"])
def test_retained_a01_cannot_pass_with_incomplete_or_rebound_source_facts(tmp_path, monkeypatch, change):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    snapshot = deepcopy(fixed_automation_evidence.verified(output, data, attempt, specimen))
    execution = snapshot["body"]["execution"]
    if change == "missing_events":
        execution["events"]["complete"] = False
    elif change == "no_used_snapshot":
        execution["model"]["used_at"] = None
    elif change == "wrong_revision":
        execution["model"]["revision"] = 1
    elif change == "boolean_revision":
        snapshot["body"]["draft"]["initialization"]["version"] = True
    elif change == "extra_raw_payload":
        execution["messages"][0]["raw_tool_secret"] = "unretained-canary"
    else:
        execution["run_id"] = str(uuid4())
    with pytest.raises((ValueError, fixed_run.adapters.Blocked)):
        fixed_automation_evidence.evaluate(snapshot, specimen, attempt)


@pytest.mark.parametrize("change", ["wrong_goal", "extra_effect", "wrong_native", "unknown", "unsettled_schedule", "wrong_occurrence"])
def test_stable_but_wrong_execution_is_not_an_automation_success(tmp_path, monkeypatch, change):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    snapshot = deepcopy(fixed_automation_evidence.verified(output, data, attempt, specimen))
    body = snapshot["body"]
    if change == "wrong_goal":
        body["execution"]["messages"][0]["text_digest"] = "0" * 64
    elif change == "extra_effect":
        body["profile"]["counts"]["work_runs"] = 1
    elif change == "wrong_native":
        body["profile"]["native"][-1]["input_tokens"] = 999
    elif change == "unknown":
        body["profile"]["provider"]["complete"] = False
        body["profile"]["provider"]["unknown_roots"] = 1
    elif change == "unsettled_schedule":
        body["schedule"]["enabled"] = True
    else:
        body["profile"]["occurrences"][0]["ask_run_id"] = str(uuid4())
    facts, _ = fixed_automation_evidence.evaluate(snapshot, specimen, attempt)
    assert facts[0].status == "FAIL"


def test_fact_persistence_failure_poison_prevents_enable_and_dispatch(tmp_path, monkeypatch):
    from evaluation_runner.journal import Journal

    command, output, calls, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == 0
    original = Journal.save

    def fail_draft(self):
        if self.data.get("attempts") and self.data["attempts"][0].get("automation_draft_ref"):
            raise OSError("test disk error")
        original(self)

    with monkeypatch.context() as patch:
        patch.setattr(Journal, "save", fail_draft)
        with pytest.raises(OSError):
            command("run")  # Even safe failure journaling cannot complete; caller must retain the error.
    assert not any(path.endswith(("/state", "/step")) for _, path, _ in calls)
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    assert not data["attempts"][0].get("automation_draft_ref")


@pytest.mark.parametrize("kind", ["submit-run", "enable-automation", "step-automation"])
@pytest.mark.parametrize("change", ["unknown", "path", "payload"])
def test_offline_and_cleanup_cannot_borrow_confirmed_facts_for_unknown_or_different_write(tmp_path, monkeypatch, kind, change):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    collection = "submission_intents" if kind == "submit-run" else "fixture_intents"
    intent = attempt[collection][kind]
    if change == "unknown":
        intent["status"] = "UNKNOWN"
    elif change == "path":
        intent["path"] += "/different"
    else:
        intent["payload_digest" if kind == "submit-run" else "digest"] = "0" * 64
    with pytest.raises(fixed_run.adapters.Blocked):
        fixed_run.validate_offline_report(data, specimen, fixed_run.catalog(), directory=output)
    with pytest.raises(fixed_run.adapters.Blocked):
        fixed_automation_evidence.cleanup_ready(output, data, attempt, specimen)


@pytest.mark.parametrize("stage, change", [("draft", "tokens"), ("enabled", "pending"), ("enabled", "unknown_roots"),
    ("draft", "native"), ("enabled", "root"), ("enabled", "calls")])
def test_early_unreconciled_budget_blocks_the_next_mutation_and_cannot_be_hidden_by_final_usage(tmp_path, monkeypatch, stage, change):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    snapshot = deepcopy(fixed_automation_evidence.verified(output, data, attempt, specimen))
    early = snapshot["body"][stage]
    if change == "native":
        early["profile"]["native"][0]["input_tokens"] = 99
    elif change == "root":
        early["profile"]["provider"]["roots"][0]["run_id"] = str(uuid4())
    else:
        early["profile"]["provider"][change] = 999 if change == "tokens" else 1
    validator = fixed_automation_evidence.validate_draft if stage == "draft" else fixed_automation_evidence.validate_enabled
    with pytest.raises(fixed_run.adapters.Blocked):
        validator(early, data, attempt, specimen)  # The same gate runs before enable/step.
    with pytest.raises(fixed_run.adapters.Blocked):
        fixed_automation_evidence.evaluate(snapshot, specimen, attempt)


def test_a01_submit_receipt_must_bind_the_returned_original_root(tmp_path, monkeypatch):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    attempt["submission_intents"]["submit-run"]["response"]["run_id"] = str(uuid4())
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    with pytest.raises(fixed_run.adapters.Blocked):
        fixed_automation_evidence.verified(output, data, attempt, specimen)


@pytest.mark.parametrize("stage, field", [("draft", "tokens"), ("enabled", "pending"), ("enabled", "unknown_roots")])
def test_early_http_budget_inconsistency_permits_no_next_business_mutation(tmp_path, monkeypatch, stage, field):
    command, _, calls, _, _ = harness(tmp_path, monkeypatch, profile_fault=(stage, field))
    assert command("init") == 0
    assert command("run") != 0
    assert not any(path.endswith("/step") for _, path, _ in calls)
    assert sum(path.endswith("/state") for _, path, _ in calls) == int(stage == "enabled")


@pytest.mark.parametrize("reference", [True, False, 2, 2.0, "02", "2.0", "2 ", " 2", "1", None])
def test_snapshot_reference_revision_preserves_exact_text_contract_without_numeric_coercion(tmp_path, monkeypatch, reference):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    snapshot = deepcopy(fixed_automation_evidence.verified(output, data, attempt, specimen))
    snapshot["body"]["execution"]["model"]["revision"] = reference
    with pytest.raises(fixed_run.adapters.Blocked):
        fixed_automation_evidence.evaluate(snapshot, specimen, attempt)


@pytest.mark.parametrize("native", [True, False, "2", "02", "2.0", 2.0, 1, None])
def test_native_call_revision_remains_strict_integer_and_matches_text_reference(tmp_path, monkeypatch, native):
    command, output, _, _, _ = harness(tmp_path, monkeypatch)
    assert command("init") == command("run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A01")
    snapshot = deepcopy(fixed_automation_evidence.verified(output, data, attempt, specimen))
    snapshot["body"]["execution"]["model"]["calls"][0]["model_revision"] = native
    with pytest.raises(fixed_run.adapters.Blocked):
        fixed_automation_evidence.evaluate(snapshot, specimen, attempt)
