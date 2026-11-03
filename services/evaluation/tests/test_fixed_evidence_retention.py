import copy
import hashlib
import io
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from test_fixed_report_identity import saved_report

from evaluation_runner import adapters, fixed_audit, fixed_evidence, fixed_run
from evaluation_runner.journal import Journal, atomic_json


def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fixed_run", "report", "--case-id", "AET-A02", "--output", str(tmp_path)])
    monkeypatch.setattr(fixed_run, "Platform", lambda *_: pytest.fail("Offline facts cannot be reconstructed by HTTP"))
    return fixed_run.main()


@pytest.mark.parametrize("mutation", ["missing", "deleted", "tampered", "traversal", "absolute", "cross_attempt", "extra_payload"])
def test_absent_or_invalid_retained_facts_cannot_reexport_pass(tmp_path, monkeypatch, mutation):
    data = saved_report(tmp_path)
    attempt = data["attempts"][0]
    reference = attempt["evidence_refs"][0]
    path = tmp_path / reference["path"]
    if mutation == "missing":
        attempt.pop("evidence_refs")
    elif mutation == "deleted":
        path.unlink()
    elif mutation == "tampered":
        path.write_text("{}", encoding="utf-8")
    elif mutation == "traversal":
        reference["path"] = "../outside.json"
    elif mutation == "absolute":
        reference["path"] = str(path)
    else:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if mutation == "cross_attempt":
            snapshot["binding"]["attempt_id"] = str(uuid4())
        else:
            snapshot["tool_raw_output"] = "forbidden arbitrary response"
        atomic_json(path, snapshot)
        reference["sha256"] = fixed_evidence.file_digest(path)
    atomic_json(tmp_path / "journal.json", data)
    original = (tmp_path / "report.json").read_bytes()
    assert offline(tmp_path, monkeypatch) != 0
    assert (tmp_path / "report.json").read_bytes() == original
    assert not (tmp_path / fixed_audit.READY).exists()


@pytest.mark.parametrize("mutation", ["output", "input", "context", "prior_status", "usage", "zero_usage"])
def test_facts_are_recomputed_instead_of_trusting_saved_pass_assertions(tmp_path, monkeypatch, mutation):
    data = saved_report(tmp_path)
    reference = data["attempts"][0]["evidence_refs"][0]
    path = tmp_path / reference["path"]
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "output":
        snapshot["messages"][-1]["output_digest"] = "0" * 64
        snapshot["messages"][-1]["declared_output"] = None
    elif mutation == "input":
        snapshot["messages"][0]["text_sha256"] = "0" * 64
    elif mutation == "context":
        snapshot["observer"]["context_assertions"][0]["human_context_digest"] = "0" * 64
    elif mutation == "prior_status":
        snapshot["runs"][0]["status"] = "FAILED"
    elif mutation == "usage":
        snapshot["usage"]["unknown_calls"] = 1
    else:
        snapshot["usage"].update(calls=0, reported_calls=0, input_tokens=0, output_tokens=0)
    atomic_json(path, snapshot)
    reference["sha256"] = fixed_evidence.file_digest(path)
    atomic_json(tmp_path / "journal.json", data)
    assert offline(tmp_path, monkeypatch) != 0


def test_independent_reader_rechecks_facts_and_all_three_export_hashes(tmp_path, monkeypatch):
    saved_report(tmp_path)
    assert offline(tmp_path, monkeypatch) == 0
    assert fixed_audit.validate_ready(tmp_path)["gate"] == "PASS"
    (tmp_path / "results.xml").write_text("partial export", encoding="utf-8")
    with pytest.raises(ValueError, match="Partial or changed"):
        fixed_audit.validate_ready(tmp_path)


def test_snapshot_write_failure_never_creates_reference_or_verdict(tmp_path, monkeypatch):
    data = saved_report(tmp_path)
    attempt = data["attempts"][0]
    reference = attempt["evidence_refs"][0]
    snapshot = fixed_evidence.load(tmp_path, reference, "a02-facts")
    monkeypatch.setattr(fixed_evidence, "atomic_json", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    before = copy.deepcopy(attempt)
    with pytest.raises(OSError):
        fixed_evidence.store(tmp_path, "a02-facts", snapshot)
    assert attempt == before


@pytest.mark.parametrize("failure", ["audit", "xml", "ready"])
def test_failed_export_or_audit_seal_cannot_publish_a_new_valid_pass(tmp_path, monkeypatch, failure):
    saved_report(tmp_path)
    if failure == "audit":
        monkeypatch.setattr(fixed_audit, "store", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    elif failure == "xml":
        original = fixed_audit.write_report

        def interrupted(directory, data):
            original(directory, data)
            raise OSError("export interrupted")

        monkeypatch.setattr(fixed_audit, "write_report", interrupted)
    else:
        original = fixed_audit.atomic_json

        def fail_ready(path, value):
            if path.name == fixed_audit.READY:
                raise OSError("final receipt failed")
            original(path, value)

        monkeypatch.setattr(fixed_audit, "atomic_json", fail_ready)
    assert offline(tmp_path, monkeypatch) != 0
    assert not (tmp_path / fixed_audit.READY).exists()


def test_missing_facts_prevents_cleanup_even_if_remote_is_terminal(tmp_path):
    data = saved_report(tmp_path)
    journal = Journal(tmp_path)
    attempt = data["attempts"][0]
    attempt["evidence_refs"] = []
    journal.data = data
    try:
        with pytest.raises(ValueError, match="not retained"):
            fixed_run.cleanup(object(), journal, attempt)
    finally:
        journal.close()


@pytest.mark.parametrize("mutation", ["missing_run", "missing_metric", "missing_import", "changed_hash", "pending", "lost_old_ref", "missing_score"])
def test_run_audit_is_required_and_cleanup_cannot_replace_it(tmp_path, monkeypatch, mutation):
    data = saved_report(tmp_path)
    audit_index = fixed_audit.index(tmp_path)
    reference = audit_index["sessions"][0]["reference"]
    path = tmp_path / reference["path"]
    value = fixed_evidence.load(tmp_path, reference, "network-audit")
    if mutation == "missing_run":
        data["attempts"][0].pop("run_audit_session_id")
    elif mutation == "missing_score":
        data["attempts"][0].pop("metric_results")
    elif mutation in {"missing_metric", "missing_import"}:
        value["phases"] = [row for row in value["phases"] if row["name"] != mutation.removeprefix("missing_")]
        atomic_json(path, value)
        reference["sha256"] = fixed_evidence.file_digest(path)
        data["network_audit_refs"] = [reference]
    elif mutation == "changed_hash":
        path.write_text("{}", encoding="utf-8")
    elif mutation == "pending":
        audit_index["sessions"].append({"session_id": str(uuid4()), "status": "PENDING"})
    else:
        audit_index["sessions"] = []
    atomic_json(tmp_path / fixed_audit.INDEX, audit_index)
    atomic_json(tmp_path / "journal.json", data)
    assert offline(tmp_path, monkeypatch) != 0


def test_report_appends_audit_sessions_and_retains_run_metric_and_zero_event_phases(tmp_path, monkeypatch):
    data = saved_report(tmp_path)
    old = copy.deepcopy(data["network_audit_refs"])
    assert offline(tmp_path, monkeypatch) == 0
    assert offline(tmp_path, monkeypatch) == 0
    current = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
    assert len(current["network_audit_refs"]) == 3
    assert current["network_audit_refs"][:1] == old
    receipts = [fixed_evidence.load(tmp_path, reference, "network-audit") for reference in current["network_audit_refs"]]
    assert [row["action"] for row in receipts] == ["run", "report", "report"]
    assert {row["name"] for row in receipts[0]["phases"]} == {"execution", "import", "metric", "export"}
    assert all(row["event_count"] == 0 and row["status"] == "COMPLETE" for row in receipts[-1]["phases"])
    assert "OS" in receipts[-1]["scope"]


def test_cleanup_appends_its_own_audit_but_requires_the_original_complete_run(tmp_path, monkeypatch):
    from test_core import target

    from evaluation_runner.journal import digest

    data = saved_report(tmp_path)
    target_value = target().model_copy(update={"owner_id": data["owner_id"], "environment_id": data["environment_id"]})
    target_digest = digest(target_value.model_dump())
    data["target_digest"] = target_digest
    attempt = data["attempts"][0]
    attempt["usage_completeness"] = "KNOWN"
    facts_ref = attempt["evidence_refs"][0]
    facts_path = tmp_path / facts_ref["path"]
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    facts["binding"]["target_digest"] = target_digest
    atomic_json(facts_path, facts)
    facts_ref["sha256"] = fixed_evidence.file_digest(facts_path)
    audit_index = fixed_audit.index(tmp_path)
    audit_ref = audit_index["sessions"][0]["reference"]
    audit_path = tmp_path / audit_ref["path"]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["identity"]["target_digest"] = target_digest
    atomic_json(audit_path, audit)
    audit_ref["sha256"] = fixed_evidence.file_digest(audit_path)
    data["network_audit_refs"] = [audit_ref]
    atomic_json(tmp_path / fixed_audit.INDEX, audit_index)
    atomic_json(tmp_path / "journal.json", data)
    target_path = tmp_path / "target.json"
    atomic_json(target_path, target_value.model_dump())

    class TerminalPlatform:
        def __init__(self, _):
            pass

        def login(self):
            pass

        def get(self, path):
            return {"id": path.rsplit("/", 1)[-1], "status": "SUCCEEDED"}

        def close(self):
            pass

    monkeypatch.setattr(fixed_run, "Platform", TerminalPlatform)
    monkeypatch.setattr(sys, "argv", ["fixed_run", "cleanup", "--case-id", "AET-A02", "--output", str(tmp_path),
                                     "--target-manifest", str(target_path)])
    assert fixed_run.main() == 0
    assert fixed_audit.validate_ready(tmp_path)["gate"] == "PASS"
    receipts = [fixed_evidence.load(tmp_path, row["reference"], "network-audit")
                for row in fixed_audit.index(tmp_path)["sessions"]]
    assert [row["action"] for row in receipts] == ["run", "cleanup"]


def test_snapshot_parses_the_same_single_bounded_read_that_was_hashed(tmp_path, monkeypatch):
    reference = fixed_evidence.store(tmp_path, "a02-facts", {"verified": "原始证据"})
    path = tmp_path / reference["path"]
    original_payload = path.read_bytes()
    original_open, reads = Path.open, []

    class ReplacingStream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            value = super().read(size)
            with original_open(path, "wb") as replacement:
                replacement.write(b'{"unverified":"replacement"}')
            return value

    def open_snapshot(value, *args, **kwargs):
        if value == path and args == ("rb",):
            return ReplacingStream(original_payload)
        return original_open(value, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_snapshot)
    assert fixed_evidence.load(tmp_path, reference, "a02-facts") == {"verified": "原始证据"}
    assert reads == [131073]


def test_snapshot_oversize_is_rejected_after_one_bounded_read(tmp_path, monkeypatch):
    reference = fixed_evidence.store(tmp_path, "a02-facts", {})
    path = tmp_path / reference["path"]
    payload = b" " * 150000
    path.write_bytes(payload)
    reference["sha256"] = hashlib.sha256(payload).hexdigest()
    original_open, reads = Path.open, []

    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda value, *args, **kwargs: BoundedStream(payload)
                        if value == path and args == ("rb",) else original_open(value, *args, **kwargs))
    with pytest.raises(ValueError, match="oversized"):
        fixed_evidence.load(tmp_path, reference, "a02-facts")
    assert reads == [131073]


@pytest.mark.parametrize("missing", ["empty", "wrong_root"])
def test_missing_root_context_is_blocked_instead_of_a_known_factual_failure(tmp_path, missing):
    data = saved_report(tmp_path)
    attempt = data["attempts"][0]
    snapshot = fixed_evidence.load(tmp_path, attempt["evidence_refs"][0], "a02-facts")
    if missing == "empty":
        snapshot["observer"]["context_assertions"] = []
    else:
        snapshot["observer"]["context_assertions"][0]["run_id"] = str(uuid4())
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A02")
    with pytest.raises(adapters.Blocked, match="context assertion is unavailable"):
        fixed_evidence.evaluate(snapshot, specimen, attempt)


def test_observed_wrong_context_remains_a_definite_fact_failure(tmp_path):
    data = saved_report(tmp_path)
    attempt = data["attempts"][0]
    snapshot = fixed_evidence.load(tmp_path, attempt["evidence_refs"][0], "a02-facts")
    snapshot["observer"]["context_assertions"][0]["has_project_context"] = False
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-A02")
    facts, _ = fixed_evidence.evaluate(snapshot, specimen, attempt)
    assert next(row.status for row in facts if row.registered_id == "received_context") == "FAIL"


def test_offline_report_rejects_old_tool_pass_when_retained_messages_contain_tools(tmp_path, monkeypatch):
    data = saved_report(tmp_path)
    attempt = data["attempts"][0]
    reference = attempt["evidence_refs"][0]
    snapshot = fixed_evidence.load(tmp_path, reference, "a02-facts")
    snapshot["messages"][-1]["has_tools"] = True
    path = tmp_path / reference["path"]
    atomic_json(path, snapshot)
    reference["sha256"] = fixed_evidence.file_digest(path)
    atomic_json(tmp_path / "journal.json", data)
    original = (tmp_path / "report.json").read_bytes()
    assert offline(tmp_path, monkeypatch) != 0
    assert (tmp_path / "report.json").read_bytes() == original
    assert not (tmp_path / fixed_audit.READY).exists()
