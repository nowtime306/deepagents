"""Version-scoped CLI dispatch. Unknown business effects are never converted to CLEAN."""

import importlib
from types import SimpleNamespace

from evaluation_runner.contracts import Result
from evaluation_runner.fixed_evidence import binding, load, store
from evaluation_runner.journal import UnknownSubmission, digest

from .registry import MODULES, selection


def module(specimen):
    expected, _, _ = selection(specimen.case_id, 2)
    if specimen.model_dump() != expected.model_dump():
        raise ValueError("Exact separately versioned fixed definition required")
    return importlib.import_module(MODULES[specimen.case_id])


def readiness(platform, specimen):
    handler = module(specimen)
    proof = platform.attest()
    needed = {"evaluation.read", "evaluation.manage", "evaluation.run"}
    needed |= (
        {"work.read", "work.manage", "work.execute", "agents.manage"}
        if specimen.mode == "work"
        else {"workflows.read", "workflows.manage", "workflows.execute"}
        if specimen.mode == "workflow"
        else {"chat.read", "chat.manage", "chat.run", "agents.manage"}
    )
    if specimen.case_id in {"AET-A05", "AET-W07"}:
        needed.add("tools.use")
    if not platform.get("/api/v1/auth/me").get("is_admin") or not needed.issubset(set(proof.get("permissions", []))):
        return {
            "case_id": specimen.case_id,
            "status": "BLOCKED",
            "missing": ["Dedicated administrator and underlying evaluation/business permissions required"],
            "scope": "versioned controlled engineering; no initialization or dispatch",
        }
    if specimen.case_id == "AET-F07":
        from .f07.contract import BASE, EXECUTION_LIMITS, LIMITS

        current = platform.get(BASE + "/observations")
        if (
            proof["fixture_profile"] != {"id": "handled-transform-error", "version": 2}
            or digest(proof["execution_limits"]) != digest(EXECUTION_LIMITS)
            or current["limits"] != LIMITS
            or current["reconstructed"] is not False
        ):
            raise ValueError("Original fixed F07 target proof required")
    else:
        handler.admission(platform)
    return {
        "case_id": specimen.case_id,
        "status": "READY",
        "missing": [],
        "scope": "versioned controlled engineering; not a business/quality verdict",
    }


def initialize(platform, journal, attempt, specimen):
    handler = module(specimen)
    proof = platform.attest()
    if attempt.get("profile_version", 2) != 2:
        raise ValueError("Cannot reuse original v1 journal")
    attempt["profile_version"] = 2
    attempt["sandbox_namespace"] = proof["sandbox_namespace"]
    journal.save()
    try:
        return handler.initialize(platform, journal, attempt, specimen)
    finally:
        roots(attempt, specimen)
        journal.save()


def roots(attempt, specimen):
    case = specimen.case_id
    if case == "AET-A05":
        values = [
            {"id": row["root_id"], "mode": "ask"}
            for row in attempt.get("fixture", {}).get("scenes", {}).values()
            if row.get("root_id")
        ]
    elif case == "AET-F07":
        values = [
            {"id": row["root_id"], "mode": "workflow"}
            for row in attempt.get("f07_runs", {}).values()
            if row["state"] == "OBSERVED"
        ]
        attempt["versioned_submission_unknown"] = any(
            row["state"] != "OBSERVED" for row in attempt.get("f07_runs", {}).values()
        )
    else:
        values = [{"id": attempt["root_id"], "mode": "work"}] if attempt.get("root_id") else []
    attempt["versioned_roots"] = values
    attempt["started"] = bool(values)


def settled(attempt):
    return all(
        row.get("status") == "CONFIRMED"
        for group in ("fixture_intents", "submission_intents")
        for row in attempt.get(group, {}).values()
    )


def envelope(journal, attempt, key, body):
    reference = store(
        journal.directory, "v2-cli", {"kind": "v2-cli", "binding": binding(journal.data, attempt), "body": body}
    )
    attempt.setdefault("evidence_refs", []).append(reference)
    attempt[key] = reference
    journal.save()


def retained(directory, data, attempt, key):
    value = load(directory, attempt[key], "v2-cli")
    if value["binding"] != binding(data, attempt) or attempt["evidence_refs"].count(attempt[key]) != 1:
        raise ValueError("Foreign or duplicate v2 CLI evidence")
    return value["body"]


def factual(directory, data, attempt, specimen):
    handler = module(specimen)
    journal = SimpleNamespace(directory=directory, data=data)
    platform = SimpleNamespace(target=SimpleNamespace(environment_id=data["environment_id"], owner_id=data["owner_id"]))
    case = specimen.case_id
    if case == "AET-A05":
        values = [handler.verified(platform, journal, attempt, scene) for scene in ("injection", "revocation")]
        usage = {key: sum(row["usage"][key] for row in values) for key in values[0]["usage"]}
        outcomes = [row["run"] for row in values]
    elif case == "AET-W07":
        before = load(directory, attempt["w07_before"], "w07-v2")
        after = load(directory, attempt["w07_after"], "w07-v2")
        for value in (before, after):
            if value["binding"] != binding(data, attempt):
                raise ValueError("Foreign unknown-write proof")
            handler.validate(value["body"], attempt)
        first, last = before["body"], after["body"]
        recovery = last["recovery"]
        facts = last["facts"]
        scope = {
            "environment_id": data["environment_id"],
            "owner_id": data["owner_id"],
            "root_id": attempt["root_id"],
            "namespace": attempt["sandbox_namespace"],
        }
        if (
            facts != first["facts"]
            or facts["receiver"]["scope"] != scope
            or facts["provider"]
            != {"recorded": True, "complete": True, "case_id": case, "provider_calls": 1, "tokens": 15}
            or recovery["phase"] != "OBSERVATION_RECOVERED"
            or recovery["before"] != facts
            or recovery["after"] != facts
            or recovery["before_worker_ids"] == recovery["after_worker_ids"]
        ):
            raise ValueError("Original unknown-write recovery facts differ")
        usage = {"calls": 1, "input_tokens": 10, "output_tokens": 5}
        outcomes = [{"status": "NEEDS_REVIEW", "operation_status": "UNKNOWN", "control": "NO_REPLAY"}]
    elif case == "AET-W08":
        from .w08.validation import validate

        value = load(directory, attempt["w08_observed"], "w08-v2")
        if value["binding"] != binding(data, attempt):
            raise ValueError("Foreign W08 observation")
        value = value["body"]
        scope = attempt["w08_scope"]
        proof = value["checkpoint_reconstruction"]
        if proof != {
            "checkpoint_id": value["checkpoint_id"],
            "checkpoint_ns": "",
            "effects": {"model": 0, "sdk": 0, "tool": 0},
            "reconstruction": "native-work-graph-aget_state-delta-v1",
            "transaction_read_only": True,
        } or any(type(count) is not int for count in proof["effects"].values()):
            raise ValueError("Original latest read-only Delta reconstruction proof required")
        validate(
            value,
            environment_id=scope["environment_id"],
            owner_id=scope["owner_id"],
            root_id=attempt["root_id"],
            namespace=scope["sandbox_namespace"],
        )
        if value["initialization_anchor"] != {
            "model_id": attempt["fixture"]["model_id"],
            "started_at": attempt["w08_started_at"],
            "deadline": attempt["w08_deadline"],
        }:
            raise ValueError("Original W08 initialization anchor changed")
        if not value["released"] or value["root_status"] != "COMPLETED":
            raise ValueError("Original W08 release missing")
        usage = {"calls": 6, "input_tokens": 60, "output_tokens": 30}
        outcomes = [{"status": "COMPLETED"}]
    else:
        usage, outcomes = f07_facts(directory, data, attempt, handler)
    return {
        "usage": usage,
        "business_outcomes": outcomes,
        "control_status": "PASS",
        "usage_complete": case != "AET-F07",
        "case_id": case,
    }


def f07_facts(directory, data, attempt, handler):
    for key in ("timer-init", "timer-main", "json-companion", "model-companion"):
        blob = load(directory, attempt["f07_refs"][key], "f07-v2")
        if blob["binding"] != binding(data, attempt):
            raise ValueError("Foreign native F07 evidence")
        body = blob["body"]
        root = body["run_id"]
        scene = body["scene"]
        row = attempt["fixture"][scene]
        base = "/api/v1/workflows/" + row["id"]
        responses = {
            base + "/runs": {"items": [body["native"]], "next_cursor": None},
            base + "/runs/" + root + "/events": {"items": body["events"]},
            "/api/v1/agent-evaluations/workflow-runs/" + root + "/usage": body["usage"],
        }

        def get(path, *, base=base, root=root, responses=responses, **kwargs):
            if path == base + "/runs" and kwargs != {"params": {"run_id": root}}:
                raise ValueError("Foreign F07 root lookup")
            return responses[path]

        rebuilt = handler.observe(SimpleNamespace(get=get), attempt, scene, root)
        if rebuilt != body:
            raise ValueError("Retained native F07 result changed")
    final = load(directory, attempt["f07_refs"]["final-observation"], "f07-v2")
    if final["binding"] != binding(data, attempt):
        raise ValueError("Foreign F07 final proof")
    value = final["body"]
    state = value["state"]
    if (
        len(state["roots"]) != 4
        or state["provider"]
        != {
            "calls": 1,
            "reserved_tokens": 15,
            "status": "UNKNOWN",
            "root": attempt["f07_runs"]["model-companion"]["root_id"],
        }
        or len(state["pure"]) != 2
        or any(not row["cancelled"] for row in state["pure"].values())
        or len(value["runs"]) != 4
        or len(value["usage"]) != 1
        or value["counts"]
        != {"work_tasks": 0, "sandboxes": 0, "operations": 0, "ask_runs": 0, "work_runs": 0, "workflow_runs": 4}
    ):
        raise ValueError("Original F07 aggregate effects differ")
    return {"calls": 1, "input_tokens": None, "output_tokens": None}, [
        {"status": "EXPECTED_UNKNOWN_PROPAGATED", "usage_status": "UNKNOWN"}
    ]


def execute(platform, journal, attempt, specimen, output):
    handler = module(specimen)
    if (output / "cancel.request").exists():
        raise ValueError("Cancelled before versioned dispatch")
    if not settled(attempt):
        raise UnknownSubmission("Prior ambiguous submission blocks all new v2 effects")
    try:
        if specimen.case_id == "AET-F07":
            handler.execute(platform, journal, attempt, specimen)
        else:
            handler.execute(
                platform, journal, attempt, specimen, cancelled=lambda: (output / "cancel.request").exists()
            )
    finally:
        roots(attempt, specimen)
        journal.save()
    snapshot = factual(journal.directory, journal.data, attempt, specimen)
    envelope(journal, attempt, "v2_facts", snapshot)
    attempt["usage"] = snapshot["usage"]
    attempt["usage_completeness"] = "KNOWN" if snapshot["usage_complete"] else "UNKNOWN"
    attempt["business_outcomes"] = snapshot["business_outcomes"]
    attempt["fact_results"] = [
        Result(
            registered_id=check.registered_id,
            status="PASS",
            required=check.required,
            reason="Versioned independent retained control facts verified",
        ).model_dump()
        for check in specimen.validators
    ]
    attempt["metric_results"] = []
    attempt["control_status"] = "PASS"
    attempt["task_outcome"] = "NEEDS_REVIEW" if specimen.case_id in {"AET-W07", "AET-F07"} else "PASS"
    journal.save()


def cleanup(platform, journal, attempt, specimen):
    handler = module(specimen)
    if not settled(attempt):
        return "NEEDS_REVIEW"
    status = handler.cleanup(platform, journal, attempt, specimen)
    case = specimen.case_id
    if case == "AET-A05" and status == "CLEAN":
        from .a05.protocol import BASE

        body = {
            scene: platform.get(BASE + "/receivers/" + scene + "/observations") for scene in ("injection", "revocation")
        }
    elif case == "AET-W07" and status == "CLEAN_RECEIVER_UNKNOWN_OPERATION_RETAINED":
        body = platform.get(handler.BASE + "/observations")
    elif case == "AET-W08" and status == "CLEAN_NATIVE_RELEASE_LATEST_EXPORT_VERIFIED":
        body = handler.observe(platform, attempt)
    elif case == "AET-F07" and status == "CLOSED_UNKNOWN_RETAINED":
        value = load(journal.directory, attempt["f07_refs"]["closed-observation"], "f07-v2")
        if value["binding"] != binding(journal.data, attempt):
            raise ValueError("Foreign closed F07 state")
        body = value["body"]
    else:
        return "NEEDS_REVIEW"
    envelope(journal, attempt, "v2_cleanup", {"status": status, "observation": body})
    cleanup_ready(journal.directory, journal.data, attempt, specimen)
    attempt["versioned_cleanup_status"] = status
    # Preserve unknown operation/cost review explicitly. Only known A05/W08 get CLEAN.
    return "CLEAN" if case in {"AET-A05", "AET-W08"} else status


def cleanup_ready(directory, data, attempt, specimen):
    value = retained(directory, data, attempt, "v2_cleanup")
    case = specimen.case_id
    body = value["observation"]
    handler = module(specimen)
    if case == "AET-A05":
        from .a05.protocol import validate_scene

        platform = SimpleNamespace(
            target=SimpleNamespace(owner_id=data["owner_id"], environment_id=data["environment_id"])
        )
        for scene in ("injection", "revocation"):
            validate_scene(body[scene], handler.expected(platform, attempt, scene))
            if body[scene]["closed"] is not True:
                raise ValueError("A05 receiver not closed")
        expected = "CLEAN"
    elif case == "AET-W07":
        original = load(directory, attempt["w07_after"], "w07-v2")["body"]
        if not body["closed"] or body["facts"] != original["facts"] or body["recovery"] != original["recovery"]:
            raise ValueError("W07 closed receiver facts differ; UNKNOWN must remain")
        expected = "CLEAN_RECEIVER_UNKNOWN_OPERATION_RETAINED"
    elif case == "AET-W08":
        original = load(directory, attempt["w08_observed"], "w08-v2")["body"]
        if body != original:
            raise ValueError("W08 released latest export changed")
        expected = "CLEAN_NATIVE_RELEASE_LATEST_EXPORT_VERIFIED"
    else:
        original = load(directory, attempt["f07_refs"]["final-observation"], "f07-v2")["body"]["state"]
        if (
            body["phase"] != "CLOSED"
            or body["provider"]["status"] != "UNKNOWN"
            or any(body[key] != original[key] for key in ("roots", "pure", "provider"))
        ):
            raise ValueError("F07 closed state changed original unknown provider facts")
        expected = "CLOSED_UNKNOWN_RETAINED"
    if value["status"] != expected:
        raise ValueError("Foreign versioned cleanup semantics")
    return True


def verified(directory, data, attempt, specimen):
    original = retained(directory, data, attempt, "v2_facts")
    actual = factual(directory, data, attempt, specimen)
    if actual != original:
        raise ValueError("Original versioned control facts changed")
    if "v2_cleanup" in attempt:
        cleanup_ready(directory, data, attempt, specimen)
    return actual


def evaluate(snapshot, specimen, attempt):
    facts = [
        Result(
            registered_id=check.registered_id,
            status="PASS",
            required=check.required,
            reason="Versioned independent retained control facts verified",
        )
        for check in specimen.validators
    ]
    return facts, {"business_outcomes": snapshot["business_outcomes"], "tools_complete": False}


def control_report(directory, data):
    result = {
        "control_gate": "NEEDS_REVIEW",
        "control_scope": "fixed case internal facts; parent process exit/retention remain separately enforced",
        "profile_version": 2,
        "rules_id": "controlled-a20-v2",
        "control_evidence": [],
    }
    attempts = data.get("attempts", [])
    context = data.get("suite_context", {})
    if (
        type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or type(data.get("expected_attempts")) is not int
        or not attempts
        or len(attempts) != data["expected_attempts"]
        or data.get("cancelled")
        or context.get("profile_version") != 2
        or context.get("rules_id") != "controlled-a20-v2"
    ):
        return result
    try:
        for attempt in attempts:
            specimen, _dataset, description = selection(attempt["case_id"], 2)
            if (
                attempt.get("profile_version") != 2
                or not attempt.get("started")
                or not settled(attempt)
                or attempt.get("versioned_submission_unknown")
                or attempt["case_snapshot"] != specimen.model_dump()
                or context["case_id"] != specimen.case_id
                or context["catalog_digest"] != description["catalog_digest"]
                or data["dataset_digest"] != description["catalog_digest"]
                or context["case_definition_digest"] != description["case_definition_digest"]
            ):
                raise ValueError("Incomplete or foreign v2 control identity")
            actual = verified(directory, data, attempt, specimen)
            facts, _ = evaluate(actual, specimen, attempt)
            if (
                [row.model_dump() for row in facts] != attempt["fact_results"]
                or actual["usage"] != attempt["usage"]
                or attempt.get("metric_results") != []
                or not cleanup_ready(directory, data, attempt, specimen)
            ):
                raise ValueError("Stored control result differs from retained facts")
            expected = {
                "AET-A05": "CLEAN",
                "AET-W08": "CLEAN",
                "AET-W07": "CLEAN_RECEIVER_UNKNOWN_OPERATION_RETAINED",
                "AET-F07": "CLOSED_UNKNOWN_RETAINED",
            }[specimen.case_id]
            if attempt.get("cleanup_status") != expected or (
                specimen.case_id in {"AET-W07", "AET-F07"} and attempt["task_outcome"] != "NEEDS_REVIEW"
            ):
                raise ValueError("Unknown business/cleanup status was reinterpreted")
            result["control_evidence"].append(
                {
                    "case_id": specimen.case_id,
                    "case_definition_digest": description["case_definition_digest"],
                    "catalog_digest": description["catalog_digest"],
                    "facts": attempt["v2_facts"],
                    "cleanup": attempt["v2_cleanup"],
                }
            )
    except (KeyError, ValueError, TypeError, OSError):
        return result
    result["control_gate"] = "PASS"
    return result
