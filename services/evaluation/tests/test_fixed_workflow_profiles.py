import json
import sys
from uuid import uuid4

import httpx
import pytest
from test_core import attestation, target

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run
from evaluation_runner import fixed_workflow_evidence as facts
from evaluation_runner import fixed_workflow_observation as observation
from evaluation_runner.client import Platform
from evaluation_runner.journal import atomic_json


class FormalWorkflowFixture:
    """Controlled HTTP transport for engineering tests; not real platform acceptance."""

    def __init__(self, case_id):
        self.case_id, self.target, self.namespace = case_id, target(), str(uuid4())
        self.ids = {key: str(uuid4()) for key in ("model", "workflow", "version")}
        self.rows, self.events, self.posts, self.extra_tasks = {}, {}, [], 0
        self.bad_branch, self.reject_effect, self.unknown_rejection = False, False, False
        self.proof = attestation(self.target)
        self.proof.update(sandbox_namespace=self.namespace, permissions=["evaluation.read", "evaluation.manage", "evaluation.run",
            "agents.manage", "workflows.read", "workflows.manage", "workflows.execute"])
        self.proof["execution_limits"]["profiles"][case_id] = {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}

    def effect_body(self):
        counts = dict.fromkeys(observation.COUNTS, 0)
        counts.update(workflow_runs=len(self.rows), workflow_events=sum(len(rows) for rows in self.events.values()), work_tasks=self.extra_tasks)
        return {"schema_version": 1, "environment_id": self.target.environment_id, "owner_id": self.target.owner_id,
                "namespace": self.namespace, "case_id": self.case_id, "workflow_id": self.ids["workflow"], "complete": True,
                "run_ids": sorted(self.rows), "counts": counts,
                "provider": {"observed": True, "complete": True, "calls": 0, "pending": 0, "unknown_roots": 0},
                "sandbox": {"observed": True, "complete": True, "attempts": 0}}

    def event_rows(self, identifier, text):
        pairs = [("start", "start", text, text), ("end", "end", text, text)]
        if self.case_id == "AET-F02":
            pairs = [("start", "start", text, text), ("upper", "transform", text, "HELLO"),
                     ("choose", "condition", "HELLO", "true"), ("yes", "end", "HELLO", "HELLO")]
        result = []
        for node, kind, source, output in pairs:
            for name, field, value in (("node.started", "input", source), ("node.completed", "output", output)):
                result.append({"event_id": len(self.rows) * 10 + len(result) + 1, "node_id": node, "event": name,
                               "data": {"kind": kind, field: value}})
        if self.bad_branch and self.rows:
            result[-1]["node_id"] = "no"
        return result

    def server(self, request):
        path = request.url.path
        base = "/api/v1/workflows/" + self.ids["workflow"]
        if path.endswith("/environment"):
            return httpx.Response(200, json=self.proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1", "readonly-workflow-effects-v1"]})
        if path.endswith("/observations"):
            return httpx.Response(200, json=self.effect_body())
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append((path, body))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": self.ids["model"]})
            if path.endswith("/model-bindings"):
                assert body["case_id"] == self.case_id
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/workflows":
                assert body["definition"] == observation.definition(self.case_id)
                return httpx.Response(201, json={"id": self.ids["workflow"]})
            if path.endswith("/publish"):
                return httpx.Response(200, json={"version": 1})
            if path.endswith(("/runs", "/test-runs")):
                if body["text"] == '{"name":4}':
                    if self.unknown_rejection:
                        raise httpx.ReadError("Controlled unknown response")
                    if self.reject_effect:
                        self.extra_tasks += 1
                    return httpx.Response(422, json={"error": {"code": "WORKFLOW_INPUT_INVALID", "message": "输入在 name 不符合声明的 Schema"}})
                identifier = body.get("run_id") or str(uuid4())
                published = "test-runs" not in path
                snapshot = {"model_id": self.ids["model"], "revision": 1, "definition": observation.definition(self.case_id)}
                if published:
                    snapshot["published_version"] = 1
                events = self.event_rows(identifier, body["text"])
                self.rows[identifier] = {"id": identifier, "workflow_id": self.ids["workflow"], "status": "SUCCEEDED",
                    "input": body["text"], "output": "HELLO" if self.case_id == "AET-F02" else body["text"], "snapshot": snapshot}
                self.events[identifier] = events
                return httpx.Response(200, json={"id": identifier, "status": "SUCCEEDED", "output": self.rows[identifier]["output"]})
            pytest.fail("Unexpected mutation route")
        version = {"id": self.ids["version"], "workflow_id": self.ids["workflow"], "model_id": self.ids["model"], "version": 1,
                   "draft_revision": 1, "active": True, "digest": "a" * 64, "definition": observation.definition(self.case_id)}
        if path == base + "/versions":
            return httpx.Response(200, json={"items": [version]})
        if path == base + "/versions/1":
            return httpx.Response(200, json=version)
        if path == base + "/runs":
            identifier = request.url.params.get("run_id")
            return httpx.Response(200, json={"items": [row for key, row in self.rows.items() if not identifier or key == identifier], "next_cursor": None})
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": self.events[path.split("/")[-2]]})
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": dict.fromkeys(observation.TOTAL, 0), "complete": True,
                "scope": {"mode": "workflow", "run_id": path.split("/")[-2]}})
        pytest.fail("Unexpected observation route")


def command(tmp_path, monkeypatch, fixture, action):
    manifest = tmp_path / "target.json"
    atomic_json(manifest, fixture.target.model_dump())
    platform = lambda value: Platform(value, transport=httpx.MockTransport(fixture.server))
    monkeypatch.setattr(fixed_run, "Platform", platform)
    monkeypatch.setattr(Platform, "login", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["fixed_run", action, "--case-id", fixture.case_id, "--output", str(tmp_path / "output"),
                                     "--target-manifest", str(manifest)])
    return fixed_run.main()


@pytest.mark.parametrize("case_id", ["AET-F02", "AET-F04"])
def test_formal_readonly_init_run_cleanup_and_offline_fact_recomputation(tmp_path, monkeypatch, case_id):
    fixture = FormalWorkflowFixture(case_id)
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    assert command(tmp_path, monkeypatch, fixture, "cleanup") == 0
    assert command(tmp_path, monkeypatch, fixture, "report") == 0
    output = tmp_path / "output"
    ready = fixed_audit.validate_ready(output)
    assert ready["gate"] == "PASS"
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    attempt = report["attempts"][0]
    assert attempt["usage"] == dict.fromkeys(observation.TOTAL, 0)
    assert attempt["metric_results"] == []
    audits = [fixed_evidence.load(output, reference, "network-audit") for reference in ready["network_audit_refs"]]
    assert [row["action"] for row in audits] == ["init", "run", "cleanup", "report"]
    assert {row["name"] for row in audits[1]["phases"]} == {"execution", "export"}
    assert len(fixture.rows) == 2  # One initialization test and one published execution, not two cases/repeats.
    if case_id == "AET-F04":
        assert attempt["invalid_run_id"] not in fixture.rows
        assert attempt["business_outcomes"][0] == {"kind": "invalid-input", "status": "REJECTED", "http_status": 422,
            "submission_request_id": attempt["invalid_run_id"], "platform_root": None}
        assert attempt["business_outcomes"][1]["status"] == "SUCCEEDED"
        assert [body["text"] for path, body in fixture.posts if path.endswith("/versions/1/runs")] == ['{"name":4}', '{"name":"李四"}']
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    assert len(fixture.posts) == before  # Re-observation never repeats a confirmed published submission.


def test_branch_uses_real_event_path_instead_of_accepting_only_final_output(tmp_path, monkeypatch):
    fixture = FormalWorkflowFixture("AET-F02")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.bad_branch = True
    assert command(tmp_path, monkeypatch, fixture, "run") == 1
    report = json.loads((tmp_path / "output/report.json").read_text(encoding="utf-8"))
    assert report["gate"] == "FAIL"
    assert report["attempts"][0]["platform_status"] == "SUCCEEDED"
    assert fixed_audit.validate_ready(tmp_path / "output")["gate"] == "FAIL"


def test_rejected_request_with_a_new_work_task_cannot_pass_or_dispatch_contrast(tmp_path, monkeypatch):
    fixture = FormalWorkflowFixture("AET-F04")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.reject_effect = True
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    data = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))
    assert data["attempts"][0]["task_outcome"] == "FAIL"
    assert len(fixture.rows) == 1
    assert [body["text"] for path, body in fixture.posts if path.endswith("/versions/1/runs")] == ['{"name":4}']
    with pytest.raises(ValueError):
        fixed_audit.validate_ready(tmp_path / "output")


def test_unknown_schema_response_is_not_reposted_or_called_a_rejection(tmp_path, monkeypatch):
    fixture = FormalWorkflowFixture("AET-F04")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.unknown_rejection = True
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert sum(path.endswith("/versions/1/runs") for path, _ in fixture.posts) == 1
    data = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))
    assert data["attempts"][0]["fixture_intents"]["reject-workflow-schema"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("mutation", ["missing_events", "wrong_input", "wrong_version", "wrong_namespace", "unknown_provider", "missing_counter", "fake_zero", "extra_raw", "boolean_repeat", "boolean_version"])
def test_retained_workflow_proofs_reject_missing_foreign_or_changed_fields(tmp_path, monkeypatch, mutation):
    fixture = FormalWorkflowFixture("AET-F02")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    output = tmp_path / "output"
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    reference = attempt["evidence_refs"][-1]
    snapshot = fixed_evidence.load(output, reference, "workflow-facts")
    if mutation == "missing_events":
        snapshot["body"]["execution"]["events"] = []
    elif mutation == "wrong_input":
        snapshot["body"]["execution"]["events"][3]["output"]["sha256"] = "0" * 64
        snapshot["body"]["execution"]["events"][3]["output"]["declared"] = None
    elif mutation == "wrong_version":
        snapshot["body"]["execution"]["version"] = 2
    elif mutation == "wrong_namespace":
        snapshot["body"]["after"]["namespace"] = str(uuid4())
    elif mutation == "unknown_provider":
        snapshot["body"]["after"]["provider"]["complete"] = False
    elif mutation == "missing_counter":
        snapshot["body"]["after"]["counts"].pop("model_calls")
    elif mutation == "fake_zero":
        snapshot["usage"]["input_tokens"] = False
    elif mutation == "boolean_repeat":
        snapshot["binding"]["repeat_index"] = True
    elif mutation == "boolean_version":
        snapshot["body"]["execution"]["version"] = True
    else:
        snapshot["body"]["execution"]["raw_tool_log"] = "forbidden"
    path = output / reference["path"]
    atomic_json(path, snapshot)
    reference["sha256"] = fixed_evidence.file_digest(path)
    atomic_json(output / "journal.json", data)
    assert command(tmp_path, monkeypatch, fixture, "report") != 0


def test_baseline_is_kept_after_initialization_instead_of_reset_after_invalid_request(tmp_path, monkeypatch):
    fixture = FormalWorkflowFixture("AET-F04")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    output = tmp_path / "output"
    before = json.loads((output / "journal.json").read_text(encoding="utf-8"))["attempts"][0]["workflow_baseline_ref"]
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    data = json.loads((output / "journal.json").read_text(encoding="utf-8"))
    assert data["attempts"][0]["workflow_baseline_ref"] == before
    baseline = fixed_evidence.load(output, before, "workflow-baseline")
    assert len(baseline["body"]["effects"]["run_ids"]) == 1


def test_fact_persistence_failure_blocks_pass_cleanup_and_new_dispatch(tmp_path, monkeypatch):
    fixture = FormalWorkflowFixture("AET-F02")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    original = facts.store

    def failed(directory, kind, value):
        if kind == "workflow-facts":
            raise OSError("Controlled disk failure")
        return original(directory, kind, value)

    monkeypatch.setattr(facts, "store", failed)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    data = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))
    assert data["attempts"][0]["workflow_evidence_failed"] is True
    assert data["attempts"][0]["task_outcome"] == "NEEDS_REVIEW"
    posts = len(fixture.posts)
    monkeypatch.setattr(facts, "store", original)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert command(tmp_path, monkeypatch, fixture, "cleanup") != 0
    assert len(fixture.posts) == posts
