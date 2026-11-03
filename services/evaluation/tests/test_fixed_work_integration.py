import json
import sys
from uuid import uuid4

import httpx
from test_fixed_work_observation import context, server

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run
from evaluation_runner.client import Platform
from evaluation_runner.journal import atomic_json


def test_shared_work_pipeline_retains_negative_fact_allows_only_proven_cleanup_and_skips_metrics(tmp_path, monkeypatch):
    target, identifiers = context()
    methods, mutations = [], []
    observe = server(target, identifiers, methods)
    output = tmp_path / "output"
    proof = {**target.model_dump(include={"owner_id", "environment_id", "platform_commit", "fixture_profile"}),
             "database_is_test": True, "schema_migrations": [], "sandbox_namespace": identifiers["namespace"],
             "permissions": ["evaluation.read", "evaluation.manage", "evaluation.run", "work.read", "work.manage", "work.execute"]}

    def transport(request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/work-capabilities"):
            return httpx.Response(200, json={"enabled": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1"], "work_manager": {"ready": True},
                "sandbox_limits": {"enforcement": "controlled-sandbox-reserve-v1", "scope": "test-environment",
                                   "max_created": 2, "storage_healthy": True, "attempts": 0}})
        if request.method == "POST":
            data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
            assert data["attempts"][0]["sandbox_namespace"] == identifiers["namespace"]
            body = json.loads(request.content)
            mutations.append((path, body))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": str(uuid4())})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/work-tasks":
                assert body["goal"] == "生成 /report.txt，内容必须是 AE_FILE_READY。"
                return httpx.Response(201, json={"id": identifiers["task"]})
            assert path == f"/api/v1/work-tasks/{identifiers['task']}/runs"
            assert body["revision"] == 1 and body["environment"] == "coding"
            return httpx.Response(202, json={"run_id": identifiers["run"]})
        return observe(request)

    monkeypatch.setattr(fixed_run, "Platform", lambda value: Platform(value, transport=httpx.MockTransport(transport)))
    monkeypatch.setattr(Platform, "login", lambda _: None)
    manifest = tmp_path / "target.json"
    atomic_json(manifest, target.model_dump())

    def command(action):
        monkeypatch.setattr(sys, "argv", ["fixed_run", action, "--case-id", "AET-W01", "--output", str(output),
                                         "--target-manifest", str(manifest)])
        return fixed_run.main()

    assert command("init") == 0
    assert command("run") == 1  # The actual task is a known missing-file failure, not a flipped expected success.
    assert command("cleanup") == 0
    assert command("report") == 1
    receipt = fixed_audit.validate_ready(output)
    assert receipt["gate"] == "FAIL"
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    attempt = report["attempts"][0]
    assert attempt["task_outcome"] == "FAIL" and attempt["platform_status"] == "COMPLETED"
    assert attempt["cleanup_status"] == "CLEAN" and attempt["metric_results"] == []
    assert next(row["status"] for row in attempt["fact_results"] if row["registered_id"] == "artifact_exists") == "FAIL"
    audits = [fixed_evidence.load(output, reference, "network-audit") for reference in receipt["network_audit_refs"]]
    assert {phase["name"] for phase in audits[1]["phases"]} == {"execution", "export"}
    assert len(mutations) == 4
