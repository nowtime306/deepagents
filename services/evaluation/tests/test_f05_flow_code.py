"""F05 complete private CLI with an HTTP substitute; not physical code execution."""

import copy
import hashlib
import json
import sys
from uuid import uuid4

import httpx
import pytest
from f05_support import attestation, target

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run
from evaluation_runner import fixed_case_deadline as case_deadline
from evaluation_runner import fixed_code_contract as contract
from evaluation_runner import fixed_flow_code as client
from evaluation_runner.client import Platform
from evaluation_runner.journal import atomic_json, digest


class Fixture:
    case_id = "AET-F05"

    def __init__(self):
        self.target, self.namespace, self.model, self.workflow, self.version = target(), str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
        self.roots, self.posts, self.sessions = {}, [], []
        self.lost_start, self.lost_decision, self.bad_code, self.unknown_op = False, False, False, False
        self.proof = attestation(self.target)
        self.proof.update(sandbox_namespace=self.namespace, permissions=["evaluation.read", "evaluation.manage", "evaluation.run",
            "workflows.read", "workflows.manage", "workflows.execute", "work.read", "work.manage", "work.execute"])
        self.proof["execution_limits"]["profiles"][self.case_id] = {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}

    def advance(self, root, *, reject=False):
        self.sessions.append({"id": str(uuid4()), "run_id": root["run_id"], "state": "RELEASED", "sandbox_id": str(uuid4())})
        root["snapshot"] = str(uuid4())
        if reject:
            root.update(status="FAILED", workflow_status="FAILED", rejected=True)
        elif root["phase"] == 3:
            root.update(status="COMPLETED", workflow_status="SUCCEEDED")
        else:
            root.update(status="WAITING_APPROVAL", workflow_status="RUNNING")
            node = ("before", "code", "after")[root["phase"]]
            args = contract.arguments(node)
            if self.bad_code and node == "code":
                args["code"] = "print('wrong')"
            action = {"name": "workflow_code" if node == "code" else "workflow_approval", "args": args, "description": "fixed engineering"}
            payload = {"action_requests": [action], "review_configs": [{"action_name": action["name"], "allowed_decisions": ["approve", "reject"]}]}
            root["approvals"].append({"id": str(uuid4()), "payload": payload, "digest": digest(payload), "decision": None})

    def node_events(self, root):
        rows = [("start", "node.started", "start"), ("start", "node.completed", "start"), ("before", "node.started", "approval")]
        for phase in range(root["phase"]):
            node = ("before", "code", "after")[phase]
            kind = "code" if node == "code" else "approval"
            rows.extend([(node, "node.started", kind), (node, "node.failed" if root.get("rejected") else "node.completed", kind)])
            if root.get("rejected"):
                break
            if phase < 2:
                next_node = ("code", "after")[phase]
                rows.append((next_node, "node.started", "code" if next_node == "code" else "approval"))
            else:
                rows.extend([("end", "node.started", "end"), ("end", "node.completed", "end")])
        return [{"event_id": index + 1, "node_id": node, "event": event,
                 "data": {"kind": kind, **({"error_code": "WORKFLOW_APPROVAL_REJECTED"} if event == "node.failed" else {})}}
                for index, (node, event, kind) in enumerate(rows)]

    def operations(self, root):
        if root.get("rejected"):
            return []
        return [{"id": root["operation_ids"][index], "name": "workflow_code" if index == 1 else "workflow_approval",
                 "args": contract.arguments(("before", "code", "after")[index]),
                 "status": "UNKNOWN" if self.unknown_op else "SUCCEEDED", "exit_code": None if self.unknown_op else 0}
                for index in range(root["phase"])]

    def artifacts(self, root):
        if root["phase"] < 2 or root.get("rejected"):
            return []
        return [{"id": root["artifact_ids"][index], "path": path, "size": len(raw), "digest": hashlib.sha256(raw).hexdigest()}
                for index, (path, raw) in enumerate((("/counter.txt", b"1"), ("/workflow-code.py", contract.CODE.encode()), ("/workflow-code.input.txt", b"seed")))]

    def observer(self, root):
        return {"schema_version": 2, "case_id": self.case_id, "environment_id": self.target.environment_id, "owner_id": self.target.owner_id,
                "namespace": self.namespace, "run_id": root["run_id"], "task_id": root["task_id"], "workflow_id": self.workflow,
                "workflow_run_id": root["workflow_run_id"], "workflow_status": root["workflow_status"], "status": root["status"],
                "snapshot_id": root["snapshot"], "lease_free": True, "cancel_requested": False,
                "counts": {"work_runs": len(self.roots), "workflow_runs": len(self.roots), "model_calls": 0,
                           "unknown_operations": 1 if self.unknown_op else 0, "active_runs": 0},
                "sessions_complete": True, "sessions": copy.deepcopy(self.sessions),
                "provider": {"observed": True, "complete": True, "calls": 0, "tokens": 0, "pending": 0, "unknown_roots": 0},
                "sandbox": {"attempts": len(self.sessions), "storage_healthy": True, "inventory": {"complete": True, "count": 0}}}

    def deadline_capability(self):
        from evaluation_runner import f05_deadline_contract as authentication

        data = authentication.data_directory(self.child, self.target.environment_id)
        anchor = authentication.read(data / authentication.ANCHOR)
        path = self.child / "case-deadline.json"
        record = authentication.read(path) if path.exists() else None
        return {"contract": authentication.CONTRACT, "required": True, "installed": True,
                "bound_identity_digest": digest(anchor["identity"]),
                "boundaries": dict.fromkeys(("provider", "tool", "sandbox", "submission"), True),
                "phase": "ACTIVE" if record else "AWAITING_RECORD", "deadline_bound": record is not None,
                "dispatch_allowed": record is not None, "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if record else None,
                "expires_at_ms": record["expires_at_ms"] if record else None}

    def server(self, request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=self.proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/capabilities"):
            return httpx.Response(200, json={"f05_deadline_guard": self.deadline_capability(), "observers": ["root-model-context-v1", "fixed-code-three-roots-v2"],
                "work_manager": {"ready": True}, "sandbox_limits": {"attempts": len(self.sessions), "storage_healthy": True,
                    "enforcement": "controlled-sandbox-reserve-v1", "scope": "test-environment", "max_created": 2,
                    "fixed_cases": {self.case_id: {"enabled": True, "max_created": 10, "max_concurrent": 1, "material": "fixed-code-v2", "counterfactual": "reject-first-approval"}}}})
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append((path, body))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": self.model})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/workflows":
                assert body["definition"] == contract.definition()
                return httpx.Response(201, json={"id": self.workflow})
            if path.endswith("/publish"):
                assert len(self.roots) == 1 and next(iter(self.roots.values()))["status"] == "COMPLETED"
                return httpx.Response(201, json={"version": 1})
            if path.endswith("/work-runs"):
                root = {"run_id": str(uuid4()), "task_id": str(uuid4()), "workflow_run_id": str(uuid4()), "version": body.get("version"),
                        "phase": 0, "approvals": [], "operation_ids": [str(uuid4()) for _ in range(3)], "artifact_ids": [str(uuid4()) for _ in range(3)]}
                self.roots[root["run_id"]] = root
                self.advance(root)
                if self.lost_start:
                    raise httpx.ReadError("Accepted root response lost")
                return httpx.Response(202, json={**{key: root[key] for key in ("run_id", "task_id", "workflow_run_id")}, "status": "PENDING"})
            if path.endswith("/decisions"):
                root, approval = next((root, approval) for root in self.roots.values() for approval in root["approvals"] if approval["id"] == path.split("/")[-2])
                assert body["digest"] == approval["digest"]
                approval["decision"] = body["decision"]
                root["phase"] += 1
                self.advance(root, reject=body["decision"] == "reject")
                if self.lost_decision:
                    raise httpx.ReadError("Accepted decision response lost")
                return httpx.Response(200, json={"decision": body["decision"]})
            pytest.fail("Unexpected F05 POST")
        if path.endswith("/download"):
            return httpx.Response(200, content=b"1")
        if "/versions/" in path:
            return httpx.Response(200, json={"id": self.version, "workflow_id": self.workflow, "version": 1, "draft_revision": 1,
                "definition": contract.definition(), "model_id": self.model, "digest": "a" * 64, "active": True})
        if path.endswith("/runs"):
            root = next(root for root in self.roots.values() if root["workflow_run_id"] == request.url.params["run_id"])
            snapshot = {"definition": contract.definition(), "revision": 1, "model_id": self.model}
            if root["version"] is not None:
                snapshot["published_version"] = root["version"]
            return httpx.Response(200, json={"items": [{"id": root["workflow_run_id"], "workflow_id": self.workflow,
                "status": root["workflow_status"], "snapshot": snapshot, "input": "seed", "output": "seed-code" if root["status"] == "COMPLETED" else None}], "next_cursor": None})
        if "/workflows/" in path and path.endswith("/events"):
            root = next(root for root in self.roots.values() if root["workflow_run_id"] == path.split("/")[-2])
            return httpx.Response(200, json={"items": self.node_events(root)})
        if path.endswith("/observations"):
            if "/profiles/runs/work/" in path:
                identifier = path.split("/")[-2]
                return httpx.Response(200, json={"sandbox_sessions": {"complete": True, "states": {
                    "RELEASED": sum(item["run_id"] == identifier for item in self.sessions)}}})
            return httpx.Response(200, json=self.observer(self.roots[path.split("/")[-2]]))
        root = self.roots[path.split("/")[4]]
        if path.endswith("/artifacts"):
            return httpx.Response(200, json={"items": self.artifacts(root)})
        if path.endswith("/operations"):
            return httpx.Response(200, json={"items": self.operations(root)})
        if path.endswith("/approvals"):
            return httpx.Response(200, json={"items": root["approvals"]})
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": dict.fromkeys(client.workflow.TOTAL, 0)})
        if path.endswith("/events"):
            cursor = int(request.url.params.get("after", 0))
            events = [] if cursor else [{"event_id": 1, "event": "run.started"}]
            return httpx.Response(200, json={"items": events, "next_after": 1})
        return httpx.Response(200, json={"id": root["run_id"], "task_id": root["task_id"], "owner_id": self.target.owner_id,
            "model_id": self.model, "environment": "coding", "status": root["status"]})


def command(tmp_path, monkeypatch, fixture, action):
    from evaluation_runner import f05_deadline_contract as authentication

    fixture.child = tmp_path
    path = tmp_path / "target.json"
    atomic_json(path, fixture.target.model_dump())
    context_path = tmp_path / "suite-context.json"
    if not context_path.exists():
        specimen = next(row for row in fixed_run.catalog().cases if row.case_id == fixture.case_id)
        atomic_json(context_path, fixed_run.suite_context(None, specimen, fixed_run.catalog(), fixture.target))
    data = authentication.data_directory(tmp_path, fixture.target.environment_id)
    if not (data / authentication.ANCHOR).exists():
        data.mkdir(parents=True)
        context = json.loads(context_path.read_text(encoding="utf-8"))
        anchor_sha = authentication.provision(tmp_path, {"environment_id": fixture.target.environment_id, "owner_id": fixture.target.owner_id,
            "namespace": fixture.namespace, "candidate_commit": fixture.target.platform_commit, "suite_context": context})
        atomic_json(tmp_path / "target/target-state.json", {"environment_id": fixture.target.environment_id, "owner_id": fixture.target.owner_id,
            "sandbox_namespace": fixture.namespace, "platform_commit": fixture.target.platform_commit, "f05_guard_required": True, "f05_anchor_sha256": anchor_sha})
    if action == "init" and not (tmp_path / "case-deadline.json").exists():
        case_deadline.seal(tmp_path, fixture.target, json.loads(context_path.read_text(encoding="utf-8")), fixture.namespace)
    if (tmp_path / "case-deadline.json").exists():
        monkeypatch.setenv(case_deadline.ENV, hashlib.sha256((tmp_path / "case-deadline.json").read_bytes()).hexdigest())
    monkeypatch.setattr(fixed_run, "Platform", lambda value: Platform(value, transport=httpx.MockTransport(fixture.server)))
    monkeypatch.setattr(Platform, "login", lambda _: None)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["fixed_run", action, "--case-id", fixture.case_id, "--output", str(tmp_path / "output"),
                                     "--target-manifest", str(path), "--suite-context", str(context_path)])
    return fixed_run.main()


def test_complete_three_roots_approval_reject_lifecycle_offline_ready_and_confirmed_no_replay(tmp_path, monkeypatch):
    fixture = Fixture()
    for action in ("init", "run", "cleanup", "report"):
        assert command(tmp_path, monkeypatch, fixture, action) == 0
    ready = fixed_audit.validate_ready(tmp_path / "output")
    report = json.loads((tmp_path / "output/report.json").read_text(encoding="utf-8"))
    attempt = report["attempts"][0]
    assert ready["gate"] == "PASS" and attempt["usage"] == dict.fromkeys(client.workflow.TOTAL, 0)
    assert len(fixture.roots) == 3 and len(fixture.sessions) == 10
    assert [row["status"] for row in attempt["business_outcomes"]] == ["COMPLETED", "COMPLETED", "FAILED"]
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    assert len(fixture.posts) == before


@pytest.mark.parametrize("lost", ["lost_start", "lost_decision"])
def test_unknown_start_or_decision_is_not_replayed(tmp_path, monkeypatch, lost):
    fixture = Fixture()
    setattr(fixture, lost, True)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert len(fixture.posts) == before


def test_substituted_code_digest_stops_before_approval_and_host_execution(tmp_path, monkeypatch):
    fixture = Fixture()
    fixture.bad_code = True
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    decisions = [body for path, body in fixture.posts if path.endswith("/decisions")]
    assert len(decisions) == 1
    assert next(iter(fixture.roots.values()))["phase"] == 1


def test_unknown_prior_operation_stops_before_next_approval(tmp_path, monkeypatch):
    fixture = Fixture()
    fixture.unknown_op = True
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert len([path for path, _ in fixture.posts if path.endswith("/decisions")]) == 1


def test_missing_original_decision_cannot_advance_even_when_receiver_has_advanced(tmp_path, monkeypatch):
    fixture = Fixture()
    fixture.lost_decision = True
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    path = tmp_path / "output/journal.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["attempts"][0]["submission_intents"] = {}
    atomic_json(path, data)
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert len(fixture.posts) == before


@pytest.mark.parametrize("missing", ["draft-waiting-1", "draft-final", "publication"])
def test_missing_initialization_evidence_blocks_before_any_main_root(tmp_path, monkeypatch, missing):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    path = tmp_path / "output/journal.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["attempts"][0]["flow_code_refs"][missing]
    atomic_json(path, data)
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == before and len(fixture.roots) == 1


@pytest.mark.parametrize("entry", ["initialize", "execute", "decide", "drive"])
@pytest.mark.parametrize("state", ["UNKNOWN", "SUBMITTING", "REJECTED"])
def test_unsettled_prior_intent_blocks_every_entry_before_any_io(entry, state):
    from types import SimpleNamespace

    attempt = {"submission_intents": {"old": {"status": state}}}
    with pytest.raises(RuntimeError, match="unsettled"):
        if entry == "initialize":
            client.initialize(SimpleNamespace(attempt=attempt, journal=None, platform=None), None, None)
        elif entry == "execute":
            client.execute(None, None, attempt, None)
        elif entry == "decide":
            client.decide(None, None, attempt, {}, "draft", 0, False)
        else:
            client.drive(None, None, attempt, "draft", {})


@pytest.mark.parametrize("fault", ["owner", "kind", "publication", "counter", "pending", "decision", "sessions", "unknown-status"])
def test_offline_same_gate_rejects_tampered_or_unsettled_sealed_facts(tmp_path, monkeypatch, fault):
    fixture = Fixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    directory = tmp_path / "output"
    data = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    if fault == "owner":
        data["owner_id"] = str(uuid4())
    elif fault == "decision":
        attempt["submission_intents"]["f05-published-decision-1"]["status"] = "UNKNOWN"
    else:
        slot = "publication" if fault == "publication" else "reject-final" if fault in {"sessions", "unknown-status"} else "published-final"
        old = attempt["flow_code_refs"][slot]
        value = fixed_evidence.load(directory, old, "flow-code")
        if fault == "kind":
            value["kind"] = "wrong"
        elif fault == "publication":
            value["body"]["definition"]["nodes"][2]["parameters"]["code"] = "print('wrong')"
        elif fault == "counter":
            value["body"]["operations"][1]["status"] = "UNKNOWN"
        elif fault == "pending":
            value["body"]["usage"]["pending_calls"] = 1
        elif fault == "unknown-status":
            value["body"]["status"] = "NEEDS_REVIEW"
            value["body"]["observer"]["status"] = "NEEDS_REVIEW"
        else:
            value["body"]["observer"]["sessions"][0]["state"] = "UNKNOWN"
        reference = fixed_evidence.store(directory, "flow-code", value)
        attempt["flow_code_refs"][slot] = reference
        attempt["evidence_refs"][attempt["evidence_refs"].index(old)] = reference
    specimen = next(case for case in fixed_run.catalog().cases if case.case_id == fixture.case_id)
    with pytest.raises((ValueError, RuntimeError)):
        client.cleanup_ready(directory, data, attempt, specimen)
