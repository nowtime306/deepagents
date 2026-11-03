"""W04 proof of a cooperative Work consumer rebuild, not an OS process restart."""

import json
from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import StrictModel
from .fixed_evidence import binding, load, store
from .journal import digest

PATH = "/api/v1/agent-evaluations/profiles/work-counter-consumers"
KIND = "approval-consumer"
INTENT = "rebuild-counter-consumer"


class Proof(StrictModel):
    schema_version: Literal[1]
    case_id: Literal["AET-W04"]
    source_run_id: UUID
    owner_id: UUID
    environment_id: UUID
    namespace: UUID
    status: Literal["REBUILT"]
    before_worker_ids: list[UUID] = Field(min_length=1, max_length=1)
    after_worker_ids: list[UUID] = Field(min_length=1, max_length=1)
    checkpoint_id_before: UUID
    checkpoint_id_after: UUID


def validate(body, work):
    if type(body.get("schema_version")) is not int:
        raise ValueError("Consumer rebuild proof version must be an integer")
    proof = Proof.model_validate_json(json.dumps(body, allow_nan=False))
    if (proof.source_run_id != work.run_id or proof.owner_id != work.owner_id
            or proof.environment_id != work.environment_id or proof.namespace != work.namespace
            or proof.before_worker_ids == proof.after_worker_ids
            or proof.checkpoint_id_before != proof.checkpoint_id_after):
        raise ValueError("Consumer rebuild belongs to another root or changed its checkpoint")
    return proof.model_dump(mode="json")


def capture(platform, journal, attempt, work):
    if attempt.get("approval_evidence_failed"):
        raise ValueError("Approval evidence persistence failed; no new consumer action")
    if attempt.get("approval_consumer_ref"):
        return verified(journal.directory, journal.data, attempt, work)
    payload = {"source_run_id": str(work.run_id)}
    journal.post(platform, attempt, PATH + "/rebuild", payload, INTENT)
    proof = validate(platform.get(PATH + "/" + str(work.run_id)), work)
    snapshot = {"schema_version": 1, "kind": KIND, "binding": binding(journal.data, attempt), "body": proof}
    try:
        reference = store(journal.directory, KIND, snapshot)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt["approval_consumer_ref"] = reference
        journal.save()
    except BaseException:
        attempt["approval_evidence_failed"] = True
        raise
    return verified(journal.directory, journal.data, attempt, work)


def verified(directory, data, attempt, work):
    if attempt.get("approval_evidence_failed") or not attempt.get("approval_consumer_ref"):
        raise ValueError("Consumer rebuild evidence was not confirmed retained")
    intent = attempt.get("submission_intents", {}).get(INTENT, {})
    if (intent.get("status") != "CONFIRMED" or intent.get("path") != PATH + "/rebuild"
            or intent.get("payload_digest") != digest({"source_run_id": str(work.run_id)})):
        raise ValueError("An unknown consumer rebuild cannot be implicitly reconciled")
    value = load(directory, attempt["approval_consumer_ref"], KIND)
    if (set(value) != {"schema_version", "kind", "binding", "body"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != KIND
            or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("Consumer rebuild fact identity changed")
    return validate(value["body"], work)
