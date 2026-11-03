import json

import pytest
from test_fixed_evidence_retention import offline
from test_fixed_report_identity import saved_report

from evaluation_runner import fixed_audit, fixed_evidence
from evaluation_runner.journal import atomic_json


@pytest.mark.parametrize("mutation", ["snapshot_repeat", "attempt_repeat", "audit_repeat"])
def test_boolean_repeat_cannot_impersonate_integer_scope_in_retained_a02(tmp_path, monkeypatch, mutation):
    data = saved_report(tmp_path)
    attempt = data["attempts"][0]
    if mutation == "attempt_repeat":
        attempt["repeat_index"] = True
    elif mutation == "snapshot_repeat":
        reference = attempt["evidence_refs"][0]
        path = tmp_path / reference["path"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["binding"]["repeat_index"] = True
        atomic_json(path, value)
        reference["sha256"] = fixed_evidence.file_digest(path)
    else:
        index = fixed_audit.index(tmp_path)
        reference = index["sessions"][0]["reference"]
        path = tmp_path / reference["path"]
        value = fixed_evidence.load(tmp_path, reference, "network-audit")
        value["identity"]["repeat_index"] = True
        atomic_json(path, value)
        reference["sha256"] = fixed_evidence.file_digest(path)
        data["network_audit_refs"] = [reference]
        atomic_json(tmp_path / fixed_audit.INDEX, index)
    atomic_json(tmp_path / "journal.json", data)
    original = (tmp_path / "report.json").read_bytes()
    assert offline(tmp_path, monkeypatch) != 0
    assert (tmp_path / "report.json").read_bytes() == original
