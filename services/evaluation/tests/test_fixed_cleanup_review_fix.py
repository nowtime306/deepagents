import copy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_core import attestation, target
from test_fixed_work_evidence import prepared
from test_fixed_workflow_profiles import FormalWorkflowFixture, command

from evaluation_runner import fixed_audit, fixed_evidence, fixed_run, fixed_work_evidence, network
from evaluation_runner import fixed_workflow_evidence as workflow
from evaluation_runner.client import Platform
from evaluation_runner.journal import Journal, UnknownSubmission, atomic_json


def work_offline_case(tmp_path, monkeypatch):
    journal, attempt, specimen, _, adapter = prepared(tmp_path, monkeypatch)
    snapshot = fixed_work_evidence.capture(None, journal, attempt, specimen, adapter)
    data, context = journal.data, journal.data["suite_context"]
    data.update(dataset_digest=context["catalog_digest"], case_definition_digest=context["case_definition_digest"])
    attempt.update(case_snapshot=specimen.model_dump(mode="json"), repeat_index=1, owner_id=data["owner_id"],
                   environment_id=data["environment_id"], started=True, cleanup_status="CLEAN", task_outcome="FAIL", metric_results=[])
    session = network.Session("run", {**context, "owner_id": data["owner_id"], "environment_id": data["environment_id"],
                                      "target_digest": data["target_digest"]}, None)
    monkeypatch.setattr(network, "ACTIVE", session)
    fixed_audit.begin(tmp_path, session)
    with network.phase("execution"), network.phase("export"):
        pass
    fixed_audit.seal(tmp_path, session, "COMPLETE")
    attempt["run_audit_session_id"] = session.data["session_id"]
    return journal, attempt, specimen, snapshot


@pytest.mark.parametrize("mutation", ["artifacts", "resources", "queue", "provider"])
def test_stale_clean_is_rejected_offline_even_when_work_fail_facts_are_self_consistent(tmp_path, monkeypatch, mutation):
    journal, attempt, specimen, snapshot = work_offline_case(tmp_path, monkeypatch)
    try:
        observed = snapshot["observation"]
        if mutation == "artifacts":
            observed["artifacts_complete"] = False
        elif mutation == "resources":
            observed["sandbox_states"] = {"READY": 1}
        elif mutation == "queue":
            observed["events_complete"] = False
        else:
            observed["provider"]["complete"] = False
        attempt["evidence_refs"] = [fixed_evidence.store(tmp_path, fixed_work_evidence.KIND, snapshot)]
        facts, _ = fixed_work_evidence.evaluate(snapshot, specimen, attempt)
        attempt.update(fact_results=[row.model_dump() for row in facts], usage=snapshot["usage"])
        assert any(row.status == "BLOCKED" for row in facts)
        with pytest.raises(ValueError, match="Stored CLEAN"):
            fixed_run.validate_offline_report(journal.data, specimen, fixed_run.catalog(), directory=tmp_path)
        attempt["cleanup_status"] = "NEEDS_REVIEW"
        fixed_run.validate_offline_report(journal.data, specimen, fixed_run.catalog(), directory=tmp_path)
    finally:
        journal.close()


def test_known_missing_file_work_failure_keeps_valid_clean_offline(tmp_path, monkeypatch):
    journal, attempt, specimen, snapshot = work_offline_case(tmp_path, monkeypatch)
    try:
        facts, _ = fixed_work_evidence.evaluate(snapshot, specimen, attempt)
        attempt.update(fact_results=[row.model_dump() for row in facts], usage=snapshot["usage"])
        fixed_run.validate_offline_report(journal.data, specimen, fixed_run.catalog(), directory=tmp_path)
        assert attempt["task_outcome"] == "FAIL" and attempt["cleanup_status"] == "CLEAN"
    finally:
        journal.close()


@pytest.mark.parametrize("drift_at", [1, 2, 3])
def test_work_namespace_drift_blocks_even_the_last_transport_attestation_before_post(tmp_path, drift_at):
    value = target()
    initial, changed = str(uuid4()), str(uuid4())
    calls = {"attest": 0, "post": 0}
    proof = attestation(value)

    def receiver(request):
        if request.method == "POST":
            calls["post"] += 1
            pytest.fail("A changed namespace cannot receive a Work submission")
        calls["attest"] += 1
        return httpx.Response(200, json={**proof, "sandbox_namespace": changed if calls["attest"] >= drift_at else initial})

    platform = Platform(value, transport=httpx.MockTransport(receiver))
    journal = Journal(tmp_path)
    specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-W01")
    attempt = {"attempt_id": str(uuid4()), "fixture": {"id": str(uuid4()), "model_id": "controlled"}, "sandbox_namespace": initial}
    try:
        with pytest.raises((ValueError, UnknownSubmission)):
            fixed_run.execute(platform, journal, attempt, specimen, tmp_path)
        assert calls == {"attest": drift_at, "post": 0}
    finally:
        platform.close()
        journal.close()


@pytest.mark.parametrize("case_id", ["AET-F02", "AET-F04"])
def test_readonly_namespace_drift_after_initialization_also_prevents_new_posts(tmp_path, monkeypatch, case_id):
    fixture = FormalWorkflowFixture(case_id)
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    posts = len(fixture.posts)
    fixture.proof["sandbox_namespace"] = str(uuid4())
    assert command(tmp_path, monkeypatch, fixture, "run") != 0
    assert len(fixture.posts) == posts


@pytest.mark.parametrize("mutation", ["active_workflow_runs", "active_ask_runs", "active_work_runs", "active_sandboxes",
                                     "unknown_operations", "unknown_model_usage", "provider_pending", "provider_unknown",
                                     "unaccounted_sandbox"])
def test_stable_unsettled_workflow_snapshot_is_not_clean_online_or_offline(tmp_path, monkeypatch, mutation):
    fixture = FormalWorkflowFixture("AET-F02")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    output = tmp_path / "output"
    journal = Journal(output)
    try:
        data, attempt = journal.data, journal.data["attempts"][0]
        reference = attempt["evidence_refs"][-1]
        snapshot = fixed_evidence.load(output, reference, "workflow-facts")
        after = snapshot["body"]["after"]
        if mutation.startswith("provider_"):
            after["provider"]["pending" if mutation == "provider_pending" else "unknown_roots"] = 1
        elif mutation == "unaccounted_sandbox":
            after["sandbox"]["attempts"] = 1
        else:
            after["counts"][mutation] = 1
        atomic_json(output / reference["path"], snapshot)
        reference["sha256"] = fixed_evidence.file_digest(output / reference["path"])
        specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-F02")
        facts, _ = workflow.evaluate(snapshot, specimen, attempt)
        attempt.update(task_outcome="FAIL", fact_results=[row.model_dump() for row in facts], cleanup_status="CLEAN")
        assert not workflow.cleanup_ready(output, data, attempt, specimen)
        monkeypatch.setattr(workflow.observation, "effects", lambda *_: copy.deepcopy(after))
        assert workflow.cleanup(SimpleNamespace(), journal, attempt, specimen) == "NEEDS_REVIEW"
        with pytest.raises(ValueError, match="Stored CLEAN"):
            fixed_run.validate_offline_report(data, specimen, fixed_run.catalog(), directory=output)
        attempt["cleanup_status"] = "NEEDS_REVIEW"
        fixed_run.validate_offline_report(data, specimen, fixed_run.catalog(), directory=output)
    finally:
        journal.close()


def test_known_completed_extra_effects_are_still_disposable_as_a_business_failure(tmp_path, monkeypatch):
    fixture = FormalWorkflowFixture("AET-F02")
    assert command(tmp_path, monkeypatch, fixture, "init") == 0
    assert command(tmp_path, monkeypatch, fixture, "run") == 0
    output = tmp_path / "output"
    journal = Journal(output)
    try:
        attempt = journal.data["attempts"][0]
        reference = attempt["evidence_refs"][-1]
        snapshot = fixed_evidence.load(output, reference, "workflow-facts")
        after = snapshot["body"]["after"]
        after["counts"].update(work_tasks=1, work_runs=1, operations=1, sandbox_sessions=1)
        after["sandbox"]["attempts"] = 1  # Accounted session, no active/unknown work or sandbox remains.
        atomic_json(output / reference["path"], snapshot)
        reference["sha256"] = fixed_evidence.file_digest(output / reference["path"])
        specimen = next(row for row in fixed_run.catalog().cases if row.case_id == "AET-F02")
        facts, _ = workflow.evaluate(snapshot, specimen, attempt)
        attempt.update(task_outcome="FAIL", fact_results=[row.model_dump() for row in facts], cleanup_status="CLEAN")
        assert workflow.cleanup_ready(output, journal.data, attempt, specimen)
        monkeypatch.setattr(workflow.observation, "effects", lambda *_: copy.deepcopy(after))
        assert workflow.cleanup(SimpleNamespace(), journal, attempt, specimen) == "CLEAN"
        fixed_run.validate_offline_report(journal.data, specimen, fixed_run.catalog(), directory=output)
    finally:
        journal.close()
