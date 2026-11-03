import copy
from uuid import uuid4

import pytest
from test_fixed_approval_evidence import prepared, seal_consumer

from evaluation_runner import fixed_consumer_evidence as consumer
from evaluation_runner.fixed_evidence import load, store


@pytest.mark.parametrize("mutation", ["root", "owner", "environment", "namespace", "checkpoint", "same_worker", "unknown_intent", "wrong_intent"])
def test_rebuild_needs_same_checkpoint_same_scope_and_a_new_confirmed_consumer(tmp_path, mutation):
    journal, attempt, _, before, _ = prepared(tmp_path)
    try:
        seal_consumer(journal, attempt, before)
        state = consumer.load(tmp_path, attempt["approval_consumer_ref"], consumer.KIND)
        proof = state["body"]
        field = {"root": "source_run_id", "owner": "owner_id", "environment": "environment_id",
                 "namespace": "namespace", "checkpoint": "checkpoint_id_after"}.get(mutation)
        if field:
            proof[field] = str(uuid4())
        elif mutation == "same_worker":
            proof["after_worker_ids"] = copy.deepcopy(proof["before_worker_ids"])
        elif mutation == "unknown_intent":
            attempt["submission_intents"][consumer.INTENT]["status"] = "UNKNOWN"
        else:
            attempt["submission_intents"][consumer.INTENT]["payload_digest"] = "b" * 64
        attempt["approval_consumer_ref"] = store(tmp_path, consumer.KIND, state)
        with pytest.raises(ValueError):
            consumer.verified(tmp_path, journal.data, attempt, consumer_work(before))
    finally:
        journal.close()


def consumer_work(before):
    from evaluation_runner.fixed_approval_observation import parse
    return parse(before).work


def test_confirmed_rebuild_is_retained_before_approval_and_not_repeated(tmp_path):
    journal, attempt, _, before, _ = prepared(tmp_path)
    work = consumer_work(before)
    seal_consumer(journal, attempt, before)
    original = load(tmp_path, attempt["approval_consumer_ref"], consumer.KIND)["body"]
    attempt.pop("approval_consumer_ref")
    attempt["evidence_refs"].clear()
    attempt["submission_intents"].clear()
    posts = []
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return original

    class Platform:
        def post(self, path, **kwargs):
            posts.append((path, kwargs))
            return Response()

        def get(self, path):
            assert path == consumer.PATH + "/" + str(work.run_id)
            return original
    try:
        assert consumer.capture(Platform(), journal, attempt, work) == original
        assert consumer.capture(Platform(), journal, attempt, work) == original
        assert len(posts) == 1
        journal.close()
        journal = type(journal)(tmp_path)
        assert consumer.verified(tmp_path, journal.data, journal.data["attempts"][0], work) == original
    finally:
        journal.close()


def test_lost_rebuild_response_cannot_trigger_a_second_rebuild(tmp_path):
    import httpx

    from evaluation_runner.journal import UnknownSubmission
    journal, attempt, _, before, _ = prepared(tmp_path)
    posts = []
    class Platform:
        def post(self, path, **_kwargs):
            posts.append(path)
            raise httpx.ReadError("lost after remote effect")
    try:
        for _ in range(2):
            with pytest.raises(UnknownSubmission):
                consumer.capture(Platform(), journal, attempt, consumer_work(before))
        assert len(posts) == 1 and "approval_consumer_ref" not in attempt
    finally:
        journal.close()


def test_failed_consumer_fact_save_poison_cannot_authorize_a_decision(tmp_path, monkeypatch):
    journal, attempt, _, before, _ = prepared(tmp_path)
    work = consumer_work(before)
    seal_consumer(journal, attempt, before)
    proof = load(tmp_path, attempt["approval_consumer_ref"], consumer.KIND)["body"]
    attempt.pop("approval_consumer_ref")
    attempt["evidence_refs"].clear()
    attempt["submission_intents"].clear()
    original_save, calls = journal.save, []

    def failing_save():
        calls.append(True)
        if len(calls) == 3:
            raise OSError("consumer fact not saved")
        original_save()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return proof

    class Platform:
        def post(self, _path, **_kwargs):
            return Response()

        def get(self, _path):
            return proof

    try:
        monkeypatch.setattr(journal, "save", failing_save)
        with pytest.raises(OSError):
            consumer.capture(Platform(), journal, attempt, work)
        assert attempt["approval_evidence_failed"]
        with pytest.raises(ValueError, match="not confirmed retained"):
            consumer.verified(tmp_path, journal.data, attempt, work)
    finally:
        journal.close()


@pytest.mark.parametrize("entry", ["consumer", "decision", "execute"])
def test_poisoned_evidence_stops_every_new_effect_before_mutation(tmp_path, entry):
    from evaluation_runner import fixed_approval_evidence as approval
    journal, attempt, specimen, before, _ = prepared(tmp_path)
    attempt["approval_evidence_failed"] = True
    class Platform:
        def post(self, *_args, **_kwargs):
            pytest.fail("Poisoned evidence must receive zero POST")

        def get(self, *_args, **_kwargs):
            pytest.fail("Poisoned evidence stops at entry")

        def attest(self):
            pytest.fail("Poisoned evidence stops at entry")

    try:
        with pytest.raises(ValueError, match="persistence failed"):
            if entry == "consumer":
                consumer.capture(Platform(), journal, attempt, consumer_work(before))
            elif entry == "decision":
                approval.decide(Platform(), journal, attempt, specimen)
            else:
                approval.execute(Platform(), journal, attempt, specimen)
        assert "submission_intents" not in attempt
    finally:
        journal.close()
