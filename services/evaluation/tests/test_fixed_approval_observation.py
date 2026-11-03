import copy
import hashlib
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from evaluation_runner import adapters
from evaluation_runner import fixed_approval_observation as approval


def state(case_id="AET-W04", *, waiting=False):
    value = "0" if waiting or case_id == "AET-W03" else "1"
    checksum = hashlib.sha256(value.encode()).hexdigest()
    identifier, task, owner, environment, namespace, approval_id = [str(uuid4()) for _ in range(6)]
    operation_id = str(uuid4())
    decision = None if waiting else "reject" if case_id == "AET-W03" else "approve"
    operations = [] if waiting or case_id == "AET-W03" else [{"id": operation_id, "name": "execute", "status": "SUCCEEDED"}]
    calls = 1 if waiting else 2
    return {"work": {"case_id": case_id, "environment_id": environment, "owner_id": owner, "namespace": namespace,
                     "run_id": identifier, "task_id": task, "environment": "coding",
                     "status": "WAITING_APPROVAL" if waiting else "COMPLETED", "snapshot_id": None if waiting else str(uuid4()),
                     "artifacts_complete": True, "artifacts": [{"id": str(uuid4()), "path_sha256": approval.COUNTER_PATH,
                                                               "digest": checksum, "size": 1}],
                     "events_complete": True, "events": [{"event_id": i + 1, "event": event} for i, event in enumerate(
                         ["run.started"] if waiting else ["run.started", "run.started", "run.completed"])],
                     "operations_complete": True, "operations": operations,
                     "usage": {"calls": calls, "reported_calls": calls, "unknown_calls": 0, "pending_calls": 0,
                               "input_tokens": 10 * calls, "output_tokens": 5 * calls},
                     "provider": {"recorded": True, "complete": True, "case_id": case_id,
                                  "provider_calls": calls, "tokens": 15 * calls},
                     "sandbox_complete": True, "sandbox_states": {"RELEASED": calls}},
            "approvals": [{"id": approval_id, "payload_digest": "a" * 64, "reported_digest": "a" * 64,
                           "action_count": 1, "name": "execute", "args_digest": approval.ARGS_DIGEST, "decision": decision}],
            "receipts": [{**row, "args_digest": approval.ARGS_DIGEST, "exit_code": 0} for row in operations],
            "counter": {"downloaded": True, "size": 1, "sha256": checksum, "declared": value}}


def pair(case_id="AET-W04"):
    before, after = state(case_id, waiting=True), state(case_id)
    for key in ("environment_id", "owner_id", "namespace", "run_id", "task_id"):
        after["work"][key] = before["work"][key]
    after["approvals"][0]["id"] = before["approvals"][0]["id"]
    return before, after


@pytest.mark.parametrize("case_id", ["AET-W03", "AET-W04"])
def test_fixed_reject_and_approve_controls(case_id):
    before, after = pair(case_id)
    replay = copy.deepcopy(after) if case_id == "AET-W04" else None
    assert [row.status for row in approval.evaluate(before, after, replay, case_id)] == ["PASS", "PASS"]


@pytest.mark.parametrize("mutation", ["counter", "receipt", "decision", "action", "digest", "before", "replay", "queue"])
def test_wrong_counter_approval_or_repeat_effect_is_not_a_pass(mutation):
    before, after = pair()
    replay = copy.deepcopy(after)
    if mutation == "counter":
        after["counter"]["declared"] = "0"
        after["counter"]["sha256"] = after["work"]["artifacts"][0]["digest"] = hashlib.sha256(b"0").hexdigest()
    elif mutation == "receipt":
        after["receipts"] *= 2
        after["work"]["operations"] *= 2
    elif mutation == "decision":
        after["approvals"][0]["decision"] = "reject"
    elif mutation == "action":
        before["approvals"][0]["args_digest"] = "b" * 64
    elif mutation == "digest":
        before["approvals"][0]["reported_digest"] = "b" * 64
    elif mutation == "before":
        before["work"]["status"] = "RUNNING"
    elif mutation == "replay":
        replay["work"]["usage"]["calls"] += 1
    else:
        after["work"]["events"] = after["work"]["events"][1:]
    assert approval.evaluate(before, after, replay, "AET-W04")[0].status != "PASS"


@pytest.mark.parametrize("mutation", ["pending", "unknown", "provider", "sandbox", "operation", "incomplete", "mismatch"])
def test_unsettled_resources_are_blocked(mutation):
    value = state()
    work = value["work"]
    if mutation in {"pending", "unknown"}:
        work["usage"][mutation + "_calls"] = 1
    elif mutation == "provider":
        work["provider"]["complete"] = False
    elif mutation == "sandbox":
        work["sandbox_states"]["UNKNOWN"] = 1
    elif mutation == "operation":
        work["operations"][0]["status"] = value["receipts"][0]["status"] = "UNKNOWN"
    elif mutation == "incomplete":
        work["artifacts_complete"] = False
    else:
        value["receipts"][0]["id"] = str(uuid4())
    assert not approval.settled(value)


def test_missing_artifact_is_known_failure_but_unconfirmed_download_is_blocked():
    value = state()
    value["counter"] = {"downloaded": False, "size": None, "sha256": None, "declared": None}
    assert approval.counter(value, "1") == "BLOCKED"
    value["work"]["artifacts"] = []
    assert approval.counter(value, "1") == "FAIL"
    value["work"]["artifacts_complete"] = False
    assert approval.counter(value, "1") == "BLOCKED"


@pytest.mark.parametrize("value", [True, 1.0, "1", -1])
def test_typed_receipt_exit_and_counts(value):
    observed = state()
    observed["receipts"][0]["exit_code"] = value
    if value == -1:
        assert approval.parse(observed).receipts[0].exit_code == -1
    else:
        with pytest.raises(ValidationError):
            approval.parse(observed)


@pytest.mark.parametrize("content", [b"1", b"0", b"unexpected", b"x" * 65])
def test_download_is_bounded_and_bound_to_latest_formal_artifact(content):
    class Platform:
        http = httpx.Client(base_url="http://127.0.0.1:9999", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content)))
    row = {"path": "/counter.txt", "id": str(uuid4()), "size": len(content), "digest": hashlib.sha256(content).hexdigest()}
    try:
        if len(content) > 64:
            with pytest.raises(adapters.Blocked):
                approval.download_counter(Platform(), [row])
        else:
            result = approval.download_counter(Platform(), [row])
            assert result["declared"] == (content.decode() if content in (b"0", b"1") else None)
            row["digest"] = "c" * 64
            with pytest.raises(adapters.Blocked):
                approval.download_counter(Platform(), [row])
    finally:
        Platform.http.close()
