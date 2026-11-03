import copy
import json
from uuid import uuid4

import pytest
from test_fixed_approval_observation import pair

from evaluation_runner import fixed_approval_evidence as retained
from evaluation_runner import fixed_consumer_evidence as consumer
from evaluation_runner.fixed_run import catalog
from evaluation_runner.journal import Journal, digest


def prepared(tmp_path, case_id="AET-W04"):
    before, after = pair(case_id)
    specimen = next(row for row in catalog().cases if row.case_id == case_id)
    observed = before["work"]
    journal = Journal(tmp_path)
    context = {"suite_id": str(uuid4()), "repeat_index": 1, "candidate_commit": "a" * 40,
               "rules_id": "controlled-a20-v1", "catalog_digest": digest(catalog().model_dump()),
               "case_definition_digest": digest(specimen.model_dump())}
    journal.data.update(environment_id=observed["environment_id"], owner_id=observed["owner_id"],
                        target_digest="d" * 64, suite_context=context)
    attempt = {"attempt_id": str(uuid4()), "case_id": case_id, "sandbox_namespace": observed["namespace"],
               "fixture": {"id": observed["task_id"], "revision": 2},
               "platform_root": {"mode": "work", "id": observed["run_id"], "fixture_id": observed["task_id"],
                                 "path": "/api/v1/work-runs/" + observed["run_id"]}}
    journal.data["attempts"].append(attempt)
    return journal, attempt, specimen, before, after


def seal(journal, attempt, specimen, before, after, replay=None):
    retained.retain(journal, attempt, specimen, "waiting", before, "approval_before_ref")
    if specimen.case_id == "AET-W04":
        seal_consumer(journal, attempt, before)
    retained.retain(journal, attempt, specimen, "final", after, "approval_final_ref")
    if specimen.case_id == "AET-W04":
        retained.retain(journal, attempt, specimen, "replay", replay if replay is not None else after, "approval_replay_ref")
    row = before["approvals"][0]
    intent = {"status": "CONFIRMED", "path": f"/api/v1/work-approvals/{row['id']}/decisions",
              "payload_digest": digest({"digest": row["reported_digest"], "decision": "reject" if specimen.case_id == "AET-W03" else "approve"})}
    attempt.setdefault("submission_intents", {})["decide-counter-approval"] = copy.deepcopy(intent)
    if specimen.case_id == "AET-W04":
        attempt["submission_intents"]["probe-repeat-confirmed-decision"] = copy.deepcopy(intent)
    journal.save()


def seal_consumer(journal, attempt, before):
    from evaluation_runner.fixed_evidence import binding, store
    work = retained.observation.parse(before).work
    checkpoint = str(uuid4())
    proof = {"schema_version": 1, "case_id": "AET-W04", "source_run_id": str(work.run_id),
             "owner_id": str(work.owner_id), "environment_id": str(work.environment_id), "namespace": str(work.namespace),
             "status": "REBUILT", "before_worker_ids": [str(uuid4())], "after_worker_ids": [str(uuid4())],
             "checkpoint_id_before": checkpoint, "checkpoint_id_after": checkpoint}
    ref = store(journal.directory, consumer.KIND, {"schema_version": 1, "kind": consumer.KIND,
                "binding": binding(journal.data, attempt), "body": proof})
    attempt.setdefault("evidence_refs", []).append(ref)
    attempt["approval_consumer_ref"] = ref
    attempt.setdefault("submission_intents", {})[consumer.INTENT] = {
        "status": "CONFIRMED", "path": consumer.PATH + "/rebuild",
        "payload_digest": digest({"source_run_id": str(work.run_id)})}


@pytest.mark.parametrize("case_id", ["AET-W03", "AET-W04"])
def test_sealed_approval_facts_recompute_and_are_disposable_only_when_settled(tmp_path, case_id):
    journal, attempt, specimen, before, after = prepared(tmp_path, case_id)
    try:
        seal(journal, attempt, specimen, before, after)
        snapshot = retained.verified(tmp_path, journal.data, attempt, specimen)
        facts, adapter = retained.evaluate(snapshot, specimen, attempt)
        assert [row.status for row in facts] == ["PASS"] * (3 if case_id == "AET-W04" else 2)
        assert adapter["usage"]["total"] == after["work"]["usage"]
        assert retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
        journal.close()
        journal = Journal(tmp_path)
        assert retained.verified(tmp_path, journal.data, journal.data["attempts"][0], specimen) == snapshot
    finally:
        journal.close()


@pytest.mark.parametrize("field", ["namespace", "owner_id", "environment_id", "run_id", "task_id"])
def test_other_scope_cannot_supply_final_approval_facts(tmp_path, field):
    journal, attempt, specimen, before, after = prepared(tmp_path)
    try:
        after["work"][field] = str(uuid4())
        seal(journal, attempt, specimen, before, after)
        with pytest.raises(ValueError, match="another run"):
            retained.verified(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


def test_saved_usage_and_reference_order_are_not_implicit_evidence(tmp_path):
    journal, attempt, specimen, before, after = prepared(tmp_path)
    try:
        seal(journal, attempt, specimen, before, after)
        attempt["evidence_refs"].reverse()
        with pytest.raises(ValueError, match="reference set/order"):
            retained.verified(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


@pytest.mark.parametrize("unsettled", ["provider", "counter", "operation"])
def test_blocked_counter_or_unsettled_resource_cannot_be_clean(tmp_path, unsettled):
    journal, attempt, specimen, before, after = prepared(tmp_path)
    try:
        if unsettled == "provider":
            after["work"]["provider"]["complete"] = False
        elif unsettled == "counter":
            after["counter"] = {"downloaded": False, "sha256": None, "size": None, "declared": None}
        else:
            after["receipts"][0]["status"] = after["work"]["operations"][0]["status"] = "UNKNOWN"
        seal(journal, attempt, specimen, before, after)
        assert not retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


def test_persistence_failure_poison_is_not_cleared_by_in_memory_reference(tmp_path, monkeypatch):
    journal, attempt, specimen, before, _ = prepared(tmp_path)
    try:
        monkeypatch.setattr(journal, "save", lambda: (_ for _ in ()).throw(OSError("disk unavailable")))
        with pytest.raises(OSError):
            retained.retain(journal, attempt, specimen, "waiting", before, "approval_before_ref")
        assert attempt["approval_evidence_failed"]
        with pytest.raises(ValueError, match="not confirmed retained"):
            retained.verified(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()


@pytest.mark.parametrize("stage", ["final", "replay"])
def test_stage_alias_and_fact_ref_are_persisted_together_or_poison_cleanup(tmp_path, monkeypatch, stage):
    journal, attempt, specimen, before, after = prepared(tmp_path)
    try:
        retained.retain(journal, attempt, specimen, "waiting", before, "approval_before_ref")
        if stage == "replay":
            retained.retain(journal, attempt, specimen, "final", after, "approval_final_ref")
        stored = copy.deepcopy(journal.data)
        monkeypatch.setattr(journal, "save", lambda: (_ for _ in ()).throw(OSError("alias write unavailable")))
        with pytest.raises(OSError):
            retained.retain(journal, attempt, specimen, stage, after, "approval_" + stage + "_ref")
        assert attempt["approval_evidence_failed"]
        with pytest.raises(ValueError, match="not confirmed retained"):
            retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
        assert json.loads(journal.path.read_text(encoding="utf-8")) == stored
        journal.close()
        journal = Journal(tmp_path)
        assert "approval_" + stage + "_ref" not in journal.data["attempts"][0]
    finally:
        journal.close()


@pytest.mark.parametrize("mutation", ["command", "input_unknown", "output_unknown", "calls", "provider_calls", "tokens", "call_limit", "token_limit"])
def test_decide_checks_fixed_command_checkpoint_and_known_budget_before_any_post(tmp_path, monkeypatch, mutation):
    journal, attempt, specimen, before, _ = prepared(tmp_path)
    class Platform:
        def post(self, *_args, **_kwargs):
            pytest.fail("Unsafe approval must never be submitted")
    try:
        if mutation == "command":
            before["approvals"][0]["args_digest"] = "b" * 64
        elif mutation in {"input_unknown", "output_unknown"}:
            before["work"]["usage"][mutation.split("_")[0] + "_tokens"] = None
        elif mutation == "calls":
            before["work"]["usage"]["reported_calls"] = 0
        elif mutation == "provider_calls":
            before["work"]["provider"]["provider_calls"] = 0
        elif mutation == "tokens":
            before["work"]["provider"]["tokens"] = 0
        elif mutation == "call_limit":
            before["work"]["usage"]["calls"] = before["work"]["usage"]["reported_calls"] = specimen.budgets.max_model_calls
            before["work"]["provider"]["provider_calls"] = specimen.budgets.max_model_calls
        else:
            before["work"]["usage"].update(input_tokens=specimen.budgets.max_tokens, output_tokens=0)
            before["work"]["provider"]["tokens"] = specimen.budgets.max_tokens
        monkeypatch.setattr(retained.observation, "collect", lambda *_args: copy.deepcopy(before))
        with pytest.raises(retained.adapters.Blocked):
            retained.decide(Platform(), journal, attempt, specimen)
        assert "submission_intents" not in attempt
    finally:
        journal.close()


@pytest.mark.parametrize("status", [None, "UNKNOWN", "SUBMITTING", "REJECTED"])
def test_completed_remote_run_does_not_turn_unknown_approval_into_a_repeat_probe(tmp_path, monkeypatch, status):
    journal, attempt, specimen, before, after = prepared(tmp_path)
    class Platform:
        def attest(self):
            return {}

        def get(self, _path):
            return {"status": "COMPLETED"}

        def post(self, *_args, **_kwargs):
            pytest.fail("Unknown original decision must receive no new POST")
    try:
        attempt["submission_complete"] = True
        retained.retain(journal, attempt, specimen, "waiting", before, "approval_before_ref")
        if status is not None:
            attempt["submission_intents"] = {"decide-counter-approval": {"status": status}}
        monkeypatch.setattr(retained.observation, "collect", lambda *_args: copy.deepcopy(after))
        with pytest.raises(retained.adapters.Blocked, match="unknown original"):
            retained.execute(Platform(), journal, attempt, specimen)
        assert "approval_replay_ref" not in attempt
        assert "probe-repeat-confirmed-decision" not in attempt.get("submission_intents", {})
    finally:
        journal.close()


def test_confirmed_original_decision_allows_one_explicit_probe_and_sealed_replay(tmp_path, monkeypatch):
    journal, attempt, specimen, before, after = prepared(tmp_path)
    posts = []
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"decision": "approve"}

    class Platform:
        def attest(self):
            return {}

        def get(self, _path):
            return {"status": "COMPLETED"}

        def post(self, path, **kwargs):
            posts.append((path, kwargs))
            return Response()
    try:
        seal(journal, attempt, specimen, before, after)
        attempt["submission_complete"] = True
        attempt.pop("approval_replay_ref")
        attempt["evidence_refs"].pop()
        attempt["submission_intents"].pop("probe-repeat-confirmed-decision")
        monkeypatch.setattr(retained.observation, "collect", lambda *_args: copy.deepcopy(after))
        result = retained.execute(Platform(), journal, attempt, specimen)
        assert len(posts) == 1 and result["after"] == result["replay"]
        assert retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
        retained.execute(Platform(), journal, attempt, specimen)
        assert len(posts) == 1
        attempt["submission_intents"]["decide-counter-approval"]["status"] = "UNKNOWN"
        with pytest.raises(ValueError, match="not confirmed"):
            retained.cleanup_ready(tmp_path, journal.data, attempt, specimen)
    finally:
        journal.close()
