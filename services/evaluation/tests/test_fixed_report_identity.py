import copy
import json
import sys
from uuid import uuid4

import pytest

from evaluation_runner import fixed_run
from evaluation_runner.journal import Journal, atomic_json


def saved_report(directory):
    dataset = fixed_run.catalog()
    specimen = next(row for row in dataset.cases if row.case_id == "AET-A02")
    descriptor = fixed_run.describe(specimen, dataset)
    environment, owner = str(uuid4()), str(uuid4())
    context = {**descriptor, "suite_id": str(uuid4()), "repeat_index": 1, "candidate_commit": "a" * 40}
    data = {"schema_version": 1, "environment_id": environment, "owner_id": owner, "dataset_digest": descriptor["catalog_digest"],
            "case_definition_digest": descriptor["case_definition_digest"], "suite_context": context,
            "expected_attempts": 1, "attempts": [{"case_id": "AET-A02", "repeat_index": 1,
                "environment_id": environment, "owner_id": owner, "case_snapshot": specimen.model_dump(mode="json"),
                "started": True, "platform_root": {"id": str(uuid4()), "mode": "ask"},
                "task_outcome": "PASS", "cleanup_status": "CLEAN"}]}
    journal = Journal(directory)
    journal.data = data
    journal.save()
    journal.close()
    atomic_json(directory / "report.json", {"unchanged": "prior evidence"})
    return data


@pytest.mark.parametrize("mutation", ["requested_case", "suite", "catalog", "rules", "environment", "owner", "snapshot"])
def test_offline_report_refuses_mismatched_case_suite_or_evidence_identity(tmp_path, monkeypatch, mutation):
    data = saved_report(tmp_path)
    arguments = ["fixed_run", "report", "--case-id", "AET-A02", "--output", str(tmp_path)]
    if mutation == "requested_case":
        arguments[3] = "AET-A01"
    elif mutation == "suite":
        other = copy.deepcopy(data["suite_context"])
        other["suite_id"] = str(uuid4())
        path = tmp_path / "different-suite.json"
        atomic_json(path, other)
        arguments += ["--suite-context", str(path)]
    elif mutation == "catalog":
        data["dataset_digest"] = "0" * 64
    elif mutation == "rules":
        data["suite_context"]["rules_id"] = "old-rules"
    elif mutation in {"environment", "owner"}:
        data["attempts"][0][mutation + "_id"] = str(uuid4())
    else:
        data["attempts"][0]["case_snapshot"]["input"] = "Changed input"
    atomic_json(tmp_path / "journal.json", data)
    original = (tmp_path / "report.json").read_bytes()
    monkeypatch.setattr(sys, "argv", arguments)

    def forbidden(*args):
        pytest.fail("Offline report must not authenticate or perform business HTTP")

    monkeypatch.setattr(fixed_run, "Platform", forbidden)
    assert fixed_run.main() == 2
    assert (tmp_path / "report.json").read_bytes() == original


def test_matching_offline_report_remains_network_free(tmp_path, monkeypatch):
    saved_report(tmp_path)
    monkeypatch.setattr(sys, "argv", ["fixed_run", "report", "--case-id", "AET-A02", "--output", str(tmp_path)])
    monkeypatch.setattr(fixed_run, "Platform", lambda *_: pytest.fail("Offline export cannot authenticate"))
    assert fixed_run.main() == 0
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["gate"] == "PASS"
