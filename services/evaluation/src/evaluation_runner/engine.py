"""Sequential, journaled execution; unsupported facts remain explicit blockers."""

from pathlib import Path
from uuid import uuid4

from . import adapters, network
from .contracts import Dataset, Result, Target, outcome
from .facts import verify
from .journal import Journal, UnknownSubmission, digest
from .metrics import score
from .reports import write_report


def run(platform, target: Target, dataset: Dataset, output: Path) -> dict:
    journal = Journal(output)
    fingerprint = {"environment_id": target.environment_id, "owner_id": target.owner_id,
                   "target_digest": digest(target.model_dump()), "dataset_digest": digest(dataset.model_dump())}
    try:
        for key, value in fingerprint.items():
            if key in journal.data and journal.data[key] != value:
                raise ValueError("Existing journal belongs to different target or dataset")
        journal.data.update(fingerprint)
        journal.save()
        cancelled = lambda: (output / "cancel.request").exists()
        for case in dataset.cases:
            for repeat in range(1, case.repeats + 1):
                if cancelled():
                    journal.data["cancelled"] = True
                    break
                prior = next((a for a in journal.data["attempts"] if a["case_id"] == case.case_id
                              and a["repeat_index"] == repeat), None)
                if prior and prior.get("task_outcome") and prior.get("cleanup_status") == "CLEAN":
                    continue
                attempt = prior or {"attempt_id": str(uuid4()), "case_id": case.case_id, "repeat_index": repeat,
                                    "environment_id": target.environment_id, "attempt_namespace_id": str(uuid4()),
                                    "case_snapshot": case.model_dump(), "definition_digest": digest(case.model_dump()),
                                    "adapter_version": "1", "started": False, "evidence_completeness": "UNKNOWN",
                                    "usage_completeness": "UNKNOWN", "fact_results": [], "metric_results": []}
                if not prior:
                    journal.data["attempts"].append(attempt)
                journal.save()
                try:
                    platform.attest()
                    fixture = adapters.initialize(platform, journal, attempt, case)
                    if not attempt.get("submission_complete"):
                        adapters.submit(platform, journal, attempt, case, fixture)
                    evidence = adapters.observe(platform, journal, attempt, case, cancelled=cancelled)
                    facts = []
                    for spec in case.validators:
                        facts.append(verify(spec, evidence, platform))
                        attempt["fact_results"] = [row.model_dump() for row in facts]
                        attempt["task_outcome"] = outcome(facts, [], "UNKNOWN")
                        journal.save()
                    total = adapters.known_usage(evidence, case)
                    attempt["usage_completeness"] = "KNOWN"
                    attempt["usage"] = total
                    network.PHASE = "metric"
                    metrics = []
                    if not cancelled():
                        for spec in case.metrics:
                            try:
                                metrics.append(score(spec, evidence))
                            except Exception as error:  # noqa: BLE001 -- Judge/plugin errors are distinct from persisted facts.
                                metrics.append(Result(registered_id=spec.registered_id, required=spec.required, status="ERROR",
                                                      reason="Metric execution failed: " + type(error).__name__))
                    if cancelled() and case.metrics:
                        metrics = [Result(registered_id=s.registered_id, required=s.required, status="NOT_SCORED",
                                          reason="Cancelled before scoring") for s in case.metrics]
                    attempt["fact_results"] = [row.model_dump() for row in facts]
                    attempt["metric_results"] = [row.model_dump() for row in metrics]
                    attempt["platform_status"] = evidence["run"].get("status")
                    attempt["evidence_completeness"] = "SUPPORTED_FACTS_ONLY"
                    attempt["cleanup_status"] = adapters.cleanup(platform, journal, attempt)
                    attempt["task_outcome"] = outcome(facts, metrics, attempt["cleanup_status"])
                    # Negative engineering assertions never turn an actually failed/cancelled task into business success.
                    if attempt["platform_status"] in {"FAILED", "CANCELLED"}:
                        attempt["task_outcome"] = "FAIL"
                    elif attempt["platform_status"] == "NEEDS_REVIEW":
                        attempt["task_outcome"] = "NEEDS_REVIEW"
                    if any(row.required and row.status == "FAIL" for row in facts):
                        attempt["task_outcome"] = "FAIL"
                except (adapters.Blocked, UnknownSubmission) as error:
                    untouched = not attempt.get("created_resources") and not attempt.get("submission_intents")
                    failed = any(row["required"] and row["status"] == "FAIL" for row in attempt["fact_results"])
                    attempt.update(task_outcome="FAIL" if failed else "NEEDS_REVIEW", reason=str(error),
                                   cleanup_status="CLEAN" if untouched else "NEEDS_REVIEW")
                except Exception as error:  # noqa: BLE001 -- A failed observer must preserve partial evidence and block CI.
                    # Do not serialize HTTP responses, credentials, arbitrary tool logs or stack locals.
                    failed = any(row["required"] and row["status"] == "FAIL" for row in attempt["fact_results"])
                    attempt.update(task_outcome="FAIL" if failed else "NEEDS_REVIEW", reason="Execution/observer error: " + type(error).__name__,
                                   cleanup_status="NEEDS_REVIEW")
                journal.save()
                if attempt["cleanup_status"] != "CLEAN":
                    journal.data["stopped_for_cleanup"] = True
                    break
            if journal.data.get("stopped_for_cleanup") or cancelled():
                break
        expected = sum(case.repeats for case in dataset.cases)
        journal.data["expected_attempts"] = expected
        network.PHASE = "export"
        journal.data["network_observations"] = list(network.EVENTS)
        journal.save()
        return write_report(output, journal.data)
    finally:
        journal.close()
