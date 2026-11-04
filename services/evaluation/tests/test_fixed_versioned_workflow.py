"""HTTP substitutes prove client retention/state contracts, not actual A20 execution."""

import copy
import json
import sys
from uuid import uuid4

import httpx
import pytest
from test_core import attestation, target

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run
from evaluation_runner import fixed_versioned_workflow as facts
from evaluation_runner import fixed_workflow_observation as observation
from evaluation_runner.client import Platform
from evaluation_runner.journal import Journal, atomic_json


class Fixture:
    def __init__(self, case):
        self.case_id, self.target, self.namespace = case, target(), str(uuid4())
        self.model = str(uuid4())
        self.workflows, self.rows, self.events, self.traces, self.posts = {}, {}, {}, {}, []
        self.model_revision, self.temperature = 1, None
        self.lost, self.bad_502, self.bad_trace, self.dirty, self.bad_event = False, False, None, 0, False
        self.unknown_usage = False
        self.proof = attestation(self.target)
        self.proof.update(sandbox_namespace=self.namespace, permissions=["evaluation.read", "evaluation.manage", "evaluation.run",
            "workflows.read", "workflows.manage", "workflows.execute"])
        self.proof["execution_limits"]["profiles"][case] = {"mode": "workflow", "max_model_calls": 1 if case == "AET-F01" else 0,
                                                           "max_tokens": 8192 if case == "AET-F01" else 0}

    def run(self, definition, text, root, prefix=""):
        values, events = {"input": text}, self.events[root]

        def event(node, name, **data):
            events.append({"event_id": len(events) + 1, "node_id": prefix + node["id"], "event": name,
                           "data": {"kind": node["kind"], **data}})

        for node in definition["nodes"]:
            source = values[node["input"]]
            event(node, "node.started", input=source)
            try:
                if node["kind"] in {"start", "end"}:
                    output = source
                elif node["kind"] == "llm":
                    output = source.upper() if "upper" in node["parameters"]["instructions"] else source.lower()
                elif node["kind"] == "transform":
                    output = json.loads(source)["name"]
                else:
                    child = next(item for workflow in self.workflows.values() for item in workflow["versions"]
                                 if item["id"] == node["parameters"]["version_id"])
                    output = self.run(child["definition"], source, root, prefix=prefix + node["id"] + ".0.")
            except (KeyError, RuntimeError):
                event(node, "node.failed", error_code="NODE_EXECUTION_FAILED")
                raise RuntimeError("Fixed nested failure") from None
            event(node, "node.completed", output=output)
            values[node["id"]] = output
        return output

    def effect_body(self, identifier):
        count = dict.fromkeys(observation.COUNTS, 0)
        count.update(workflow_runs=len(self.rows), workflow_events=sum(len(rows) for rows in self.events.values()),
                     model_calls=len(self.traces), work_tasks=self.dirty)
        return {"schema_version": 1, "environment_id": self.target.environment_id, "owner_id": self.target.owner_id,
                "namespace": self.namespace, "case_id": self.case_id, "workflow_id": identifier, "complete": True,
                "run_ids": [key for key, row in self.rows.items() if row["workflow_id"] == identifier], "counts": count,
                "provider": {"observed": True, "complete": True, "calls": len(self.traces), "pending": 0, "unknown_roots": 0},
                "sandbox": {"observed": True, "complete": True, "attempts": 0}}

    def server(self, request):
        path, method = request.url.path, request.method
        if path.endswith("/environment"):
            return httpx.Response(200, json=self.proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1", "readonly-workflow-effects-v1"],
                "case_limits": {"schema_version": 1, "enforcement": "controlled-case-reserve-v1", "scope": "evaluation-owner-and-case",
                                "profiles": facts.LIMITS}})
        if path.endswith("/observations"):
            return httpx.Response(200, json=self.effect_body(path.split("/")[-2]))
        if method in {"POST", "PUT"}:
            body = json.loads(request.content)
            self.posts.append((method, path, body))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": self.model})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/admin/models/" + self.model:
                assert body["revision"] == 1
                self.model_revision, self.temperature = 2, body["temperature"]
                return httpx.Response(200, json={"id": self.model})
            if path == "/api/v1/workflows":
                identifier = str(uuid4())
                self.workflows[identifier] = {"definition": body["definition"], "revision": 1, "versions": [], "current": None, "release": 1}
                return httpx.Response(201, json={"id": identifier})
            identifier = path.split("/")[4]
            workflow = self.workflows[identifier]
            if method == "PUT":
                assert workflow["revision"] == body["revision"]
                workflow.update(definition=body["definition"], revision=2)
                return httpx.Response(200, json={"id": identifier})
            if path.endswith("/set-current"):
                assert body["release_revision"] == workflow["release"]
                workflow["release"] += 1
                workflow["current"] = int(path.split("/")[-2])
                return httpx.Response(200, json={"current_published_version": workflow["current"], "release_revision": workflow["release"]})
            if path.endswith("/publish"):
                version = len(workflow["versions"]) + 1
                workflow["versions"].append({"id": str(uuid4()), "workflow_id": identifier, "version": version,
                    "draft_revision": workflow["revision"], "model_id": self.model, "model_revision": self.model_revision,
                    "definition": copy.deepcopy(workflow["definition"]), "digest": str(version) * 64, "active": True})
                workflow["current"], workflow["release"] = version, workflow["release"] + 1
                return httpx.Response(201, json={"version": version})
            if path.endswith(("/test-runs", "/runs")):
                published = not path.endswith("test-runs")
                version = workflow["versions"][int(path.split("/")[-2]) - 1] if published else None
                definition = version["definition"] if published else workflow["definition"]
                run = body.get("run_id", str(uuid4()))
                self.events[run] = []
                status, output = "SUCCEEDED", None
                try:
                    output = self.run(definition, body["text"], run)
                except RuntimeError:
                    status = "FAILED"
                snapshot = {"definition": copy.deepcopy(definition), "revision": version["draft_revision"] if published else workflow["revision"],
                            "model_id": self.model}
                if published:
                    snapshot["published_version"] = version["version"]
                self.rows[run] = {"id": run, "workflow_id": identifier, "status": status, "snapshot": snapshot,
                                  "input": body["text"], "output": output, "error_node": "action" if status == "FAILED" else None}
                if self.case_id == "AET-F01":
                    self.traces[run] = {"run_id": run, "configuration_scope": {"mode": "workflow", "run_id": run}, "items": [{
                        "snapshot_id": str(uuid4()), "phase": 1, "resource_kind": "model", "resource_id": self.model,
                        "revision": str(self.model_revision), "state": "used", "used_at": "2026-11-04", "snapshot": {"temperature": self.temperature},
                        "calls": [{"call_id": str(uuid4()), "run_id": run, "mode": "workflow", "model_id": self.model,
                            "model_revision": self.model_revision, "status": "KNOWN", "input_tokens": 10, "output_tokens": 5,
                            "effective_parameters": {"temperature": self.temperature}}]}]}
                if published and self.lost:
                    raise httpx.ReadError("Remote accepted, response lost")
                if status == "FAILED":
                    if self.bad_502:
                        self.rows[run]["status"] = "RUNNING"
                    return httpx.Response(502, json={"error": {"code": "WORKFLOW_RUN_FAILED"}})
                return httpx.Response(200, json={"id": run, "status": status, "output": output})
            pytest.fail("Unexpected workflow mutation")
        if path.endswith("/configurations"):
            value = copy.deepcopy(self.traces[path.split("/")[-2]])
            if self.bad_trace and self.rows[value["run_id"]]["snapshot"].get("published_version"):
                self.bad_trace(value["items"][0])
            return httpx.Response(200, json=value)
        if path.endswith("/usage"):
            run = path.split("/")[-2]
            counts = dict.fromkeys(observation.TOTAL, 0)
            if run in self.traces:
                counts.update(calls=1, reported_calls=1, input_tokens=10, output_tokens=5)
            if self.unknown_usage:
                counts.update(reported_calls=0, unknown_calls=1, input_tokens=None, output_tokens=None)
            return httpx.Response(200, json={"complete": True, "scope": {"mode": "workflow", "run_id": run}, "total": counts})
        identifier = path.split("/")[4]
        workflow = self.workflows[identifier]
        if path.endswith("/versions"):
            versions = [{**row, "is_current": row["version"] == workflow["current"]} for row in workflow["versions"]]
            return httpx.Response(200, json={"items": versions, "current_published_version": workflow["current"], "release_revision": workflow["release"]})
        if "/versions/" in path:
            return httpx.Response(200, json=workflow["versions"][int(path.split("/")[-1]) - 1])
        if path.endswith("/runs"):
            return httpx.Response(200, json={"items": [row for key, row in self.rows.items() if key == request.url.params["run_id"]
                                                      and row["workflow_id"] == identifier], "next_cursor": None})
        if path.endswith("/events"):
            rows = copy.deepcopy(self.events[path.split("/")[-2]])
            if self.bad_event:
                rows[-1]["node_id"] = "fake"
            return httpx.Response(200, json={"items": rows})
        pytest.fail("Unexpected workflow observation")


def test_id_only_router_contract_retains_two_confirmed_initialization_runs(tmp_path, monkeypatch):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert len(fixture.rows) == 2
    data = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    assert attempt["fixture_intents"]["workflow-v2"]["response"] == {"id": next(iter(fixture.workflows))}
    assert attempt["fixture_intents"]["maintain-f01-model"]["response"] == {"id": fixture.model}
    assert all(row["status"] == "CONFIRMED" for row in attempt["fixture_intents"].values())
    assert attempt["started"] is False and attempt["versioned_baseline_ref"]


def command(tmp_path, monkeypatch, fixture, action):
    manifest = tmp_path / "target.json"
    atomic_json(manifest, fixture.target.model_dump())
    monkeypatch.setattr(fixed_run, "Platform", lambda value: Platform(value, transport=httpx.MockTransport(fixture.server)))
    monkeypatch.setattr(Platform, "login", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["fixed_run", action, "--case-id", fixture.case_id, "--output", str(tmp_path / "output"),
                                     "--target-manifest", str(manifest)])
    return fixed_run.main()


@pytest.mark.parametrize("case", ["AET-F01", "AET-F03"])
def test_complete_private_cli_real_evidence_offline_ready_and_confirmed_no_replay(tmp_path, monkeypatch, case):
    fixture = Fixture(case)
    for action in ("init", "run", "cleanup", "report"):
        assert command(tmp_path, monkeypatch, fixture, action) == 0
    report = json.loads((tmp_path / "output/report.json").read_text(encoding="utf-8"))
    attempt = report["attempts"][0]
    assert fixed_audit.validate_ready(tmp_path / "output")["gate"] == "PASS"
    assert len(fixture.rows) == (4 if case == "AET-F01" else 3)
    assert attempt["usage"]["calls"] == (4 if case == "AET-F01" else 0)
    assert attempt["usage"]["input_tokens"] == (40 if case == "AET-F01" else 0)
    assert attempt["usage"]["output_tokens"] == (20 if case == "AET-F01" else 0)
    assert attempt["platform_status"] == ("SUCCEEDED" if case == "AET-F01" else "FAILED")
    if case == "AET-F03":
        assert attempt["business_outcomes"] == [{"run_id": attempt["platform_root"]["id"], "status": "FAILED"}]
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    assert len(fixture.posts) == before


@pytest.mark.parametrize("fault", ["reference-int", "reference-leading-zero", "native-string", "native-bool", "native-float", "temperature", "not-used"])
def test_used_trace_wrong_typed_revision_or_effective_configuration_never_passes(tmp_path, monkeypatch, fault):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0

    def mutate(row):
        if fault == "reference-int":
            row["revision"] = 2
        elif fault == "reference-leading-zero":
            row["revision"] = "02"
        elif fault.startswith("native-"):
            row["calls"][0]["model_revision"] = {"native-string": "2", "native-bool": True, "native-float": 2.0}[fault]
        elif fault == "temperature":
            row["calls"][0]["effective_parameters"]["temperature"] = 0.2
        else:
            row["used_at"] = None
    fixture.bad_trace = mutate
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))["attempts"][0]["task_outcome"] != "PASS"


@pytest.mark.parametrize("case", ["AET-F01", "AET-F03"])
def test_unknown_committed_submission_is_never_automatically_reposted(tmp_path, monkeypatch, case):
    fixture = Fixture(case)
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.lost = True
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    count = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == count
    attempt = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))["attempts"][0]
    assert any(row["status"] == "UNKNOWN" for row in attempt["submission_intents"].values())
    assert attempt["cleanup_status"] == "NEEDS_REVIEW"


def test_502_with_running_root_is_unknown_and_not_a_known_child_failure(tmp_path, monkeypatch):
    fixture = Fixture("AET-F03")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.bad_502 = True
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    count = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == count
    attempt = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))["attempts"][0]
    assert attempt["submission_intents"]["submit-failure"]["status"] == "UNKNOWN"
    assert attempt["task_outcome"] != "PASS"


def test_502_missing_failed_prefix_remains_unknown_even_when_terminal_status_matches(tmp_path, monkeypatch):
    fixture = Fixture("AET-F03")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.bad_event = True
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    attempt = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))["attempts"][0]
    assert attempt["task_outcome"] == "NEEDS_REVIEW" and attempt["cleanup_status"] == "NEEDS_REVIEW"
    assert attempt["submission_intents"]["submit-failure"]["status"] == "UNKNOWN"


def test_current_selection_drift_stops_before_any_main_post(tmp_path, monkeypatch):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    next(iter(fixture.workflows.values()))["current"] = 2
    count = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == count


def test_unknown_initialization_usage_stops_before_publish_or_next_draft_test(tmp_path, monkeypatch):
    fixture = Fixture("AET-F01")
    fixture.unknown_usage = True
    assert command(tmp_path, monkeypatch, fixture, "init") != 0
    assert len(fixture.rows) == 1
    assert not any(path.endswith("/publish") for _, path, _ in fixture.posts)


def test_unknown_first_main_usage_stops_before_second_version_dispatch(tmp_path, monkeypatch):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.unknown_usage = True
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    main = [path for _, path, _ in fixture.posts if "/versions/" in path and path.endswith("/runs")]
    assert len(main) == 1 and main[0].endswith("/versions/1/runs")


@pytest.mark.parametrize("field", ["status", "path", "digest"])
def test_unknown_or_changed_initialization_intent_blocks_main_effects(tmp_path, monkeypatch, field):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    path = tmp_path / "output/journal.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["attempts"][0]["fixture_intents"]["maintain-f01-model"][field] = "UNKNOWN" if field == "status" else "changed"
    atomic_json(path, data)
    count = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == count


def test_wrong_actual_model_node_event_yields_business_fail_not_green(tmp_path, monkeypatch):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.bad_event = True
    assert command(tmp_path, monkeypatch, fixture, "run") == 1
    attempt = json.loads((tmp_path / "output/journal.json").read_text(encoding="utf-8"))["attempts"][0]
    assert attempt["task_outcome"] == "FAIL" and attempt["platform_status"] == "SUCCEEDED"


@pytest.mark.parametrize("case", ["AET-F01", "AET-F03"])
def test_dirty_owned_target_blocks_before_main_submission(tmp_path, monkeypatch, case):
    fixture = Fixture(case)
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.dirty = 1
    count = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == count


@pytest.mark.parametrize("error", [OSError, ValueError])
def test_evidence_alias_save_failure_poison_blocks_new_effects(tmp_path, monkeypatch, error):
    journal = Journal(tmp_path / "evidence")
    attempt = {"attempt_id": str(uuid4()), "case_id": "AET-F03", "repeat_index": 1}
    journal.data.update(owner_id=str(uuid4()), environment_id=str(uuid4()), target_digest="c" * 64,
                        suite_context={"repeat_index": 1, "suite_id": str(uuid4()), "catalog_digest": "a" * 64,
                                       "case_definition_digest": "b" * 64, "rules_id": "controlled-a20-v1", "candidate_commit": "a" * 40})
    monkeypatch.setattr(journal, "save", lambda: (_ for _ in ()).throw(error("controlled persistence failure")))
    try:
        with pytest.raises(error):
            facts.retain(journal, attempt, "baseline", {})
        assert attempt["versioned_evidence_failed"] is True
        with pytest.raises(Exception, match="persistence"):
            facts.baseline(None, journal, attempt, None)
    finally:
        journal.close()


@pytest.mark.parametrize("fault", ["owner", "repeat-bool", "missing-reference", "kind", "native-revision", "unknown-intent"])
def test_offline_same_reader_rejects_binding_references_and_native_contract_tampering(tmp_path, monkeypatch, fault):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    directory = tmp_path / "output"
    data = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
    attempt = data["attempts"][0]
    if fault == "owner":
        data["owner_id"] = str(uuid4())
    elif fault == "repeat-bool":
        data["suite_context"]["repeat_index"] = True
    elif fault == "missing-reference":
        attempt["evidence_refs"].pop(0)
    elif fault == "unknown-intent":
        attempt["submission_intents"]["submit-v1"]["status"] = "UNKNOWN"
    else:
        original = attempt["versioned_facts_ref"]
        value = fixed_evidence.load(directory, original, "versioned-facts")
        if fault == "kind":
            value["kind"] = "versioned-baseline"
        else:
            value["body"]["execution"][0]["trace"]["calls"][0]["model_revision"] = "2"
        reference = fixed_evidence.store(directory, "versioned-facts", value)
        attempt["versioned_facts_ref"] = reference
        attempt["evidence_refs"][-1] = reference
    specimen = next(case for case in fixed_run.catalog().cases if case.case_id == fixture.case_id)
    with pytest.raises((ValueError, RuntimeError)):
        facts.cleanup_ready(directory, data, attempt, specimen)


def test_cancelled_before_main_dispatch_does_not_create_a_root(tmp_path, monkeypatch):
    fixture = Fixture("AET-F01")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    (tmp_path / "output/cancel.request").touch()
    count = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == count and len(fixture.rows) == 2
