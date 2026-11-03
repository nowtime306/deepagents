"""Fixed A20 engineering profiles. Every effect uses formal HTTP and a durable intent."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from . import adapters, network
from .client import Platform
from .contracts import Check, Dataset, Result, Target
from .fixed_fixtures import FixedFixtures
from .journal import Journal, atomic_json, digest
from .metrics import score
from .reports import write_report

RULES_ID = "controlled-a20-v1"
IMPLEMENTED = {"AET-A02"}
CATALOG_PATH = Path(__file__).resolve().parents[2] / "datasets/a20-fixed.json"


def catalog():
    return Dataset.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def readiness(platform, specimen):
    """Identity attestation is separate from a read-only, case-specific readiness verdict."""
    proof = platform.attest()
    missing = []
    if specimen.case_id not in IMPLEMENTED:
        missing.append("Fixed init/observe/cleanup handler is still being implemented")
    user = platform.get("/api/v1/auth/me")
    needed = {"evaluation.read", "evaluation.manage", "evaluation.run", "agents.manage"}
    needed |= {"work.read", "work.manage", "work.execute"} if specimen.mode == "work" else {"chat.read", "chat.manage", "chat.run"}
    if not user.get("is_admin"):
        missing.append("Dedicated target administrator required for controlled-model initialization")
    if not needed.issubset(set(proof.get("permissions", []))):
        missing.append("Underlying evaluation/business permissions are missing")
    try:
        capabilities = platform.get("/api/v1/agent-evaluations/profiles/capabilities")
        if "root-model-context-v1" not in capabilities.get("observers", []):
            missing.append("Trusted root context observer is unavailable")
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
    fixtures = FixedFixtures(platform, journal, attempt)
    model = fixtures.model(specimen.case_id)
    if specimen.case_id == "AET-A02":
        agent = fixtures.published_agent(specimen.case_id, model)
        attempt["fixture"] = {"model_id": model, "id": fixtures.conversation(agent), **agent}
    elif specimen.case_id == "AET-W01":
        inputs = specimen.fixture_parameters["fixed_inputs"]
        attempt["fixture"] = {"model_id": model, **fixtures.work_task(inputs["goal"], {})}
    else:
        raise adapters.Blocked("No fixed initialization handler")
    journal.save()


def execute(platform, journal, attempt, specimen, output):
    if not attempt.get("fixture"):
        raise adapters.Blocked("A confirmed fixed fixture initialization is required")
    inputs = specimen.fixture_parameters["fixed_inputs"]
    actual_case = specimen.model_copy(update={"fixture_parameters": {"prior_messages": inputs.get("messages", [])[:-1]}})
    if not attempt.get("submission_complete"):
        adapters.submit(platform, journal, attempt, actual_case, attempt["fixture"])
    evidence = adapters.observe(platform, journal, attempt, actual_case,
                                cancelled=lambda: (output / "cancel.request").exists())
    facts = []
    if specimen.case_id == "AET-A02":
        from .facts import verify

        facts.append(verify(Check(registered_id="user_message_count", parameters={"expected": 2}), evidence, platform))
        try:
            actual = json.loads(evidence["run"]["output"])
            matches = json.dumps(actual, sort_keys=True) == json.dumps({"project": "星桥", "people": 3}, sort_keys=True)
            facts.append(Result(registered_id="output_equals", status="PASS" if matches else "FAIL"))
        except (KeyError, TypeError, ValueError):
            facts.append(Result(registered_id="output_equals", status="FAIL", reason="No valid final JSON object"))
        attempt["fact_results"] = [fact.model_dump() for fact in facts]
        journal.save()
        observed = platform.get("/api/v1/agent-evaluations/profiles/runs/ask/" + attempt["platform_root"]["id"] + "/observations")
        assertions = observed.get("context_assertions", [])
        complete = observed.get("controlled_model", {}).get("complete") is True
        context = complete and any(row.get("human_turns") == 2 and row.get("has_project_context") is True
                                   and row.get("has_update_to_three") is True for row in assertions)
        facts.append(Result(registered_id="received_context", status="PASS" if context else "BLOCKED",
                            reason="Only trusted booleans and context digests are observed"))
        messages = evidence["messages"]
        no_tools = all(row.get("role") != "tool" and not row.get("content", {}).get("data", {}).get("tool_calls")
                       for row in messages["items"])
        evidence.update(tools_complete="messages" in evidence["observed"] and no_tools, tools=[])
    else:
        from .facts import verify
        facts = [verify(check, evidence, platform) for check in specimen.validators]
    attempt["fact_results"] = [fact.model_dump() for fact in facts]
    attempt["platform_status"] = evidence["run"]["status"]
    attempt["task_outcome"] = "FAIL" if any(row.status == "FAIL" for row in facts) else "NEEDS_REVIEW"
    journal.save()
    attempt["usage"] = adapters.known_usage(evidence, specimen)
    attempt["usage_completeness"] = "KNOWN"
    network.PHASE = "metric"
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
    for reference in [*attempt.get("prior_run_refs", {}).values(), root]:
        if adapters.read_run(platform, reference).get("status") not in {"SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED"}:
            return "NEEDS_REVIEW"
    if attempt.get("usage_completeness") != "KNOWN":
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


def validate_offline_report(data, specimen, dataset, *, context_path=None, target_path=None):
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
        if (attempt.get("case_id") != specimen.case_id or attempt.get("repeat_index") != context["repeat_index"]
                or attempt.get("owner_id") != data["owner_id"] or attempt.get("environment_id") != data["environment_id"]
                or digest(attempt.get("case_snapshot")) != expected["case_definition_digest"]):
            raise ValueError("Attempt identity or immutable snapshot differs")


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
    if args.action == "describe":
        print(json.dumps(describe(specimen, dataset)))
        return 0
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.action == "report":
        journal = Journal(output)
        try:
            validate_offline_report(journal.data, specimen, dataset, context_path=args.suite_context,
                                    target_path=args.target_manifest)
            return 0 if write_report(output, journal.data)["gate"] == "PASS" else 1
        except (ValueError, KeyError, TypeError):
            print("Offline fixed report identity is invalid")
            return 2
        finally:
            journal.close()
    if args.target_manifest is None:
        parser.error("--target-manifest is required")
    platform, journal = None, None
    try:
        target = Target.model_validate_json(args.target_manifest.read_text(encoding="utf-8"))
        for key in ("DEEPEVAL_HOME", "ENV_DIR_PATH", "DEEPEVAL_RESULTS_FOLDER", "DEEPEVAL_CACHE_FOLDER"):
            private = output / key.lower()
            private.mkdir(exist_ok=True)
            os.environ[key] = str(private)
        network.install_guard(urlsplit(target.api_base_url).port)
        network.PHASE = "execution"
        platform = Platform(target)
        platform.login()
        if args.action == "preflight":
            result = readiness(platform, specimen)
            atomic_json(output / "fixed-preflight.json", {"environment_status": "ATTESTED", **result,
                                                         **describe(specimen, dataset)})
            return 0 if result["status"] == "READY" else 1
        journal = Journal(output)
        context = suite_context(args.suite_context, specimen, dataset, target, journal.data.get("suite_context"))
        identity = {"environment_id": target.environment_id, "owner_id": target.owner_id,
                    "target_digest": digest(target.model_dump()), "dataset_digest": context["catalog_digest"],
                    "case_definition_digest": context["case_definition_digest"], "suite_context": context,
                    "expected_attempts": 1}
        for key, value in identity.items():
            if key in journal.data and journal.data[key] != value:
                raise ValueError("Existing journal identity changed")
        journal.data.update(identity)
        if not journal.data["attempts"]:
            journal.data["attempts"].append({"attempt_id": str(uuid4()), "case_id": specimen.case_id,
                                             "repeat_index": context["repeat_index"], "started": False,
                                             "environment_id": target.environment_id, "owner_id": target.owner_id,
                                             "case_snapshot": specimen.model_dump(),
                                             "fact_results": [], "metric_results": [], "cleanup_status": "NOT_CHECKED",
                                             "task_outcome": "NEEDS_REVIEW", "cost_completeness": "UNKNOWN", "cost": None})
        attempt = journal.data["attempts"][0]
        journal.save()
        try:
            if (output / "cancel.request").exists() and args.action in {"init", "run"}:
                raise adapters.Blocked("Cancelled before further dispatch")
            if args.action == "init":
                initialize(platform, journal, attempt, specimen)
            elif args.action == "run":
                status = readiness(platform, specimen)
                if status["status"] != "READY":
                    raise adapters.Blocked("Read-only case readiness blocked execution")
                execute(platform, journal, attempt, specimen, output)
                attempt["cleanup_status"] = cleanup(platform, journal, attempt)
                if attempt["cleanup_status"] != "CLEAN" and attempt["task_outcome"] == "PASS":
                    attempt["task_outcome"] = "NEEDS_REVIEW"
            elif args.action == "cleanup":
                attempt["cleanup_status"] = cleanup(platform, journal, attempt)
        except Exception as error:  # noqa: BLE001 -- Persist partial facts, unknown effects and safe error types at the CLI boundary.
            if any(row.get("required") and row.get("status") == "FAIL" for row in attempt["fact_results"]):
                attempt["task_outcome"] = "FAIL"
            else:
                attempt["task_outcome"] = "NEEDS_REVIEW"
            attempt.update(reason="Fixed profile blocked/failed: " + type(error).__name__, cleanup_status="NEEDS_REVIEW")
            journal.save()
            write_report(output, journal.data)
            return 1
        journal.save()
        network.PHASE = "export"
        journal.data["network_observations"] = list(network.EVENTS)
        journal.save()
        report = write_report(output, journal.data)
        if args.action == "init":
            return 0  # Initialization is not task success; the report gate remains NEEDS_REVIEW.
        return 0 if (attempt["cleanup_status"] == "CLEAN" if args.action == "cleanup" else report["gate"] == "PASS") else 1
    except ValueError:
        print("Fixed evaluation contract is invalid")
        return 2
    except Exception as error:  # noqa: BLE001 -- Do not print secret-bearing request/response bodies or exception locals.
        print("Fixed evaluation infrastructure error: " + type(error).__name__)
        return 3
    finally:
        if journal is not None:
            journal.close()
        if platform is not None:
            platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
