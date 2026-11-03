"""Work facts are sealed and rechecked before a known negative control can be disposed."""

import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from evaluation_runner import fixed_work_evidence as retained
from evaluation_runner.contracts import Dataset, outcome
from evaluation_runner.fixed_evidence import load, store
from evaluation_runner.journal import Journal, digest
from test_fixed_work_observation import observation


def prepared(tmp_path, monkeypatch):
    observed, _ = observation()
    dataset = Dataset.model_validate_json((Path(__file__).resolve().parents[1] / "datasets/a20-fixed.json").read_text(encoding="utf-8"))
    specimen = next(row for row in dataset.cases if row.case_id == "AET-W01")
    context = {"schema_version": 1, "suite_id": str(uuid4()), "case_id": specimen.case_id, "repeat_index": 1,
               "candidate_commit": "a" * 40, "rules_id": "controlled-a20-v1",
               "catalog_digest": digest(dataset.model_dump()), "case_definition_digest": digest(specimen.model_dump())}
    journal = Journal(tmp_path)
    journal.data.update(environment_id=observed["environment_id"], owner_id=observed["owner_id"], target_digest="c" * 64,
                        suite_context=context)
    attempt = {"attempt_id": str(uuid4()), "case_id": specimen.case_id, "fixture": {"id": observed["task_id"]},
               "sandbox_namespace": observed["namespace"],
               "platform_root": {"mode": "work", "id": observed["run_id"], "fixture_id": observed["task_id"],
                                 "path": "/api/v1/work-runs/" + observed["run_id"]}}
    journal.data["attempts"].append(attempt)
    monkeypatch.setattr(retained.work, "collect", lambda *args: copy.deepcopy(observed))
    adapter = {"run": {"id": observed["run_id"], "status": observed["status"]}, "root": attempt["platform_root"]}
    return journal, attempt, specimen, observed, adapter


def test_capture_recomputes_from_retained_file_and_a_missing_file_stays_fail(tmp_path, monkeypatch):
    journal, attempt, specimen, observed, adapter = prepared(tmp_path, monkeypatch)
    try:
        snapshot = retained.capture(None, journal, attempt, specimen, adapter)
        facts, converted = retained.evaluate(snapshot, specimen, attempt)
        assert outcome(facts, [], "CLEAN") == "FAIL"
        assert converted["run"]["status"] == "COMPLETED" and converted["usage"]["total"] == observed["usage"]
        assert retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
        disk = json.loads((tmp_path / "journal.json").read_text(encoding="utf-8"))
        reference = disk["attempts"][0]["evidence_refs"][0]
        assert load(tmp_path, reference, retained.KIND) == snapshot
    finally:
        journal.close()


@pytest.mark.parametrize("failure", ["save", "journal"])
def test_evidence_persistence_failure_never_returns_a_grade(tmp_path, monkeypatch, failure):
    journal, attempt, specimen, _, adapter = prepared(tmp_path, monkeypatch)
    def refused(*args):
        raise OSError("Synthetic evidence persistence failure")
    monkeypatch.setattr(retained if failure == "save" else journal, "store" if failure == "save" else "save", refused)
    try:
        with pytest.raises(OSError):
            retained.capture(None, journal, attempt, specimen, adapter)
        with pytest.raises(ValueError):
            retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


@pytest.mark.parametrize("mutation", ["owner", "environment", "namespace", "run", "task", "binding", "usage", "boolean_usage", "boolean_repeat"])
def test_internally_rehashed_wrong_scope_or_usage_cannot_be_cleaned(tmp_path, monkeypatch, mutation):
    journal, attempt, specimen, _, adapter = prepared(tmp_path, monkeypatch)
    try:
        snapshot = retained.capture(None, journal, attempt, specimen, adapter)
        if mutation in {"owner", "environment", "run", "task"}:
            snapshot["observation"][mutation + "_id"] = str(uuid4())
        elif mutation == "namespace":snapshot["observation"]["namespace"] = str(uuid4())
        elif mutation == "binding":snapshot["binding"]["suite_id"] = str(uuid4())
        elif mutation == "usage":snapshot["usage"]["calls"] = 2
        elif mutation == "boolean_repeat":snapshot["binding"]["repeat_index"] = True
        else:snapshot["usage"]["unknown_calls"] = False
        attempt["evidence_refs"] = [store(tmp_path, retained.KIND, snapshot)]
        with pytest.raises(ValueError):
            retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


@pytest.mark.parametrize("mutation", ["incomplete", "present"])
def test_only_a_proven_missing_artifact_can_authorize_negative_control_cleanup(tmp_path, monkeypatch, mutation):
    journal, attempt, specimen, _, adapter = prepared(tmp_path, monkeypatch)
    try:
        snapshot = retained.capture(None, journal, attempt, specimen, adapter)
        if mutation == "incomplete":snapshot["observation"]["artifacts_complete"] = False
        else:snapshot["observation"]["artifacts"][0]["path_sha256"] = hashlib.sha256(b"/report.txt").hexdigest()
        attempt["evidence_refs"] = [store(tmp_path, retained.KIND, snapshot)]
        facts, _ = retained.evaluate(snapshot, specimen, attempt)
        assert next(fact for fact in facts if fact.registered_id == "artifact_exists").status == ("BLOCKED" if mutation == "incomplete" else "PASS")
        assert not retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


def test_older_evidence_is_not_ignored_when_a_newer_valid_snapshot_exists(tmp_path, monkeypatch):
    journal, attempt, specimen, _, adapter = prepared(tmp_path, monkeypatch)
    try:
        retained.capture(None, journal, attempt, specimen, adapter)
        first = attempt["evidence_refs"][0]
        retained.capture(None, journal, attempt, specimen, adapter)
        assert len(attempt["evidence_refs"]) == 2
        path = tmp_path / first["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with pytest.raises(ValueError):
            retained.verified(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


@pytest.mark.parametrize("mutation", ["missing_ref", "missing_file", "wrong_adapter_root", "missing_namespace"])
def test_missing_or_foreign_observation_is_not_replaced_with_default_empty_data(tmp_path, monkeypatch, mutation):
    journal, attempt, specimen, _, adapter = prepared(tmp_path, monkeypatch)
    try:
        if mutation == "wrong_adapter_root":
            adapter["run"]["id"] = str(uuid4())
            with pytest.raises(ValueError):retained.capture(None, journal, attempt, specimen, adapter)
        elif mutation == "missing_namespace":
            del attempt["sandbox_namespace"]
            with pytest.raises(KeyError):retained.capture(None, journal, attempt, specimen, adapter)
        else:
            retained.capture(None, journal, attempt, specimen, adapter)
            if mutation == "missing_ref":attempt["evidence_refs"] = []
            else:(tmp_path / attempt["evidence_refs"][0]["path"]).unlink()
            with pytest.raises((ValueError, FileNotFoundError)):
                retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()
