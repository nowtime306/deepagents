import json
from uuid import uuid4

import portalocker
import pytest
from test_fixed_evidence_retention import offline
from test_fixed_report_identity import saved_report

from evaluation_runner import fixed_audit, fixed_evidence, network
from evaluation_runner.journal import Journal, atomic_json


@pytest.mark.parametrize("mutation", ["random_session", "empty_refs", "report_refs", "later_error", "later_pending"])
def test_ready_cannot_borrow_stale_or_incomplete_audit_index(tmp_path, monkeypatch, mutation):
    saved_report(tmp_path)
    assert offline(tmp_path, monkeypatch) == 0
    ready = fixed_audit.validate_ready(tmp_path)
    if mutation == "random_session":
        ready["session_id"] = str(uuid4())
    elif mutation == "empty_refs":
        ready["network_audit_refs"] = []
    elif mutation == "report_refs":
        report_path = tmp_path / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["attempts"][0]["network_audit_refs"] = []
        atomic_json(report_path, report)
        ready["files"]["report.json"] = fixed_evidence.file_digest(report_path)
    else:
        session = network.Session("cleanup", fixed_audit.export_identity(ready), None)
        fixed_audit.begin(tmp_path, session)
        if mutation == "later_error":
            fixed_audit.seal(tmp_path, session, "ERROR")
    atomic_json(tmp_path / fixed_audit.READY, ready)
    with pytest.raises(ValueError):
        fixed_audit.validate_ready(tmp_path)


@pytest.mark.parametrize("field", ["environment_id", "owner_id", "target_digest", "case_id", "catalog_digest",
                                   "case_definition_digest", "candidate_commit", "suite_id", "repeat_index", "rules_id"])
def test_current_export_must_match_the_whole_execution_identity(tmp_path, monkeypatch, field):
    saved_report(tmp_path)
    assert offline(tmp_path, monkeypatch) == 0
    ready = fixed_audit.validate_ready(tmp_path)
    audit_index = fixed_audit.index(tmp_path)
    reference = audit_index["sessions"][-1]["reference"]
    value = fixed_evidence.load(tmp_path, reference, "network-audit")
    value["identity"][field] = 2 if field == "repeat_index" else "different"
    path = tmp_path / reference["path"]
    atomic_json(path, value)
    reference["sha256"] = fixed_evidence.file_digest(path)
    atomic_json(tmp_path / fixed_audit.INDEX, audit_index)
    ready["network_audit_refs"][-1] = reference
    report_path = tmp_path / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["attempts"][0]["network_audit_refs"][-1] = reference
    atomic_json(report_path, report)
    ready["files"]["report.json"] = fixed_evidence.file_digest(report_path)
    atomic_json(tmp_path / fixed_audit.READY, ready)
    with pytest.raises(ValueError, match="identity-bound export"):
        fixed_audit.validate_ready(tmp_path)


@pytest.mark.parametrize("mutation", ["missing_export", "wrong_action", "error", "denied_socket"])
def test_latest_receipt_requires_a_complete_successful_export_session(tmp_path, monkeypatch, mutation):
    saved_report(tmp_path)
    assert offline(tmp_path, monkeypatch) == 0
    ready = fixed_audit.validate_ready(tmp_path)
    audit_index = fixed_audit.index(tmp_path)
    reference = audit_index["sessions"][-1]["reference"]
    value = fixed_evidence.load(tmp_path, reference, "network-audit")
    if mutation == "missing_export":
        value["phases"] = [row for row in value["phases"] if row["name"] != "export"]
    elif mutation == "wrong_action":
        value["action"] = "preflight"
    elif mutation == "error":
        value["status"] = "ERROR"
    else:
        value["events"] = [{"phase": "export", "event": "socket.connect", "allowed": False}]
    path = tmp_path / reference["path"]
    atomic_json(path, value)
    reference["sha256"] = fixed_evidence.file_digest(path)
    atomic_json(tmp_path / fixed_audit.INDEX, audit_index)
    ready["network_audit_refs"][-1] = reference
    report_path = tmp_path / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["attempts"][0]["network_audit_refs"][-1] = reference
    atomic_json(report_path, report)
    ready["files"]["report.json"] = fixed_evidence.file_digest(report_path)
    atomic_json(tmp_path / fixed_audit.READY, ready)
    with pytest.raises(ValueError, match="identity-bound export"):
        fixed_audit.validate_ready(tmp_path)


def test_report_export_receipt_records_suite_and_repeat_identity(tmp_path, monkeypatch):
    data = saved_report(tmp_path)
    assert offline(tmp_path, monkeypatch) == 0
    ready = fixed_audit.validate_ready(tmp_path)
    value = fixed_evidence.load(tmp_path, ready["network_audit_refs"][-1], "network-audit")
    assert value["identity"]["suite_id"] == data["suite_context"]["suite_id"]
    assert value["identity"]["repeat_index"] == data["suite_context"]["repeat_index"]


def test_ready_reader_cannot_interleave_an_active_cli_index_update(tmp_path, monkeypatch):
    saved_report(tmp_path)
    assert offline(tmp_path, monkeypatch) == 0
    journal = Journal(tmp_path)
    try:
        with pytest.raises(portalocker.exceptions.LockException):
            fixed_audit.validate_ready(tmp_path)
    finally:
        journal.close()
    assert fixed_audit.validate_ready(tmp_path)["gate"] == "PASS"
