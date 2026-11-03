import copy
import json
import sys
from uuid import uuid4

import pytest

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run, network
from evaluation_runner.contracts import Result
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
    data["target_digest"] = "b" * 64
    attempt = data["attempts"][0]
    attempt["attempt_id"] = str(uuid4())
    root = attempt["platform_root"]["id"]
    prior = str(uuid4())
    attempt["prior_run_refs"] = {"turn-0": {"id": prior, "mode": "ask", "path": "/api/v1/runs/" + prior}}
    attempt["platform_root"]["path"] = "/api/v1/runs/" + root
    messages = []
    inputs = specimen.fixture_parameters["fixed_inputs"]["messages"]
    for index, (identifier, text) in enumerate(zip([prior, root], inputs, strict=True)):
        messages += [fixed_evidence.message_projection({"role": "user", "run_id": identifier, "content": {"text": text}}),
                     fixed_evidence.message_projection({"role": "assistant", "run_id": identifier,
                        "content": {"data": {"content": json.dumps({"project": "星桥", "people": index + 2})}}})]
    snapshot = {"schema_version": 1, "kind": "a02-facts", "binding": fixed_evidence.binding(data, attempt),
        "scope": "formal HTTP facts", "runs": [{"id": identifier, "source": "/api/v1/runs/" + identifier,
        "status": "SUCCEEDED"} for identifier in [prior, root]], "final_run_id": root,
        "messages_complete": True, "messages": messages,
        "observer": {"mode": "ask", "run_id": root, "status": "SUCCEEDED", "controlled_model": {
            "recorded": True, "complete": True, "case_id": "AET-A02", "provider_calls": 1, "tokens": 15},
            "context_assertions": [{"mode": "ask", "run_id": root, "case_id": "AET-A02", "role": "lead",
                "provider_calls": 1, "human_turns": 2, "has_project_context": True, "has_update_to_three": True,
                "human_context_digest": fixed_evidence.text_digest(json.dumps(inputs, ensure_ascii=False))}]},
        "usage": {"calls": 2, "reported_calls": 2, "unknown_calls": 0, "pending_calls": 0,
                  "input_tokens": 20, "output_tokens": 10}}
    attempt["evidence_refs"] = [fixed_evidence.store(directory, "a02-facts", snapshot)]
    facts, _ = fixed_evidence.evaluate(snapshot, specimen, attempt)
    attempt["fact_results"] = [row.model_dump() for row in facts]
    attempt["metric_results"] = [Result(registered_id="tool_correctness", status="PASS", score=1.0).model_dump()]
    attempt["usage"] = snapshot["usage"]
    session = network.Session("run", {**context, "environment_id": environment, "owner_id": owner,
        "target_digest": data["target_digest"]}, "http://127.0.0.1:8199")
    for name in ("execution", "import", "metric", "export"):
        session.data["phases"].append({"name": name, "status": "COMPLETE", "started_at": network.now(),
                                     "ended_at": network.now(), "event_count": 0})
    attempt["run_audit_session_id"] = session.data["session_id"]
    fixed_audit.begin(directory, session)
    reference = fixed_audit.seal(directory, session, "COMPLETE")
    data["network_audit_refs"] = [reference]
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
