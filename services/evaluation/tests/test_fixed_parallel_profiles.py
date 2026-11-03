"""F06 CLI/HTTP contract substitutes, not real workflow business acceptance."""

import json
from uuid import uuid4

import httpx
import pytest
from test_fixed_parallel_evidence import events
from test_fixed_workflow_profiles import FormalWorkflowFixture, command

from evaluation_runner import fixed_audit
from evaluation_runner import fixed_parallel_evidence as facts
from evaluation_runner import fixed_workflow_observation as observation


class ParallelFixture(FormalWorkflowFixture):
    def __init__(self):
        super().__init__("AET-F06")
        self.workflows = {}
        self.lost_submission = False

    def server(self, request):
        path = request.url.path
        if path.endswith("/environment"):
            return httpx.Response(200, json=self.proof)
        if path.endswith("/auth/me"):
            return httpx.Response(200, json={"is_admin": True})
        if path.endswith("/profiles/capabilities"):
            return httpx.Response(200, json={"observers": ["root-model-context-v1", "readonly-workflow-effects-v1"]})
        if path.endswith("/observations"):
            workflow = path.split("/")[-2]
            counts = dict.fromkeys(observation.COUNTS, 0)
            counts.update(workflow_runs=len(self.rows), workflow_events=sum(len(rows) for rows in self.events.values()),
                          work_tasks=self.extra_tasks)
            return httpx.Response(200, json={"schema_version": 1, "environment_id": self.target.environment_id,
                "owner_id": self.target.owner_id, "namespace": self.namespace, "case_id": self.case_id,
                "workflow_id": workflow, "complete": True, "run_ids": sorted(
                    identifier for identifier, row in self.rows.items() if row["workflow_id"] == workflow),
                "counts": counts, "provider": {"observed": True, "complete": True, "calls": 0, "pending": 0, "unknown_roots": 0},
                "sandbox": {"observed": True, "complete": True, "attempts": 0}})
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append((path, body))
            if path == "/api/v1/admin/models":
                return httpx.Response(201, json={"id": self.ids["model"]})
            if path.endswith("/model-bindings"):
                return httpx.Response(200, json={"sealed": True})
            if path == "/api/v1/workflows":
                identifier = str(uuid4())
                self.workflows[identifier] = {"id": str(uuid4()), "workflow_id": identifier,
                    "model_id": self.ids["model"], "version": 1, "draft_revision": 1, "active": True,
                    "digest": "a" * 64, "definition": body["definition"]}
                return httpx.Response(201, json={"id": identifier})
            identifier = path.split("/")[4]
            version = self.workflows[identifier]
            if path.endswith("/publish"):
                return httpx.Response(201, json={"version": 1})
            if path.endswith(("/runs", "/test-runs")):
                run_id = body.get("run_id", str(uuid4()))
                published = "test-runs" not in path
                definition = version["definition"]
                operation = definition["nodes"][1]["parameters"].get("operation")
                output = facts.TEXT.lower() if operation == "lower" else facts.TEXT.upper() if operation == "upper" else facts.OUTPUT
                snapshot = {"model_id": self.ids["model"], "revision": 1, "definition": definition}
                if published:
                    snapshot["published_version"] = 1
                self.rows[run_id] = {"id": run_id, "workflow_id": identifier, "status": "SUCCEEDED",
                                     "input": body["text"], "output": output, "snapshot": snapshot}
                self.events[run_id] = [{"event_id": row["event_id"], "event": row["event"], "node_id": row["node_id"],
                    "data": {"kind": row["kind"], "input": row["input"]["declared"] if row["input"] else None,
                             "output": row["output"]["declared"] if row["output"] else None}}
                    for row in events(operation, interleaved=operation is None)]
                if published and self.lost_submission:
                    raise httpx.ReadError("Accepted root, response lost")
                return httpx.Response(200, json={"id": run_id, "status": "SUCCEEDED", "output": output})
            pytest.fail("Unexpected F06 mutation")
        if path.endswith("/usage"):
            return httpx.Response(200, json={"total": dict.fromkeys(observation.TOTAL, 0), "complete": True,
                                           "scope": {"mode": "workflow", "run_id": path.split("/")[-2]}})
        identifier = path.split("/")[4]
        version = self.workflows[identifier]
        if path.endswith("/versions"):
            return httpx.Response(200, json={"items": [version]})
        if "/versions/" in path:
            return httpx.Response(200, json=version)
        if path.endswith("/runs"):
            return httpx.Response(200, json={"items": [row for key, row in self.rows.items()
                if key == request.url.params["run_id"] and row["workflow_id"] == identifier], "next_cursor": None})
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": self.events[path.split("/")[-2]]})
        pytest.fail("Unexpected F06 read route")


def test_formal_four_roots_retained_ready_offline_recompute_and_no_confirmed_replay(tmp_path, monkeypatch):
    fixture = ParallelFixture()
    for action in ("init", "run", "cleanup", "report"):
        assert command(tmp_path, monkeypatch, fixture, action) == 0
    assert len(fixture.workflows) == 3 and len(fixture.rows) == 4
    assert fixed_audit.validate_ready(tmp_path / "output")["gate"] == "PASS"
    report = json.loads((tmp_path / "output/report.json").read_text(encoding="utf-8"))
    attempt = report["attempts"][0]
    assert attempt["fact_results"][0]["registered_id"] == "all_branches_join"
    assert attempt["usage"] == dict.fromkeys(observation.TOTAL, 0)
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    assert len(fixture.posts) == before


def test_lost_response_is_never_resubmitted_or_called_clean(tmp_path, monkeypatch):
    fixture = ParallelFixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.lost_submission = True
    assert command(tmp_path, monkeypatch, fixture, "run") == 1
    before = len(fixture.posts)
    assert command(tmp_path, monkeypatch, fixture, "run") == 1
    assert len(fixture.posts) == before and len(fixture.rows) == 4
    assert command(tmp_path, monkeypatch, fixture, "cleanup") == 1
    report = json.loads((tmp_path / "output/report.json").read_text(encoding="utf-8"))
    assert report["attempts"][0]["cleanup_status"] == "NEEDS_REVIEW"


def test_changed_owned_effects_stop_before_formal_main_submission(tmp_path, monkeypatch):
    fixture = ParallelFixture()
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    fixture.extra_tasks = 1
    # Re-read current effects is required before the next formal write, not just a stored clean baseline.
    assert command(tmp_path, monkeypatch, fixture, "run") == 1
    assert len(fixture.rows) == 3
