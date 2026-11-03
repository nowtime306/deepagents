"""Mode-specific formal API execution, observation, and exact-resource cleanup."""

import time
from uuid import uuid4

from .contracts import Case

TERMINAL = {"SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}


class Blocked(RuntimeError):
    pass


def enforce_budget(case: Case, attestation: dict) -> None:
    """Do not submit a budget that the current formal endpoint cannot enforce."""
    if case.mode == "work":
        if case.budgets.max_model_calls < 5 or case.budgets.max_tokens < 1000:
            raise Blocked("Requested budget is below the Work endpoint minimum; it will not be increased")
        return
    proof = attestation.get("execution_limits", {})
    bound = proof.get("profiles", {}).get(case.case_id, {})
    if (proof.get("schema_version") != 1 or proof.get("enforcement") != "controlled-model-factory-v1"
            or proof.get("scope") != "resource-run-root" or bound.get("mode") != case.mode):
        raise Blocked("The current Ask/Workflow endpoint has no attested per-run budget enforcement")
    calls, tokens = bound.get("max_model_calls"), bound.get("max_tokens")
    if type(calls) is not int or type(tokens) is not int or not 0 <= calls <= 100 or not 0 <= tokens <= 1000000:
        raise Blocked("Invalid server-side execution limit proof")
    roots = 1 + len(case.fixture_parameters.get("prior_messages", [])) if case.mode == "ask" else 1
    if case.budgets.max_model_calls < calls * roots or case.budgets.max_tokens < tokens * roots:
        raise Blocked("Requested budget is smaller than the server-enforced root limits")


def known_usage(evidence: dict, case: Case) -> dict:
    total = evidence.get("usage", {}).get("total", {})
    counts = ("calls", "reported_calls", "unknown_calls", "pending_calls")
    known = ("usage" in evidence.get("observed", [])
             and all(type(total.get(key)) is int and total[key] >= 0 for key in counts)
             and total.get("unknown_calls") == 0
             and total.get("pending_calls") == 0 and type(total.get("calls")) is int
             and total.get("calls") >= 0 and total.get("calls") == total.get("reported_calls"))
    if known and total["calls"]:
        known = all(type(total.get(key)) is int and total[key] >= 0 for key in ("input_tokens", "output_tokens"))
    if not known:
        raise Blocked("Usage is missing or unknown; no subsequent task may be dispatched")
    tokens = (total.get("input_tokens") or 0) + (total.get("output_tokens") or 0)
    if total["calls"] > case.budgets.max_model_calls or tokens > case.budgets.max_tokens:
        raise Blocked("Observed use exceeds the authorized budget; dispatch stopped")
    return total


def initialize(platform, journal, attempt: dict, case: Case) -> dict:
    """Create fresh formal resources; unsupported profiles stop before any write."""
    enforce_budget(case, platform.attest())
    profile = case.fixture_profile.id
    if profile not in {"ask-plain", "work-coding", "workflow-published"} or case.fixture_profile.version != 1:
        raise Blocked("Reviewed fixture profile is not implemented: " + profile)
    if case.dependencies:
        raise Blocked("Missing trusted fixture/observer capabilities: " + ", ".join(case.dependencies))
    model = platform.target.resource_bindings.get(case.case_id)
    if not model:
        raise Blocked("No isolated controlled model binding for " + case.case_id)
    post = lambda path, body, key: journal.post(platform, attempt, path, body, key)
    if case.mode == "ask" and profile == "ask-plain":
        row = post("/api/v1/conversations", {"title": "Evaluation " + case.case_id}, "create-fixture")
        resource = {"kind": "conversation", "id": row["id"], "path": "/api/v1/conversations/" + row["id"]}
    elif case.mode == "work" and profile == "work-coding":
        row = post("/api/v1/work-tasks", {"title": "Evaluation " + case.case_id, "goal": case.input}, "create-fixture")
        resource = {"kind": "work-task", "id": row["id"], "path": "/api/v1/work-tasks/" + row["id"]}
    elif case.mode == "workflow" and profile == "workflow-published":
        # Provisioned published versions are immutable business inputs. We never edit/delete them.
        identifier = case.fixture_parameters.get("workflow_binding")
        workflow = platform.target.resource_bindings.get(identifier or "")
        if not workflow or case.business_version is None:
            raise Blocked("A fixed published workflow fixture binding/version is required")
        return {"id": workflow, "model_id": model, "owned": False}
    else:
        raise Blocked("Fixture profile and mode do not match")
    if resource not in attempt.setdefault("created_resources", []):
        attempt["created_resources"].append(resource)
    journal.save()
    return {"id": row["id"], "model_id": model, "owned": True}


def submit(platform, journal, attempt, case: Case, fixture: dict) -> dict:
    enforce_budget(case, platform.attest())
    base = "/api/v1/"
    if case.mode == "ask":
        path = base + "conversations/" + fixture["id"] + "/runs"
        prior_messages = case.fixture_parameters.get("prior_messages", [])
        if not isinstance(prior_messages, list) or not all(isinstance(text, str) for text in prior_messages):
            raise Blocked("Invalid reviewed multi-turn fixture")
        for index, text in enumerate(prior_messages):
            row = journal.post(platform, attempt, path, {"message": text, "model_id": fixture["model_id"]},
                               "submit-prior-" + str(index))
            root = {"id": row["run_id"], "mode": "ask", "fixture_id": fixture["id"],
                    "path": base + "runs/" + row["run_id"]}
            attempt.update(platform_root=root, started=True)
            journal.save()
            observed = observe(platform, journal, attempt, case)
            known_usage(observed, case)
            if observed["run"].get("status") != "SUCCEEDED":
                raise Blocked("An earlier conversation turn did not succeed")
            attempt.setdefault("prior_run_refs", {})[str(index)] = root
        payload = {"message": case.input, "model_id": fixture["model_id"]}
    elif case.mode == "work":
        path = base + "work-tasks/" + fixture["id"] + "/runs"
        payload = {"model_id": fixture["model_id"], "revision": 1, "environment": "coding",
                   "token_limit": case.budgets.max_tokens, "step_limit": case.budgets.max_model_calls}
    else:
        path = base + f"workflows/{fixture['id']}/versions/{case.business_version}/runs"
        payload = {"text": case.input, "run_id": attempt.setdefault("client_run_id", str(uuid4()))}
    journal.save()
    row = journal.post(platform, attempt, path, payload, "submit-run")
    identifier = row.get("run_id") or row.get("id")
    if not identifier:
        raise Blocked("Submission did not return an observable run identity")
    scope = "runs" if case.mode == "ask" else "work-runs"
    reference = {"id": identifier, "mode": case.mode, "fixture_id": fixture["id"],
                 "path": base + scope + "/" + identifier}
    if case.mode == "workflow":
        reference["path"] = base + f"workflows/{fixture['id']}/runs"
    attempt["platform_root"] = reference
    attempt["started"] = True
    attempt["submission_complete"] = True
    journal.save()
    return reference


def read_run(platform, reference: dict) -> dict:
    if reference["mode"] != "workflow":
        return platform.get(reference["path"])
    rows = platform.get(reference["path"], params={"run_id": reference["id"]}).get("items", [])
    return next((row for row in rows if str(row["id"]) == reference["id"]), {"status": "PENDING"})


def observe(platform, journal, attempt, case: Case, *, cancelled=lambda: False, poll_seconds=0.25) -> dict:
    deadline = time.monotonic() + case.budgets.timeout_seconds
    reference = attempt["platform_root"]
    while True:
        # Recheck live permission/target registration before every observation or effect.
        platform.attest()
        row = read_run(platform, reference)
        if row.get("status") in TERMINAL:
            evidence = {"run": row, "root": reference, "output": "", "observed": ["run"]}
            if case.mode == "work":
                for name in ("artifacts", "operations"):
                    response = platform.get(reference["path"] + "/" + name)
                    if isinstance(response.get("items"), list) and not response.get("next_after") and not response.get("next_before"):
                        evidence[name] = response["items"]
                        evidence["observed"].append(name)
                evidence["usage"] = platform.get(reference["path"] + "/usage")
                evidence["observed"].append("usage")
            elif case.mode == "ask":
                evidence["messages"] = platform.get("/api/v1/conversations/" + reference["fixture_id"] + "/messages")
                if (isinstance(evidence["messages"].get("items"), list)
                        and "next_before" in evidence["messages"] and evidence["messages"]["next_before"] is None):
                    evidence["observed"].append("messages")
                messages = evidence["messages"].get("items", [])
                answers = [message for message in messages if message.get("role") == "assistant"
                           and str(message.get("run_id")) == reference["id"]]
                if answers:
                    content = answers[-1].get("content", {})
                    text = content.get("data", {}).get("content") if isinstance(content, dict) else None
                    if isinstance(text, str):
                        evidence["run"]["output"] = text
                evidence["usage"] = platform.get("/api/v1/conversations/" + reference["fixture_id"] + "/usage")
                evidence["observed"].append("usage")
            else:
                evidence["usage"] = platform.get("/api/v1/agent-evaluations/workflow-runs/" + reference["id"] + "/usage")
                evidence["observed"].append("usage")
            return evidence
        if row.get("status") == "WAITING_APPROVAL":
            if case.approval_policy != "reject":
                raise Blocked("Approval requires a reviewed action-specific fixture policy")
            for approval in platform.get(reference["path"] + "/approvals").get("items", []):
                if not approval.get("decision"):
                    journal.post(platform, attempt, "/api/v1/work-approvals/" + approval["id"] + "/decisions",
                                 {"digest": approval["digest"], "decision": "reject"}, "reject-" + approval["id"])
        if cancelled() or time.monotonic() >= deadline:
            if case.mode != "workflow":
                journal.post(platform, attempt, reference["path"] + "/cancel", {}, "cancel-run")
            raise Blocked("Cancelled or deadline reached; cancellation requested, terminal cleanup not yet confirmed")
        time.sleep(poll_seconds)


def cleanup(platform, journal, attempt: dict) -> str:
    """Delete only registered IDs after terminal-state confirmation; preserve unknown writes."""
    if any(row["status"] != "CONFIRMED" for row in attempt.get("submission_intents", {}).values()):
        return "NEEDS_REVIEW"
    reference = attempt.get("platform_root")
    if reference and read_run(platform, reference).get("status") not in {"SUCCEEDED", "COMPLETED", "CANCELLED", "FAILED"}:
        return "NEEDS_REVIEW"
    if reference and reference["mode"] == "work":
        receipts = platform.get(reference["path"] + "/operations").get("items", [])
        if any(row.get("status") in {"STARTED", "UNKNOWN"} for row in receipts):
            return "NEEDS_REVIEW"
    for resource in reversed(attempt.get("created_resources", [])):
        platform.attest()
        response = platform.http.delete(resource["path"])
        if response.status_code not in {200, 204, 404}:
            return "NEEDS_REVIEW"
        resource["cleaned"] = True
        journal.save()
    return "CLEAN"
