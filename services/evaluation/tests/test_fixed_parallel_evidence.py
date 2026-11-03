"""F06 protocol evidence controls; no real workflow/queue/model execution."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from evaluation_runner import fixed_parallel_evidence as facts
from evaluation_runner import fixed_workflow_observation as observation
from evaluation_runner.adapters import Blocked
from evaluation_runner.fixed_evidence import binding
from evaluation_runner.journal import Journal, digest


def item(operation=None):
    value = {key: str(uuid4()) for key in ("id", "version_id", "init_run_id")}
    value.update(version=1, version_digest="a" * 64)
    if operation:
        value["operation"] = operation
    return value


def events(operation=None, *, interleaved=False):
    output = "mixed" if operation == "lower" else "MIXED" if operation == "upper" else facts.OUTPUT
    pairs = [("start", "start", facts.TEXT, facts.TEXT), ("action", "transform" if operation else "parallel", facts.TEXT, output)]
    rows = []

    def append(node, kind, event, value):
        rows.append({"event_id": len(rows) + 1, "node_id": node, "kind": kind, "event": event,
                     "input": facts.text(value) if event == "node.started" else None,
                     "output": facts.text(value) if event == "node.completed" else None})

    for node, kind, source, result in pairs:
        append(node, kind, "node.started", source)
        if node == "action" and operation is None:
            branches = []
            for index, end in enumerate(("mixed", "MIXED", "mixed")):
                branches.append([(f"action.{index}.{n}", k, event, value)
                                 for n, k, start, stop in (("start", "start", facts.TEXT, facts.TEXT),
                                                          ("action", "transform", facts.TEXT, end), ("end", "end", end, end))
                                 for event, value in (("node.started", start), ("node.completed", stop))])
            ordered = [entry for group in (zip(*branches, strict=True) if interleaved else branches) for entry in group]
            for entry in ordered:
                append(*entry)
        append(node, kind, "node.completed", result)
    append("end", "end", "node.started", output)
    append("end", "end", "node.completed", output)
    return rows


def record(fixture, definition, identifier=None, *, published=False, interleaved=False):
    base = "/api/v1/workflows/" + fixture["id"]
    identifier = identifier or fixture["init_run_id"]
    operation = fixture.get("operation")
    output = facts.TEXT.lower() if operation == "lower" else facts.TEXT.upper() if operation == "upper" else facts.OUTPUT
    return {"id": identifier, "workflow_id": fixture["id"], "status": "SUCCEEDED", "source": base + "/runs",
            "events_source": base + f"/runs/{identifier}/events", "version_id": fixture["version_id"],
            "version": 1 if published else None, "business_digest": fixture["version_digest"],
            "definition_digest": digest(definition), "input": facts.text(facts.TEXT), "output": facts.text(output),
            "events": events(operation, interleaved=interleaved), "usage": dict.fromkeys(observation.TOTAL, 0)}


def body_fixture():
    fixture = {"model_id": str(uuid4()), "children": [item("lower"), item("upper")], **item()}
    attempt = {"attempt_id": str(uuid4()), "case_id": "AET-F06", "fixture": fixture,
               "sandbox_namespace": str(uuid4()), "client_run_id": str(uuid4())}
    data = {"environment_id": str(uuid4()), "owner_id": str(uuid4()), "target_digest": "b" * 64,
            "suite_context": {"repeat_index": 1, "suite_id": str(uuid4()), "catalog_digest": "c" * 64,
                              "case_definition_digest": "d" * 64, "rules_id": "controlled-a20-v1", "candidate_commit": "e" * 40}}
    initialization = [record(child, definition) for child, definition in facts.specifications(fixture)]
    root_id = attempt["client_run_id"]
    execution = record(fixture, facts.specifications(fixture)[-1][1], root_id, published=True, interleaved=True)
    path = f"/api/v1/workflows/{fixture['id']}/versions/1/runs"
    attempt["platform_root"] = {"mode": "workflow", "id": root_id, "fixture_id": fixture["id"],
                                "path": f"/api/v1/workflows/{fixture['id']}/runs"}
    attempt["submission_intents"] = {"submit-run": {"status": "CONFIRMED", "path": path,
        "payload_digest": digest({"run_id": root_id, "text": facts.TEXT}), "response": {"id": root_id}}}

    def effects(records, ids):
        counts = dict.fromkeys(observation.COUNTS, 0)
        counts.update(workflow_runs=len(records), workflow_events=sum(len(row["events"]) for row in records))
        return {"schema_version": 1, "environment_id": data["environment_id"], "owner_id": data["owner_id"],
                "namespace": attempt["sandbox_namespace"], "case_id": "AET-F06", "workflow_id": fixture["id"],
                "complete": True, "run_ids": sorted(ids), "counts": counts,
                "provider": {"observed": True, "complete": True, "calls": 0, "pending": 0, "unknown_roots": 0},
                "sandbox": {"observed": True, "complete": True, "attempts": 0}}

    before = {"initialization": initialization, "effects": effects(initialization, [fixture["init_run_id"]])}
    body = {"baseline": before, "execution": execution,
            "after": effects([*initialization, execution], [fixture["init_run_id"], root_id])}
    snapshot = {"schema_version": 1, "kind": "parallel-facts", "binding": binding(data, attempt), "body": body,
                "usage": facts.aggregate(body)}
    return data, attempt, snapshot, SimpleNamespace(case_id="AET-F06")


@pytest.mark.parametrize("interleaved", [False, True])
def test_complete_branch_paths_accept_serial_or_interleaved_siblings(interleaved):
    _, attempt, snapshot, specimen = body_fixture()
    snapshot["body"]["execution"]["events"] = events(interleaved=interleaved)
    results, evidence = facts.evaluate(snapshot, specimen, attempt)
    assert results[0].status == "PASS"
    assert evidence["usage"]["total"] == dict.fromkeys(observation.TOTAL, 0)
    assert len(snapshot["body"]["execution"]["events"]) == 24


@pytest.mark.parametrize("change", ["missing", "input", "output", "duplicate", "early_join", "wrong_order"])
def test_correct_final_array_does_not_hide_incorrect_branch_process(change):
    _, attempt, snapshot, specimen = body_fixture()
    rows = snapshot["body"]["execution"]["events"]
    if change == "missing":
        rows[:] = [row for row in rows if not row["node_id"].startswith("action.2.")]
    elif change in {"input", "output"}:
        event = next(row for row in rows if row["node_id"] == "action.1.action"
                     and row["event"] == ("node.started" if change == "input" else "node.completed"))
        event[change] = facts.text("mixed")
    elif change == "duplicate":
        rows[-1] = {**rows[-2], "event_id": rows[-1]["event_id"]}
    else:
        name = "action" if change == "early_join" else "action.0.end"
        index = next(i for i, row in enumerate(rows) if row["node_id"] == name and row["event"] == "node.completed")
        rows.insert(3, rows.pop(index))
        for i, row in enumerate(rows, 1):
            row["event_id"] = i
    snapshot["body"]["after"]["counts"]["workflow_events"] = sum(
        len(row["events"]) for row in snapshot["body"]["baseline"]["initialization"]) + len(rows)
    results, _ = facts.evaluate(snapshot, specimen, attempt)
    assert results[0].status == "FAIL"


@pytest.mark.parametrize("change", ["version", "scope", "unknown", "bool", "extra_root", "provider", "sandbox", "intent"])
def test_wrong_identity_or_unsettled_effects_cannot_be_accepted(change):
    _, attempt, snapshot, specimen = body_fixture()
    row, after = snapshot["body"]["execution"], snapshot["body"]["after"]
    if change == "version":
        row["version"] = True
    elif change == "scope":
        after["owner_id"] = str(uuid4())
    elif change == "unknown":
        row["usage"]["unknown_calls"] = None
    elif change == "bool":
        row["usage"]["calls"] = False
    elif change == "extra_root":
        after["run_ids"].append(str(uuid4()))
    elif change in {"provider", "sandbox"}:
        after[change]["calls" if change == "provider" else "attempts"] = 1
    else:
        attempt["submission_intents"]["submit-run"]["status"] = "UNKNOWN"
    with pytest.raises((ValueError, Blocked)):
        facts.evaluate(snapshot, specimen, attempt)


def test_retained_baseline_and_final_are_recomputed_and_tamper_rejected(tmp_path):
    data, attempt, snapshot, specimen = body_fixture()
    journal = Journal(tmp_path)
    journal.data = data
    data["attempts"] = [attempt]
    try:
        facts.retain(journal, attempt, "parallel-baseline", snapshot["body"]["baseline"])
        facts.retain(journal, attempt, "parallel-facts", snapshot["body"])
        assert facts.cleanup_ready(tmp_path, data, attempt, specimen)
        reference = attempt["evidence_refs"][-1]
        (tmp_path / reference["path"]).write_bytes(b"{}")
        with pytest.raises(ValueError, match="changed evidence"):
            facts.verified(tmp_path, data, attempt, specimen)
    finally:
        journal.close()


@pytest.mark.parametrize("exception", [OSError, ValueError])
def test_evidence_save_failure_blocks_next_dispatch_before_http(tmp_path, monkeypatch, exception):
    data, attempt, snapshot, specimen = body_fixture()
    journal = Journal(tmp_path)
    journal.data = data
    try:
        monkeypatch.setattr(journal, "save", lambda: (_ for _ in ()).throw(exception("save failed")))
        with pytest.raises(exception):
            facts.retain(journal, attempt, "parallel-baseline", snapshot["body"]["baseline"])
        assert attempt["parallel_evidence_failed"] is True
        with pytest.raises(Blocked, match="persistence failed"):
            facts.execute(None, journal, attempt, specimen)
    finally:
        journal.close()


def test_reference_kind_change_cannot_authorize_fact_recompute_or_clean(tmp_path):
    data, attempt, snapshot, specimen = body_fixture()
    journal = Journal(tmp_path)
    journal.data = data
    try:
        facts.retain(journal, attempt, "parallel-baseline", snapshot["body"]["baseline"])
        facts.retain(journal, attempt, "parallel-facts", snapshot["body"])
        attempt["evidence_refs"][-1]["kind"] = "parallel-baseline"
        for check in (facts.verified, facts.cleanup_ready):
            with pytest.raises(ValueError, match="identity differs"):
                check(tmp_path, data, attempt, specimen)
    finally:
        journal.close()


def test_missing_or_wrong_kind_baseline_does_not_authorize_cleanup(tmp_path):
    data, attempt, snapshot, specimen = body_fixture()
    journal = Journal(tmp_path)
    journal.data = data
    try:
        with pytest.raises(Blocked, match="baseline"):
            facts.verified(tmp_path, data, attempt, specimen)
        facts.retain(journal, attempt, "parallel-facts", snapshot["body"])
        attempt["parallel_baseline_ref"] = attempt["evidence_refs"][0]
        with pytest.raises(ValueError, match="not a baseline"):
            facts.baseline(None, journal, attempt, specimen)
    finally:
        journal.close()


def test_known_failed_root_is_a_failed_task_even_with_no_model_cost():
    _, attempt, snapshot, specimen = body_fixture()
    snapshot["body"]["execution"]["status"] = "FAILED"
    snapshot["body"]["execution"]["output"] = None
    result, _ = facts.evaluate(snapshot, specimen, attempt)
    assert result[0].status == "FAIL"
