"""Fixed A20 engineering profiles. Every effect uses formal HTTP and a durable intent."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from . import adapters, fixed_audit, fixed_evidence, fixed_workflow_evidence, fixed_workflow_observation, network
from .client import Platform
from .contracts import Dataset, Result, Target
from .fixed_fixtures import FixedFixtures
from .journal import Journal, atomic_json, digest
from .metrics import score

RULES_ID = "controlled-a20-v1"
IMPLEMENTED = {"AET-A02", "AET-W01", "AET-F02", "AET-F04"}
CATALOG_PATH = Path(__file__).resolve().parents[2] / "datasets/a20-fixed.json"


def catalog():
    return Dataset.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def evidence_module(specimen):
    if specimen.case_id == "AET-A02":
        return fixed_evidence
    if specimen.case_id in fixed_workflow_observation.CASES:
        return fixed_workflow_evidence
    if specimen.case_id == "AET-W01":
        from . import fixed_work_evidence
        return fixed_work_evidence
    raise adapters.Blocked("No fixed evidence handler")


def readiness(platform, specimen):
    """Identity attestation is separate from a read-only, case-specific readiness verdict."""
    proof = platform.attest()
    missing = []
    if specimen.case_id not in IMPLEMENTED:
        missing.append("Fixed init/observe/cleanup handler is still being implemented")
    user = platform.get("/api/v1/auth/me")
    needed = {"evaluation.read", "evaluation.manage", "evaluation.run"}
    needed |= ({"work.read", "work.manage", "work.execute"} if specimen.mode == "work" else
               {"workflows.read", "workflows.manage", "workflows.execute"} if specimen.mode == "workflow" else
               {"chat.read", "chat.manage", "chat.run", "agents.manage"})
    if not user.get("is_admin"):
        missing.append("Dedicated target administrator required for controlled-model initialization")
    if not needed.issubset(set(proof.get("permissions", []))):
        missing.append("Underlying evaluation/business permissions are missing")
    try:
        capabilities = platform.get("/api/v1/agent-evaluations/profiles/capabilities")
        if "root-model-context-v1" not in capabilities.get("observers", []):
            missing.append("Trusted root context observer is unavailable")
        if specimen.case_id in fixed_workflow_observation.CASES:
            if "readonly-workflow-effects-v1" not in capabilities.get("observers", []):
                missing.append("Trusted readonly workflow effects observer is unavailable")
            bound = proof.get("execution_limits", {}).get("profiles", {}).get(specimen.case_id, {})
            if bound.get("max_model_calls") != 0 or bound.get("max_tokens") != 0:
                missing.append("The fixed readonly profiles require enforced zero model calls/tokens")
        if specimen.mode == "work":
            work = platform.get("/api/v1/work-capabilities")
            bound = capabilities.get("sandbox_limits") or {}
            if (not work.get("enabled") or not capabilities.get("work_manager", {}).get("ready")
                    or bound.get("enforcement") != "controlled-sandbox-reserve-v1"
                    or bound.get("scope") != "test-environment" or bound.get("max_created") != 2
                    or not bound.get("storage_healthy") or bound.get("attempts", 2) >= 2):
                missing.append("A live worker/manager and enforced target sandbox bound are required")
        execution = specimen.model_copy(update={"fixture_parameters": {
            "prior_messages": specimen.fixture_parameters.get("fixed_inputs", {}).get("messages", [])[:-1]}})
        adapters.enforce_budget(execution, proof)
    except (ValueError, httpx.HTTPError, adapters.Blocked):
        missing.append("Trusted profile observations or budget enforcement are unavailable")
    return {"case_id": specimen.case_id, "status": "BLOCKED" if missing else "READY", "missing": missing,
            "scope": "controlled engineering; not a semantic-quality or UI verdict"}


def initialize(platform, journal, attempt, specimen):
    status = readiness(platform, specimen)
    attempt["readiness"] = status
    journal.save()
    if status["status"] != "READY":
        raise adapters.Blocked("Case readiness is blocked")
    if specimen.case_id == "AET-W01" or specimen.case_id in fixed_workflow_observation.CASES:
        namespace = str(UUID(platform.attest()["sandbox_namespace"]))
        if "sandbox_namespace" in attempt and attempt["sandbox_namespace"] != namespace:
            raise ValueError("The attested sandbox namespace changed")
        attempt["sandbox_namespace"] = namespace
        journal.save()
        platform.expected_sandbox_namespace = namespace
    fixtures = FixedFixtures(platform, journal, attempt)
    model = fixtures.model(specimen.case_id)
    if specimen.case_id == "AET-A02":
        agent = fixtures.published_agent(specimen.case_id, model)
        attempt["fixture"] = {"model_id": model, "id": fixtures.conversation(agent), **agent}
    elif specimen.case_id == "AET-W01":
        inputs = specimen.fixture_parameters["fixed_inputs"]
        attempt["fixture"] = {"model_id": model, **fixtures.work_task(inputs["goal"], {})}
    elif specimen.case_id in fixed_workflow_observation.CASES:
        inputs = specimen.fixture_parameters["fixed_inputs"]
        attempt["fixture"] = {"model_id": model, **fixtures.readonly_workflow(
            specimen.case_id, model, fixed_workflow_observation.definition(specimen.case_id), inputs.get("valid_contrast", specimen.input))}
    else:
        raise adapters.Blocked("No fixed initialization handler")
    journal.save()
    if specimen.case_id in fixed_workflow_observation.CASES:
        fixed_workflow_evidence.ensure_baseline(platform, journal, attempt, specimen)


def execute(platform, journal, attempt, specimen, output):
    if not attempt.get("fixture"):
        raise adapters.Blocked("A confirmed fixed fixture initialization is required")
    inputs = specimen.fixture_parameters["fixed_inputs"]
    actual_case = specimen.model_copy(update={"fixture_parameters": {"prior_messages": inputs.get("messages", [])[:-1]}})
    module = evidence_module(specimen)
    if specimen.case_id == "AET-W01" or specimen.case_id in fixed_workflow_observation.CASES:
        platform.expected_sandbox_namespace = attempt["sandbox_namespace"]
        platform.attest()
    if specimen.case_id in fixed_workflow_observation.CASES:
        snapshot = fixed_workflow_evidence.execute(platform, journal, attempt, specimen,
                                                  cancelled=lambda: (output / "cancel.request").exists())
    else:
        if not attempt.get("submission_complete"):
            adapters.submit(platform, journal, attempt, actual_case, attempt["fixture"])
        evidence = adapters.observe(platform, journal, attempt, actual_case,
                                    cancelled=lambda: (output / "cancel.request").exists())
        snapshot = module.capture(platform, journal, attempt, specimen, evidence)
    facts, evidence = module.evaluate(snapshot, specimen, attempt)
    attempt["fact_results"] = [fact.model_dump() for fact in facts]
    attempt["platform_status"] = evidence["run"]["status"]
    if "business_outcomes" in evidence:
        attempt["business_outcomes"] = evidence["business_outcomes"]
    attempt["task_outcome"] = "FAIL" if any(row.status == "FAIL" for row in facts) else "NEEDS_REVIEW"
    journal.save()
    attempt["usage"] = adapters.known_usage(evidence, specimen)
    attempt["usage_completeness"] = "KNOWN"
    metrics = []
    for check in specimen.metrics:
        try:
            metrics.append(score(check, evidence))
        except Exception as error:  # noqa: BLE001 -- Preserve already-persisted factual failures on scorer errors.
            metrics.append(Result(registered_id=check.registered_id, status="ERROR", reason=type(error).__name__))
    attempt["metric_results"] = [metric.model_dump() for metric in metrics]
    if all(row.status == "PASS" for row in facts + metrics) and attempt["platform_status"] in {"SUCCEEDED", "COMPLETED"}:
        attempt["task_outcome"] = "PASS"
    journal.save()


def cleanup(platform, journal, attempt):
    """Retain historical evidence; only a target with no active/unknown effects is clean."""
    intents = [*attempt.get("fixture_intents", {}).values(), *attempt.get("submission_intents", {}).values()]
    if any(row["status"] not in {"CONFIRMED", "REJECTED"} for row in intents):
        return "NEEDS_REVIEW"
    root = attempt.get("platform_root")
    if not root:
        return "CLEAN" if not attempt.get("created_resources") else "NEEDS_REVIEW"
    specimen = next(row for row in catalog().cases if row.case_id == attempt["case_id"])
    module = evidence_module(specimen)
    if specimen.case_id == "AET-W01" or specimen.case_id in fixed_workflow_observation.CASES:
        platform.expected_sandbox_namespace = attempt["sandbox_namespace"]
        platform.attest()
    module.verified(journal.directory, journal.data, attempt, specimen)
    pending = network.ACTIVE.data["session_id"] if network.ACTIVE else None
    fixed_audit.validate(journal.directory, journal.data, pending_session=pending)
    for reference in [*attempt.get("prior_run_refs", {}).values(), root]:
        if adapters.read_run(platform, reference).get("status") not in {"SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED"}:
            return "NEEDS_REVIEW"
    if attempt.get("usage_completeness") != "KNOWN":
        return "NEEDS_REVIEW"
    if specimen.case_id == "AET-W01" and not module.cleanup_ready(journal.directory, journal.data, attempt, specimen):
        return "NEEDS_REVIEW"
    if specimen.case_id in fixed_workflow_observation.CASES and module.cleanup(platform, journal, attempt, specimen) != "CLEAN":
        return "NEEDS_REVIEW"
    if root["mode"] == "work":
        receipts = platform.get(root["path"] + "/operations")["items"]
        if any(row["status"] in {"STARTED", "UNKNOWN"} for row in receipts):
            return "NEEDS_REVIEW"
        observation = platform.get("/api/v1/agent-evaluations/profiles/runs/work/" + root["id"] + "/observations")
        sessions = observation.get("sandbox_sessions", {})
        if sessions.get("complete") is not True or any(state != "RELEASED" and count for state, count in sessions.get("states", {}).items()):
            return "NEEDS_REVIEW"
    attempt["retained_resources"] = [{**resource, "reason": "Owned execution evidence; deprovision with this target only"}
                                     for resource in attempt.get("created_resources", [])]
    journal.save()
    return "CLEAN"


def describe(specimen, dataset):
    return {"schema_version": 1, "catalog_digest": digest(dataset.model_dump(mode="json")),
            "case_definition_digest": digest(specimen.model_dump(mode="json")), "case_id": specimen.case_id,
            "rules_id": RULES_ID, "implemented": specimen.case_id in IMPLEMENTED,
            "maximum_model_calls": specimen.budgets.max_model_calls, "maximum_tokens": specimen.budgets.max_tokens,
            "maximum_sandboxes": 2, "sandbox_enforcement": "controlled-sandbox-reserve-v1"}


def suite_context(path, specimen, dataset, target, existing=None):
    expected = describe(specimen, dataset)
    value = (json.loads(path.read_text(encoding="utf-8")) if path else existing) or {
        **expected, "suite_id": str(uuid4()), "repeat_index": 1, "candidate_commit": target.platform_commit}
    for key in ("catalog_digest", "case_definition_digest", "case_id", "rules_id"):
        if value.get(key) != expected[key]:
            raise ValueError("Suite context does not match the fixed reviewed catalog")
    if (type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or type(value.get("repeat_index")) is not int or not 1 <= value["repeat_index"] <= 3
            or value.get("candidate_commit") != target.platform_commit):
        raise ValueError("Invalid suite repeat/candidate identity")
    UUID(value["suite_id"])
    return value


def validate_offline_report(data, specimen, dataset, *, context_path=None, target_path=None, directory=None):
    expected = describe(specimen, dataset)
    context = data["suite_context"]
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or type(context.get("schema_version")) is not int or context["schema_version"] != 1
            or type(context.get("repeat_index")) is not int or not 1 <= context["repeat_index"] <= 3):
        raise ValueError("Invalid offline report version/repeat identity")
    for key in ("catalog_digest", "case_definition_digest", "case_id", "rules_id"):
        if context.get(key) != expected[key]:
            raise ValueError("Offline report does not match the requested fixed case/catalog/rules")
    if data.get("dataset_digest") != expected["catalog_digest"] or data.get("case_definition_digest") != expected["case_definition_digest"]:
        raise ValueError("Offline journal definition changed")
    UUID(data["owner_id"])
    UUID(data["environment_id"])
    UUID(context["suite_id"])
    if context_path:
        requested = json.loads(context_path.read_text(encoding="utf-8"))
        identity = ("schema_version", "suite_id", "catalog_digest", "case_definition_digest", "repeat_index", "case_id", "candidate_commit", "rules_id")
        if any(type(requested.get(key)) is not type(context.get(key)) or requested.get(key) != context.get(key) for key in identity):
            raise ValueError("Requested suite differs from the recorded suite")
    if target_path:
        target = Target.model_validate_json(target_path.read_text(encoding="utf-8"))
        if (target.owner_id != data["owner_id"] or target.environment_id != data["environment_id"]
                or target.platform_commit != context["candidate_commit"] or digest(target.model_dump()) != data.get("target_digest")):
            raise ValueError("Requested target differs from the recorded owner/environment")
    if not data["attempts"]:
        raise ValueError("No recorded fixed execution")
    for attempt in data["attempts"]:
        if (attempt.get("case_id") != specimen.case_id or type(attempt.get("repeat_index")) is not int
                or attempt.get("repeat_index") != context["repeat_index"]
                or attempt.get("owner_id") != data["owner_id"] or attempt.get("environment_id") != data["environment_id"]
                or digest(attempt.get("case_snapshot")) != expected["case_definition_digest"]):
            raise ValueError("Attempt identity or immutable snapshot differs")
        if attempt.get("started"):
            if directory is None:
                raise ValueError("A retained evidence directory is required")
            module = evidence_module(specimen)
            snapshot = module.verified(directory, data, attempt, specimen)
            facts, evidence = module.evaluate(snapshot, specimen, attempt)
            if ([row.model_dump() for row in facts] != attempt.get("fact_results")
                    or snapshot["usage"] != attempt.get("usage")
                    or "business_outcomes" in evidence and evidence["business_outcomes"] != attempt.get("business_outcomes")):
                raise ValueError("Stored verdict differs from independently recomputed facts")
            if attempt.get("task_outcome") == "PASS" and any(row.status != "PASS" for row in facts):
                raise ValueError("A failing factual snapshot cannot produce PASS")
            if (attempt.get("cleanup_status") == "CLEAN"
                    and (specimen.case_id == "AET-W01" or specimen.case_id in fixed_workflow_observation.CASES)
                    and not module.cleanup_ready(directory, data, attempt, specimen)):
                raise ValueError("Stored CLEAN lacks settled retained cleanup evidence")
            metrics = [Result.model_validate(row) for row in attempt.get("metric_results", [])]
            if any(row.registered_id == "tool_correctness" and row.status == "PASS"
                   and not evidence.get("tools_complete") for row in metrics):
                raise ValueError("Saved tool score lacks complete retained no-tool evidence")
            if ([row.registered_id for row in metrics] != [check.registered_id for check in specimen.metrics]
                    or any(row.required != check.required for row, check in zip(metrics, specimen.metrics, strict=True))
                    or (attempt.get("task_outcome") == "PASS" and any(row.status != "PASS" for row in metrics))):
                raise ValueError("Required metric results are missing or inconsistent")
    fixed_audit.validate(directory, data, pending_session=network.ACTIVE.data["session_id"] if network.ACTIVE else None)



def main():
    parser = argparse.ArgumentParser(description="Fixed controlled A20 engineering profiles")
    parser.add_argument("action", choices=["describe", "preflight", "init", "run", "cleanup", "report"])
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--target-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--suite-context", type=Path)
    args = parser.parse_args()
    dataset = catalog()
    specimen = next((row for row in dataset.cases if row.case_id == args.case_id), None)
    if specimen is None:
        parser.error("Only fixed reviewed case IDs are accepted")
    if args.output is None and args.action != "describe":
        parser.error("--output is required")
    output = args.output.resolve() if args.output else None
    session = network.Session(args.action, describe(specimen, dataset), None)
    network.install_guard(None, session=session)
    if output is None:
        with network.phase("execution"):
            result = describe(specimen, dataset)
        with network.phase("export"):
            result = dict(result)
        session.data.update(status="COMPLETE", ended_at=network.now(), policy_digest=digest(session.data["policy"]))
        session.closed = True
        print(json.dumps({**result, "network_audit": session.data, "network_audit_digest": digest(session.data)}))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    journal, platform = None, None
    try:
        journal = Journal(output)
        fixed_audit.begin(output, session)
        if args.action == "describe":
            with network.phase("execution"):
                result = describe(specimen, dataset)
            with network.phase("export"):
                atomic_json(output / "fixed-description.json", result)
            fixed_audit.seal(output, session, "COMPLETE")
            print(json.dumps(result))
            return 0
        with network.phase("execution"):
            if args.action == "report":
                validate_offline_report(journal.data, specimen, dataset, context_path=args.suite_context,
                                        target_path=args.target_manifest, directory=output)
                session.data["identity"].update({key: journal.data[key] for key in
                                               ("environment_id", "owner_id", "target_digest")})
                session.data["identity"]["candidate_commit"] = journal.data["suite_context"]["candidate_commit"]
            else:
                if args.target_manifest is None:
                    raise ValueError("A target manifest is required")
                target = Target.model_validate_json(args.target_manifest.read_text(encoding="utf-8"))
                session.data["identity"].update(environment_id=target.environment_id, owner_id=target.owner_id,
                                               target_digest=digest(target.model_dump()), candidate_commit=target.platform_commit)
                session.data["policy"]["allowed_origin"] = target.api_base_url
                network.install_guard(urlsplit(target.api_base_url).port, session=session)
                for key in ("DEEPEVAL_HOME", "ENV_DIR_PATH", "DEEPEVAL_RESULTS_FOLDER", "DEEPEVAL_CACHE_FOLDER"):
                    private = output / key.lower()
                    private.mkdir(exist_ok=True)
                    os.environ[key] = str(private)
                platform = Platform(target)
                platform.login()
                if args.action == "preflight":
                    result = readiness(platform, specimen)
                    with network.phase("export"):
                        atomic_json(output / "fixed-preflight.json", {"environment_status": "ATTESTED", **result,
                                                                     **describe(specimen, dataset)})
                else:
                    context = suite_context(args.suite_context, specimen, dataset, target, journal.data.get("suite_context"))
                    identity = {"environment_id": target.environment_id, "owner_id": target.owner_id,
                                "target_digest": digest(target.model_dump()), "dataset_digest": context["catalog_digest"],
                                "case_definition_digest": context["case_definition_digest"], "suite_context": context,
                                "expected_attempts": 1}
                    for key, value in identity.items():
                        if key in journal.data and journal.data[key] != value:
                            raise ValueError("Existing journal identity changed")
                    journal.data.update(identity)
                    session.data["identity"].update(suite_id=context["suite_id"], repeat_index=context["repeat_index"])
                    if not journal.data["attempts"]:
                        journal.data["attempts"].append({"attempt_id": str(uuid4()), "case_id": specimen.case_id,
                            "repeat_index": context["repeat_index"], "started": False,
                            "environment_id": target.environment_id, "owner_id": target.owner_id,
                            "case_snapshot": specimen.model_dump(), "fact_results": [], "metric_results": [],
                            "cleanup_status": "NOT_CHECKED", "task_outcome": "NEEDS_REVIEW",
                            "cost_completeness": "UNKNOWN", "cost": None})
                    attempt = journal.data["attempts"][0]
                    journal.save()
                    if (output / "cancel.request").exists() and args.action in {"init", "run"}:
                        raise adapters.Blocked("Cancelled before further dispatch")
                    if args.action == "init":
                        initialize(platform, journal, attempt, specimen)
                    elif args.action == "run":
                        status = readiness(platform, specimen)
                        if status["status"] != "READY":
                            raise adapters.Blocked("Read-only case readiness blocked execution")
                        attempt["run_audit_session_id"] = session.data["session_id"]
                        journal.save()
                        execute(platform, journal, attempt, specimen, output)
                        attempt["cleanup_status"] = cleanup(platform, journal, attempt)
                        if attempt["cleanup_status"] != "CLEAN" and attempt["task_outcome"] == "PASS":
                            attempt["task_outcome"] = "NEEDS_REVIEW"
                    elif args.action == "cleanup":
                        attempt["cleanup_status"] = cleanup(platform, journal, attempt)
        if args.action == "preflight":
            fixed_audit.seal(output, session, "COMPLETE")
            return 0 if result["status"] == "READY" else 1
        report = fixed_audit.publish(output, journal, session)
        if args.action == "init":
            return 0
        if args.action == "cleanup":
            return 0 if attempt["cleanup_status"] == "CLEAN" else 1
        return 0 if report["gate"] == "PASS" else 1
    except Exception as error:  # noqa: BLE001 -- Preserve facts, safe failure types and per-command audit on every exit.
        if journal is not None and args.action in {"init", "run", "cleanup"}:
            for attempt in journal.data.get("attempts", []):
                failed = any(row.get("required") and row.get("status") == "FAIL" for row in attempt.get("fact_results", []))
                attempt.update(task_outcome="FAIL" if failed else "NEEDS_REVIEW", cleanup_status="NEEDS_REVIEW",
                               reason="Fixed profile blocked/failed: " + type(error).__name__)
            journal.save()
        if journal is not None and not session.closed:
            fixed_audit.seal(output, session, "ERROR")
        print("Fixed evaluation blocked: " + type(error).__name__)
        return 2 if isinstance(error, (ValueError, KeyError, TypeError)) else 1
    finally:
        if journal is not None:
            journal.close()
        if platform is not None:
            platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
