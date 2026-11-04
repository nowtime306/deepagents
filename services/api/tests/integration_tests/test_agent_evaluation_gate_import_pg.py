"""Real PG import contracts using explicitly synthetic packets; no target/publication/consumer."""

import hashlib
import json

import pytest
from test_agent_evaluation_worker_pg import actor, claim, no_business_effects, packet, seed, state

pytest_plugins = ["test_agent_evaluation_platform"]


def report_gate(value, gate):
    value["gate_verdict"] = gate
    data = json.loads(value["report"]["files"]["json"])
    data["gate_verdict"] = gate
    data["samples"] = value["samples"]
    raw = json.dumps(data)
    value["report"]["files"]["json"] = raw
    value["report"]["manifest"]["files_sha256"]["json"] = hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize("fact,gate", [("PASS", "NEEDS_REVIEW"), ("FAIL", "FAIL")])
def test_import_preserves_factual_outcome_and_stricter_gate(h, fact, gate):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    value = packet(row)
    value["samples"][0].update(task_outcome=fact, gate_verdict=gate)
    report_gate(value, gate)
    h.http.portal.call(worker.commit, row, value)
    saved = state(h, row)
    assert saved["run"][0]["gate_verdict"] == gate
    assert saved["samples"][0]["task_outcome"] == fact and saved["samples"][0]["gate_verdict"] == gate
    assert json.loads(saved["reports"][0]["files"]["json"])["gate_verdict"] == gate
    no_business_effects(h)


@pytest.mark.parametrize(
    "fault",
    [
        "green_top",
        "green_report",
        "invalid_sample",
        "invalid_top",
        "missing_top",
        "empty_pass",
        "report_sample_mismatch",
    ],
)
def test_inconsistent_or_invalid_input_never_imports(h, fault):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    value = packet(row)
    value["samples"][0]["gate_verdict"] = "NEEDS_REVIEW"
    report_gate(value, "NEEDS_REVIEW")
    if fault == "green_top":
        value["gate_verdict"] = "PASS"
    elif fault == "green_report":
        report_gate(value, "PASS")
        value["gate_verdict"] = "NEEDS_REVIEW"
    elif fault == "invalid_sample":
        value["samples"][0]["gate_verdict"] = "PENDING"
    elif fault == "invalid_top":
        value["gate_verdict"] = "PENDING"
    elif fault == "missing_top":
        del value["gate_verdict"]
    elif fault == "empty_pass":
        value["samples"][0]["facts"] = []
    else:
        data = json.loads(value["report"]["files"]["json"])
        data["samples"] = []
        raw = json.dumps(data)
        value["report"]["files"]["json"] = raw
        value["report"]["manifest"]["files_sha256"]["json"] = hashlib.sha256(raw.encode()).hexdigest()
    before = state(h, row)
    with pytest.raises(ValueError):
        h.http.portal.call(worker.commit, row, value)
    assert state(h, row) == before
    no_business_effects(h)
