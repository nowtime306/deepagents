"""Fixed approval decisions and retained facts; unknown submissions are never replayed."""

import time
from uuid import UUID

from . import adapters
from . import fixed_approval_observation as observation
from .fixed_evidence import binding, load, store
from .journal import digest

KIND = "approval-facts"


def check_case(specimen):
    if specimen.case_id not in observation.CASES or specimen.mode != "work" or specimen.metrics:
        raise ValueError("Only the registered counter approval cases are supported")


def retain(journal, attempt, specimen, stage, body, slot):
    check_case(specimen)
    value = {"schema_version": 1, "kind": KIND, "binding": binding(journal.data, attempt), "stage": stage, "body": body}
    try:
        reference = store(journal.directory, KIND, value)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt[slot] = reference
        journal.save()
    except BaseException:
        attempt["approval_evidence_failed"] = True
        raise
    return reference


def read(directory, data, attempt, reference, specimen, stage):
    check_case(specimen)
    value = load(directory, reference, KIND)
    if (set(value) != {"schema_version", "kind", "binding", "stage", "body"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != KIND or value["stage"] != stage
            or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("Approval fact identity changed")
    state = observation.parse(value["body"])
    root = attempt["platform_root"]
    identifier, task, namespace = str(UUID(root["id"])), str(UUID(attempt["fixture"]["id"])), str(UUID(attempt["sandbox_namespace"]))
    if (root != {"id": identifier, "mode": "work", "fixture_id": task, "path": "/api/v1/work-runs/" + identifier}
            or state.work.case_id != specimen.case_id or str(state.work.run_id) != identifier or str(state.work.task_id) != task
            or str(state.work.owner_id) != data["owner_id"] or str(state.work.environment_id) != data["environment_id"]
            or str(state.work.namespace) != namespace):
        raise ValueError("Approval observation belongs to another run, case or environment")
    return value["body"]


def decide(platform, journal, attempt, specimen):
    if not attempt.get("approval_before_ref"):
        before = observation.collect(platform, attempt, specimen)
        state = observation.parse(before)
        if (state.work.status != "WAITING_APPROVAL" or len(state.approvals) != 1
                or not observation.valid_action(state.approvals[0]) or state.approvals[0].decision is not None
                or observation.counter(before, "0") != "PASS" or state.receipts or state.work.operations
                or not state.work.operations_complete or not state.work.events_complete
                or state.work.sandbox_states != {"RELEASED": 1} or not state.work.sandbox_complete
                or not observation.usage_known(state.work)
                or state.work.usage.calls >= specimen.budgets.max_model_calls
                or state.work.usage.input_tokens + state.work.usage.output_tokens >= specimen.budgets.max_tokens):
            raise adapters.Blocked("The fixed counter action and untouched checkpoint must be verified before deciding")
        retain(journal, attempt, specimen, "waiting", before, "approval_before_ref")
    before = read(journal.directory, journal.data, attempt, attempt["approval_before_ref"], specimen, "waiting")
    row = observation.parse(before).approvals[0]
    decision = "reject" if specimen.case_id == "AET-W03" else "approve"
    journal.post(platform, attempt, f"/api/v1/work-approvals/{row.id}/decisions",
                 {"digest": row.reported_digest, "decision": decision}, "decide-counter-approval")


def confirmed_decision(attempt, approval, decision, key):
    intent = attempt.get("submission_intents", {}).get(key, {})
    return (intent.get("status") == "CONFIRMED"
            and intent.get("path") == f"/api/v1/work-approvals/{approval.id}/decisions"
            and intent.get("payload_digest") == digest({"digest": approval.reported_digest, "decision": decision}))


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    check_case(specimen)
    if not attempt.get("submission_complete"):
        adapters.submit(platform, journal, attempt, specimen, attempt["fixture"])
    deadline = time.monotonic() + specimen.budgets.timeout_seconds
    while True:
        platform.attest()
        row = adapters.read_run(platform, attempt["platform_root"])
        if row["status"] in adapters.TERMINAL:
            break
        if row["status"] == "WAITING_APPROVAL":
            decide(platform, journal, attempt, specimen)
        if cancelled() or time.monotonic() >= deadline:
            journal.post(platform, attempt, attempt["platform_root"]["path"] + "/cancel", {}, "cancel-run")
            raise adapters.Blocked("Approval case cancelled; terminal recovery still needs confirmation")
        time.sleep(0.25)
    if not attempt.get("approval_final_ref"):
        final = observation.collect(platform, attempt, specimen)
        retain(journal, attempt, specimen, "final", final, "approval_final_ref")
    if specimen.case_id == "AET-W04" and not attempt.get("approval_replay_ref"):
        before = read(journal.directory, journal.data, attempt, attempt["approval_before_ref"], specimen, "waiting")
        approval = observation.parse(before).approvals[0]
        if not confirmed_decision(attempt, approval, "approve", "decide-counter-approval"):
            raise adapters.Blocked("An unknown original approval cannot be repeated as an idempotency probe")
        journal.post(platform, attempt, f"/api/v1/work-approvals/{approval.id}/decisions",
                     {"digest": approval.reported_digest, "decision": "approve"}, "probe-repeat-confirmed-decision")
        replay = observation.collect(platform, attempt, specimen)
        retain(journal, attempt, specimen, "replay", replay, "approval_replay_ref")
    return verified(journal.directory, journal.data, attempt, specimen)


def verified(directory, data, attempt, specimen):
    check_case(specimen)
    if attempt.get("approval_evidence_failed") or not attempt.get("approval_before_ref") or not attempt.get("approval_final_ref"):
        raise ValueError("Approval checkpoint/final facts were not confirmed retained")
    expected = {"waiting": attempt["approval_before_ref"], "final": attempt["approval_final_ref"]}
    if specimen.case_id == "AET-W04":
        expected["replay"] = attempt["approval_replay_ref"]
    elif attempt.get("approval_replay_ref"):
        raise ValueError("Unexpected approval repeat probe")
    if digest(attempt.get("evidence_refs")) != digest(list(expected.values())):
        raise ValueError("Approval fact reference set/order changed")
    bodies = {stage: read(directory, data, attempt, reference, specimen, stage) for stage, reference in expected.items()}
    approval = observation.parse(bodies["waiting"]).approvals[0]
    decision = "reject" if specimen.case_id == "AET-W03" else "approve"
    keys = ["decide-counter-approval"] + (["probe-repeat-confirmed-decision"] if specimen.case_id == "AET-W04" else [])
    if not all(confirmed_decision(attempt, approval, decision, key) for key in keys):
        raise ValueError("Approval submissions were not confirmed; no implicit reconciliation")
    return {"before": bodies["waiting"], "after": bodies["final"], "replay": bodies.get("replay"),
            "usage": bodies["final"]["work"]["usage"]}


def evaluate(snapshot, specimen, attempt):
    check_case(specimen)
    facts = observation.evaluate(snapshot["before"], snapshot["after"], snapshot["replay"], specimen.case_id)
    final = observation.parse(snapshot["after"])
    return facts, {"run": {"id": str(final.work.run_id), "status": final.work.status},
                   "usage": {"total": snapshot["usage"]}, "observed": ["usage"]}


def cleanup_ready(directory, data, attempt, specimen):
    snapshot = verified(directory, data, attempt, specimen)
    expected = "0" if specimen.case_id == "AET-W03" else "1"
    return (observation.settled(snapshot["after"]) and observation.counter(snapshot["after"], expected) != "BLOCKED"
            and (snapshot["replay"] is None or observation.settled(snapshot["replay"])
                 and observation.counter(snapshot["replay"], expected) != "BLOCKED"))
