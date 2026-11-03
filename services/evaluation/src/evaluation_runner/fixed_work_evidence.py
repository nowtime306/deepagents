"""Retained W01 observations; a known missing artifact is a failure, not an evidence gap."""

from pathlib import Path
from uuid import UUID

from . import fixed_work_observation as work
from .contracts import Case, Result
from .fixed_evidence import binding, load, store
from .journal import Journal, digest

KIND = "w01-facts"


def check_case(specimen: Case) -> None:
    if specimen.case_id != "AET-W01" or specimen.mode != "work" or specimen.metrics:
        raise ValueError("Only the registered W01 Work facts are supported")


def root_identity(attempt: dict) -> tuple[str, str, str]:
    root = attempt["platform_root"]
    identifier, task = str(UUID(root["id"])), str(UUID(attempt["fixture"]["id"]))
    namespace = str(UUID(attempt["sandbox_namespace"]))
    if (root["mode"] != "work" or root["path"] != "/api/v1/work-runs/" + identifier
            or root["fixture_id"] != task):
        raise ValueError("Unexpected fixed Work run or task reference")
    return identifier, task, namespace


def capture(platform, journal: Journal, attempt: dict, specimen: Case, evidence: dict) -> dict:
    """Seal formal observations before facts or cleanup can consume them."""
    check_case(specimen)
    identifier, task, _ = root_identity(attempt)
    if str(evidence["run"]["id"]) != identifier or evidence["root"] != attempt["platform_root"]:
        raise ValueError("Adapter observations belong to another Work root")
    observation = work.collect(platform, identifier, task)
    if observation["status"] != evidence["run"]["status"]:
        raise ValueError("Work terminal state changed during observation")
    snapshot = {"schema_version": 1, "kind": KIND, "binding": binding(journal.data, attempt),
                "observation": observation, "usage": observation["usage"]}
    try:
        reference = store(journal.directory, KIND, snapshot)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt.pop("work_evidence_failed", None)
        journal.save()
    except BaseException:
        attempt["work_evidence_failed"] = True
        raise
    return verified(journal.directory, journal.data, attempt, specimen)


def verified(directory: Path, data: dict, attempt: dict, specimen: Case) -> dict:
    check_case(specimen)
    if attempt.get("work_evidence_failed"):
        raise ValueError("Work evidence persistence was not confirmed")
    references = attempt.get("evidence_refs", [])
    if not references:
        raise ValueError("W01 facts were not retained")
    for reference in references:
        snapshot = load(directory, reference, KIND)
        if (set(snapshot) != {"schema_version", "kind", "binding", "observation", "usage"}
                or type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1
                or snapshot["kind"] != KIND or digest(snapshot["binding"]) != digest(binding(data, attempt))):
            raise ValueError("Work fact snapshot identity changed")
        observation = work.parse(snapshot["observation"])
        declared_usage = work.NativeUsage.model_validate(snapshot["usage"])
        if (str(observation.owner_id) != data["owner_id"] or str(observation.environment_id) != data["environment_id"]
                or declared_usage.model_dump() != observation.usage.model_dump()):
            raise ValueError("Work observation owner, environment or usage changed")
        evaluate(snapshot, specimen, attempt)
    return snapshot


def evaluate(snapshot: dict, specimen: Case, attempt: dict) -> tuple[list[Result], dict]:
    """Recompute from retained bytes; no live service or model is called."""
    check_case(specimen)
    identifier, task, namespace = root_identity(attempt)
    observation = work.parse(snapshot["observation"])
    if (str(observation.run_id) != identifier or str(observation.task_id) != task
            or str(observation.namespace) != namespace):
        raise ValueError("Work observation root, task or initial namespace differs")
    facts = work.evaluate(snapshot["observation"])
    adapter = {"run": {"id": identifier, "status": observation.status},
               "usage": {"total": observation.usage.model_dump()}, "observed": ["usage"]}
    return facts, adapter


def cleanup_ready(directory: Path, data: dict, attempt: dict, specimen: Case) -> bool:
    """Allow disposal of a proven negative control, never missing or unsettled evidence."""
    snapshot = verified(directory, data, attempt, specimen)
    facts, _ = evaluate(snapshot, specimen, attempt)
    statuses = {fact.registered_id: fact.status for fact in facts}
    return statuses.pop("artifact_exists", None) == "FAIL" and all(status == "PASS" for status in statuses.values())
