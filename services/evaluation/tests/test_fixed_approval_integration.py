"""Full shared HTTP-transport pipeline; not an actual Coding task or worker restart."""

import json
import sys
from uuid import uuid4

import httpx
import pytest
from test_fixed_approval_observation import pair

from evaluation_runner import fixed_audit, fixed_consumer_evidence, fixed_run
from evaluation_runner.client import Platform
from evaluation_runner.contracts import Target
from evaluation_runner.fixed_approval_observation import COMMAND
from evaluation_runner.journal import atomic_json, digest


def harness(tmp_path, monkeypatch, case_id, *, unknown=False):
    before, after = pair(case_id)
    work = before["work"]
    target = Target(environment_id=work["environment_id"], owner_id=work["owner_id"], api_base_url="http://127.0.0.1:1",
                    platform_commit="a" * 40, required_migrations=[], fixture_profile={"id": "controlled-v1", "version": 1})
    payload = {"action_requests": [{"name": "execute", "args": {"command": COMMAND}}]}
    for state in (before, after):
        state["approvals"][0].update(payload_digest=digest(payload), reported_digest=digest(payload))
    checkpoint = str(uuid4())
    proof = {"schema_version": 1, "case_id": "AET-W04", "source_run_id": work["run_id"], "owner_id": work["owner_id"],
             "environment_id": work["environment_id"], "namespace": work["namespace"], "status": "REBUILT",
             "before_worker_ids": [str(uuid4())], "after_worker_ids": [str(uuid4())],
             "checkpoint_id_before": checkpoint, "checkpoint_id_after": checkpoint}
    environment = {**target.model_dump(include={"owner_id", "environment_id", "platform_commit", "fixture_profile"}),
                   "database_is_test": True, "schema_migrations": [], "sandbox_namespace": work["namespace"],
                   "permissions": ["evaluation.read", "evaluation.manage", "evaluation.run", "work.read", "work.manage", "work.execute"]}
    state, calls, output = {"final": False}, [], tmp_path / "output"
    model = str(uuid4())

    def transport(request):
        path, method = request.url.path, request.method
        current = after if state["final"] else before
        observed = current["work"]
        if path.endswith("/environment"):
            return httpx.Response(200, json=environment)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/work-capabilities"):
            return httpx.Response(200, json={"enabled": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1", "work-consumer-rebuild-v1"],
                "work_manager": {"ready": True}, "sandbox_limits": {"enforcement": "controlled-sandbox-reserve-v1",
                "scope": "test-environment", "max_created": 2, "storage_healthy": True, "attempts": 0}})
        if method == "POST":
            calls.append((path, request.content))
            saved = json.loads((output / "journal.json").read_text(encoding="utf-8"))["attempts"][0]
            assert saved["sandbox_namespace"] == work["namespace"]
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": model})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/work-tasks":
                return httpx.Response(201, json={"id": work["task_id"]})
            if path.endswith("/files"):
                assert request.content == b"0" and request.url.params["revision"] == "1"
                return httpx.Response(201, json={"revision": 2})
            body = json.loads(request.content)
            if path.endswith("/runs"):
                assert body["revision"] == 2 and body["environment"] == "coding"
                return httpx.Response(202, json={"run_id": work["run_id"]})
            if path.endswith("/rebuild"):
                assert saved["approval_before_ref"] and not state["final"]
                assert body == {"source_run_id": work["run_id"]}
                if unknown:
                    raise httpx.ReadError("Consumer response lost after possible rebuild")
                return httpx.Response(200, json=proof)
            if path.endswith("/decisions"):
                assert saved["approval_before_ref"]
                if case_id == "AET-W04":
                    assert saved["approval_consumer_ref"]
                assert body == {"digest": digest(payload), "decision": "reject" if case_id == "AET-W03" else "approve"}
                state["final"] = True
                return httpx.Response(200, json={"status": "PENDING"})
            raise AssertionError(path)
        if path.startswith(fixed_consumer_evidence.PATH + "/"):
            return httpx.Response(200, json=proof)
        if path.endswith("/artifacts"):
            return httpx.Response(200, json={"items": [{"id": row["id"], "path": "/counter.txt", "digest": row["digest"], "size": row["size"]}
                                                       for row in observed["artifacts"]]})
        if path.endswith("/download"):
            return httpx.Response(200, content=current["counter"]["declared"].encode())
        if path.endswith("/operations"):
            return httpx.Response(200, json={"items": [{**{key: row[key] for key in ("id", "name", "status", "exit_code")},
                                                     "args": {"command": COMMAND}} for row in current["receipts"]]})
        if path.endswith("/approvals"):
            return httpx.Response(200, json={"items": [{"id": row["id"], "payload": payload, "digest": row["reported_digest"],
                                                       "decision": row["decision"]} for row in current["approvals"]]})
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": observed["usage"]})
        if path.endswith("/observations"):
            return httpx.Response(200, json={"mode": "work", "run_id": work["run_id"], "status": observed["status"],
                "controlled_model": observed["provider"], "sandbox_sessions": {"complete": True, "states": observed["sandbox_states"]}})
        if path.endswith("/events"):
            cursor = int(request.url.params["after"])
            rows = [row for row in observed["events"] if row["event_id"] > cursor]
            return httpx.Response(200, json={"items": rows, "next_after": rows[-1]["event_id"] if rows else cursor})
        if path == "/api/v1/work-runs/" + work["run_id"]:
            return httpx.Response(200, json={"id": work["run_id"], "task_id": work["task_id"], "owner_id": work["owner_id"],
                "environment": "coding", "status": observed["status"], "result": {"snapshot_id": observed["snapshot_id"]}})
        raise AssertionError((method, path))

    monkeypatch.setattr(fixed_run, "IMPLEMENTED", fixed_run.IMPLEMENTED | {case_id})
    monkeypatch.setattr(fixed_run, "Platform", lambda value: Platform(value, transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(Platform, "login", lambda _: None)
    manifest = tmp_path / "target.json"
    atomic_json(manifest, target.model_dump())

    def command(action):
        monkeypatch.setattr(sys, "argv", ["fixed_run", action, "--case-id", case_id, "--output", str(output), "--target-manifest", str(manifest)])
        return fixed_run.main()

    return command, output, calls, environment


@pytest.mark.parametrize("case_id", ["AET-W03", "AET-W04"])
def test_shared_uploaded_revision_approval_and_consumption_evidence_to_offline_ready(tmp_path, monkeypatch, case_id):
    command, output, calls, _ = harness(tmp_path, monkeypatch, case_id)
    assert command("init") == 0
    assert command("run") == 0
    assert command("cleanup") == 0
    assert command("report") == 0
    ready = fixed_audit.validate_ready(output)
    assert ready["gate"] == "PASS"
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    assert attempt["fixture"]["revision"] == 2 and attempt["cleanup_status"] == "CLEAN"
    assert all(row["status"] == "PASS" for row in attempt["fact_results"])
    assert attempt["usage"]["calls"] == 2 and attempt["metric_results"] == []
    assert sum(path.endswith("/decisions") for path, _ in calls) == (2 if case_id == "AET-W04" else 1)
    assert sum(path.endswith("/rebuild") for path, _ in calls) == int(case_id == "AET-W04")


def test_unknown_consumer_rebuild_cannot_retry_or_approve_or_clean(tmp_path, monkeypatch):
    command, output, calls, _ = harness(tmp_path, monkeypatch, "AET-W04", unknown=True)
    assert command("init") == 0
    assert command("run") != 0 and command("run") != 0
    assert command("cleanup") != 0
    assert sum(path.endswith("/rebuild") for path, _ in calls) == 1
    assert not any(path.endswith("/decisions") for path, _ in calls)
    with pytest.raises(ValueError):
        fixed_audit.validate_ready(output)


def test_namespace_drift_blocks_approval_task_submission_with_no_new_post(tmp_path, monkeypatch):
    command, _, calls, environment = harness(tmp_path, monkeypatch, "AET-W04")
    assert command("init") == 0
    previous = len(calls)
    environment["sandbox_namespace"] = str(uuid4())
    assert command("run") != 0
    assert len(calls) == previous
