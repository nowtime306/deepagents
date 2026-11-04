"""Diagnostic snapshots never replace accepted evidence or settle unknown effects."""

import copy
import json

import pytest
from evaluation_runner import fixed_audit, network


def test_begin_invalidates_previous_receipts_without_changing_formal_report(tmp_path):
    (tmp_path / fixed_audit.READY).write_text("previous receipt", encoding="utf-8")
    (tmp_path / fixed_audit.PARTIAL).write_text("previous diagnostic", encoding="utf-8")
    (tmp_path / "report.json").write_text("previous report", encoding="utf-8")
    session = network.Session("run", {}, None)
    fixed_audit.begin(tmp_path, session)
    assert not (tmp_path / fixed_audit.READY).exists()
    assert not (tmp_path / fixed_audit.PARTIAL).exists()
    assert (tmp_path / "report.json").read_text(encoding="utf-8") == "previous report"
    assert fixed_audit.index(tmp_path)["sessions"][-1]["status"] == "PENDING"


def test_public_reader_rejects_missing_readiness_as_incomplete_publication(tmp_path):
    with pytest.raises(ValueError, match="Incomplete report publication"):
        fixed_audit.validate_ready(tmp_path)


@pytest.mark.parametrize("outcome,gate", [("FAIL", "FAIL"), ("NEEDS_REVIEW", "NEEDS_REVIEW"), ("PASS", "NEEDS_REVIEW")])
def test_error_diagnostic_preserves_latest_root_facts_and_unknown_usage(tmp_path, outcome, gate):
    session = network.Session("run", {}, None)
    fixed_audit.begin(tmp_path, session)
    fixed_audit.seal(tmp_path, session, "ERROR")
    data = {"attempts": [{"started": True, "platform_root": {"id": "acknowledged-root"},
                         "task_outcome": outcome, "fact_results": [{"status": outcome}],
                         "usage": {"unknown_calls": 1, "input_tokens": None}}]}
    original = copy.deepcopy(data)
    (tmp_path / "report.json").write_text("immutable prior formal report", encoding="utf-8")
    fixed_audit.publish_error(tmp_path, data, session, "RuntimeError")
    partial = json.loads((tmp_path / fixed_audit.PARTIAL).read_text(encoding="utf-8"))
    assert partial["publication_status"] == "INCOMPLETE" and partial["acceptance_ready"] is False
    assert partial["gate"] == gate and partial["attempts"] == original["attempts"]
    assert data == original and len(partial["network_audit_refs"]) == 1
    assert partial["audit_session_id"] == session.data["session_id"]
    assert (tmp_path / "report.json").read_text(encoding="utf-8") == "immutable prior formal report"
    assert not (tmp_path / fixed_audit.READY).exists()
    with pytest.raises(FileNotFoundError):
        fixed_audit._validate_ready(tmp_path)


def test_diagnostic_write_failure_cannot_issue_readiness(tmp_path, monkeypatch):
    session = network.Session("run", {}, None)
    fixed_audit.begin(tmp_path, session)
    fixed_audit.seal(tmp_path, session, "ERROR")
    original = fixed_audit.atomic_json

    def fail_diagnostic(path, data):
        if path.name == fixed_audit.PARTIAL:
            raise OSError("controlled write failure")
        original(path, data)

    monkeypatch.setattr(fixed_audit, "atomic_json", fail_diagnostic)
    with pytest.raises(OSError, match="controlled write failure"):
        fixed_audit.publish_error(tmp_path, {"attempts": []}, session, "RuntimeError")
    assert not (tmp_path / fixed_audit.READY).exists()
    assert not (tmp_path / fixed_audit.PARTIAL).exists()
