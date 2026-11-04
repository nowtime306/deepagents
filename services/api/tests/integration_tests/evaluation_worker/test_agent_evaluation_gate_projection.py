"""Synthetic local collector files/ready substitute, real projection and report rendering; no runtime or DB."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from evaluation_runner import platform_driver as driver


def material():
    child = {
        "case_id": "AET-A02",
        "repeat_ordinal": 1,
        "status": "PASS",
        "child": "a02",
        "cleanup": "DATABASE_REMOVED_EVIDENCE_RETAINED",
        "root_run_ids": [str(uuid4())],
    }
    row = {
        "gate": "PASS",
        "attempts": [
            {
                "case_id": "AET-A02",
                "repeat_index": 1,
                "task_outcome": "PASS",
                "fact_results": [{"registered_id": "output_equals", "status": "PASS"}],
                "metric_results": [],
            }
        ],
    }
    ready = {"gate": "PASS", "session_id": str(uuid4())}
    return child, row, ready


@pytest.mark.parametrize("fault", ["infrastructure", "child_review", "report_review", "ready_review", "cleanup"])
def test_original_pass_fact_stays_pass_but_gate_requires_review(fault):
    child, row, ready = material()
    if fault == "infrastructure":
        child["infrastructure_error"] = {"exit_code": 3, "reason": "EXIT_REPORT_MISMATCH"}
    elif fault == "child_review":
        child["status"] = "NEEDS_REVIEW"
    elif fault == "report_review":
        row["gate"] = "NEEDS_REVIEW"
    elif fault == "ready_review":
        ready["gate"] = "NEEDS_REVIEW"
    else:
        child["cleanup"] = "RETAINED"
    original = deepcopy(row)
    sample = driver.sample_projection(child, row, ready)
    assert sample["task_outcome"] == "PASS" and sample["gate_verdict"] == "NEEDS_REVIEW"
    assert row == original


@pytest.mark.parametrize("source", ["task", "fact", "child", "report", "ready"])
def test_known_failure_cannot_be_overridden_by_infrastructure_review(source):
    child, row, ready = material()
    child["infrastructure_error"] = {"exit_code": 3}
    if source == "task":
        row["attempts"][0]["task_outcome"] = "FAIL"
    elif source == "fact":
        row["attempts"][0]["fact_results"][0]["status"] = "FAIL"
    elif source == "child":
        child["status"] = "FAIL"
    elif source == "report":
        row["gate"] = "FAIL"
    else:
        ready["gate"] = "FAIL"
    sample = driver.sample_projection(child, row, ready)
    assert sample["gate_verdict"] == "FAIL"
    assert sample["task_outcome"] == row["attempts"][0]["task_outcome"]


@pytest.mark.parametrize(
    "suite_gate,stopped,expected",
    [
        ("PASS", None, "PASS"),
        ("NEEDS_REVIEW", None, "NEEDS_REVIEW"),
        ("PASS", "COORDINATOR_ERROR_RuntimeError", "NEEDS_REVIEW"),
        ("FAIL", "SOURCE_CHANGED", "FAIL"),
    ],
)
def test_parent_gate_and_stop_survive_json_junit_markdown(tmp_path, monkeypatch, suite_gate, stopped, expected):
    child, report, ready = material()
    case_dir = tmp_path / "suite/a02"
    (case_dir / "result").mkdir(parents=True)
    (case_dir / "target").mkdir()
    commit, suite_id = "a" * 40, str(uuid4())
    context = {"candidate_commit": commit, "suite_id": suite_id, "case_id": "AET-A02", "repeat_index": 1}
    target = {"environment_id": str(uuid4()), "owner_id": str(uuid4())}
    registered = SimpleNamespace(model_dump=lambda **_: {"synthetic": "catalog"})
    suite = {
        "candidate_commit": commit,
        "dataset_digest": driver.digest(registered.model_dump()),
        "suite_id": suite_id,
        "attempts": [child],
        "gate": suite_gate,
    }
    if stopped:
        suite["stopped_reason"] = stopped
    for path, value in [
        (tmp_path / "suite/suite.json", suite),
        (case_dir / "suite-context.json", context),
        (case_dir / "target/target-manifest.json", target),
        (case_dir / "result/report.json", report),
    ]:
        path.write_text(json.dumps(value))
    ready.update(
        **target,
        suite_context=context,
        files={"report.json": hashlib.sha256((case_dir / "result/report.json").read_bytes()).hexdigest()},
    )
    monkeypatch.setattr(
        driver, "validate_ready", lambda _: ready
    )  # Local collector-contract fixture, not real audit proof.
    request = {
        "candidate_commit": commit,
        "definition": {"cases": [{"case_id": "AET-A02"}]},
        "repeats": 1,
        "run_id": str(uuid4()),
        "owner_id": str(uuid4()),
    }
    actual = driver.collect(request, tmp_path, registered)
    assert actual["gate_verdict"] == actual["samples"][0]["gate_verdict"] == expected
    assert actual["samples"][0]["task_outcome"] == "PASS"
    public = json.loads(actual["report"]["files"]["json"])
    assert public["gate_verdict"] == expected and public["samples"] == actual["samples"]
    xml = ElementTree.fromstring(actual["report"]["files"]["junit"])
    assert int(xml.get("errors")) == (expected == "NEEDS_REVIEW")
    assert int(xml.get("failures")) == (expected == "FAIL")
    assert expected in actual["report"]["files"]["markdown"]


def test_empty_original_pass_facts_rejected():
    child, row, ready = material()
    row["attempts"][0]["fact_results"] = []
    with pytest.raises(ValueError, match="empty fact"):
        driver.sample_projection(child, row, ready)


@pytest.mark.parametrize("source", ["child", "report", "ready"])
def test_invalid_source_gate_rejected(source):
    child, row, ready = material()
    {"child": child, "report": row, "ready": ready}[source]["status" if source == "child" else "gate"] = "PENDING"
    with pytest.raises(ValueError, match="Invalid original child gate"):
        driver.sample_projection(child, row, ready)
