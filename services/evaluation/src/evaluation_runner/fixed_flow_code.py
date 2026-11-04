"""Three formal F05 Work roots with fixed approvals and independently retained facts."""

import hashlib
import os
import time
from contextlib import contextmanager
from uuid import UUID

import httpx

from . import adapters
from . import fixed_case_deadline as case_deadline
from . import fixed_code_contract as contract
from . import fixed_workflow_observation as workflow
from .contracts import Result
from .fixed_approval_observation import download_counter
from .fixed_evidence import binding, load, store
from .fixed_fixtures import FixedFixtures
from .fixed_work_observation import complete_events
from .journal import UnknownSubmission, digest


def dispatchable(attempt):
    if attempt.get("flow_code_evidence_failed"):
        raise adapters.Blocked("Failed F05 persistence prevents new effects")
    intents = [*attempt.get("fixture_intents", {}).values(), *attempt.get("submission_intents", {}).values()]
    if any(row.get("status") != "CONFIRMED" for row in intents):
        raise UnknownSubmission("An original F05 effect is unsettled; no new submission or decision")


def deadline_value(directory, data, attempt):
    value = read(directory, data, attempt, "deadline")
    expected = {key: data[key] for key in ("environment_id", "owner_id", "target_digest", "suite_context")}
    expected["namespace"] = str(UUID(attempt.get("sandbox_namespace", value["scope"]["namespace"])))
    return case_deadline.validate(value, expected)


def check_deadline(journal, attempt):
    value = deadline_value(journal.directory, journal.data, attempt)
    now = time.time_ns() // 1_000_000
    if not value["started_at_ms"] <= now < value["expires_at_ms"]:
        raise adapters.Blocked("The shared F05 initialization/run deadline elapsed or the clock moved backwards")
    return (value["expires_at_ms"] - now) / 1000


class DeadlineStream(httpx.SyncByteStream):
    """Check a consumed body between reads; blocking transport reads are not forcibly interrupted."""

    def __init__(self, source, check):
        self.source, self.check = source, check

    def __iter__(self):
        iterator = iter(self.source)
        try:
            while True:
                self.check()
                try:
                    chunk = next(iterator)
                except StopIteration:
                    self.check()
                    return
                self.check()
                yield chunk
        finally:
            self.close()

    def close(self):
        self.source.close()


@contextmanager
def deadline_scope(platform, journal, attempt, specimen, *, initialize=False):
    """Persist once before fixture effects; enforce the same deadline at every actual HTTP dispatch."""
    dispatchable(attempt)
    if type(specimen.budgets.timeout_seconds) is not int or specimen.budgets.timeout_seconds != 300:
        raise adapters.Blocked("Only the fixed 300-second F05 case budget is supported")
    parent = case_deadline.load(journal.directory.parent, os.environ.get(case_deadline.ENV))
    expected = {key: journal.data[key] for key in ("environment_id", "owner_id", "target_digest", "suite_context")}
    expected["namespace"] = str(UUID(parent["scope"]["namespace"]))
    case_deadline.validate(parent, expected)
    platform.expected_sandbox_namespace = expected["namespace"]
    if "deadline" not in attempt.get("flow_code_refs", {}):
        if not initialize or any(attempt.get(key) for key in ("fixture", "fixture_intents", "submission_intents", "created_resources", "evidence_refs")):
            raise adapters.Blocked("A missing original F05 deadline cannot be reset after effects")
        retain(journal, attempt, "deadline", parent)
    if digest(deadline_value(journal.directory, journal.data, attempt)) != digest(parent):
        raise adapters.Blocked("The parent and original sealed F05 deadline differ")
    local_expiry = time.monotonic() + check_deadline(journal, attempt)

    def before_request(request):
        remaining = min(check_deadline(journal, attempt), local_expiry - time.monotonic())
        if remaining <= 0:
            raise adapters.Blocked("The shared F05 deadline elapsed before HTTP dispatch")
        request.extensions["timeout"] = {key: remaining if value is None else min(value, remaining)
                                         for key, value in request.extensions.get("timeout", {}).items()}

    def confirm():
        check_deadline(journal, attempt)
        if time.monotonic() >= local_expiry:
            raise adapters.Blocked("The shared F05 deadline elapsed while awaiting an HTTP response")

    def after_response(response):
        confirm()
        if not response.is_stream_consumed:
            response.stream = DeadlineStream(response.stream, confirm)

    requests, responses = platform.http.event_hooks["request"], platform.http.event_hooks["response"]
    requests.append(before_request)
    responses.append(after_response)
    previous_guard = getattr(platform, "effect_confirmation_guard", None)
    platform.effect_confirmation_guard = confirm
    try:
        capabilities = platform.get("/api/v1/agent-evaluations/profiles/capabilities")
        guard = capabilities.get("f05_deadline_guard") or {}
        identity = {key: parent["scope"][key] for key in ("environment_id", "owner_id", "namespace", "suite_context")}
        identity["candidate_commit"] = parent["scope"]["suite_context"]["candidate_commit"]
        if (guard.get("dispatch_allowed") is not True or guard.get("phase") != "ACTIVE"
                or guard.get("record_sha256") != os.environ.get(case_deadline.ENV)
                or type(guard.get("expires_at_ms")) is not int or guard["expires_at_ms"] != parent["expires_at_ms"]
                or guard.get("bound_identity_digest") != digest(identity)):
            raise adapters.Blocked("The actual target did not bind the original F05 deadline")
        yield
        check_deadline(journal, attempt)
    finally:
        requests.remove(before_request)
        responses.remove(after_response)
        platform.effect_confirmation_guard = previous_guard


def readiness(platform, specimen, capabilities=None, *, require_active=False):
    proof = platform.attest()
    capabilities = capabilities or platform.get("/api/v1/agent-evaluations/profiles/capabilities")
    limit = capabilities.get("sandbox_limits", {})
    expected = {"enabled": True, "max_created": 10, "max_concurrent": 1, "material": "fixed-code-v2", "counterfactual": "reject-first-approval"}
    guard = capabilities.get("f05_deadline_guard") or {}
    if (guard.get("contract") != "f05-deadline-guard-v1" or guard.get("required") is not True or guard.get("installed") is not True
            or digest(guard.get("boundaries")) != digest(dict.fromkeys(("provider", "tool", "sandbox", "submission"), True))
            or guard.get("phase") not in {"AWAITING_RECORD", "ACTIVE"}
            or require_active and (guard.get("phase") != "ACTIVE" or guard.get("dispatch_allowed") is not True)):
        raise adapters.Blocked("The F05 target expiry fence is not installed or active")
    if (specimen.case_id != contract.CASE or specimen.mode != "workflow" or specimen.fixture_profile.version != 2
            or specimen.metrics or specimen.input != contract.INPUT or specimen.approval_policy != "none"
            or not {"work.read", "work.manage", "work.execute"}.issubset(proof["permissions"])
            or "fixed-code-three-roots-v2" not in capabilities.get("observers", [])
            or not capabilities.get("work_manager", {}).get("ready")
            or limit.get("enforcement") != "controlled-sandbox-reserve-v1" or limit.get("scope") != "test-environment"
            or type(limit.get("max_created")) is not int or limit["max_created"] != 2
            or digest(limit.get("fixed_cases", {}).get(contract.CASE)) != digest(expected)
            or limit.get("storage_healthy") is not True or type(limit.get("attempts")) is not int
            or limit["attempts"] > 10
            or digest(proof.get("execution_limits", {}).get("profiles", {}).get(contract.CASE)) != digest({
                "mode": "workflow", "max_model_calls": 0, "max_tokens": 0})):
        raise adapters.Blocked("F05 fixed three-root/zero-model/ten-creation capabilities are incomplete")


def retain(journal, attempt, slot, body):
    if attempt.get("flow_code_evidence_failed"):
        raise adapters.Blocked("Failed F05 persistence prevents new effects")
    try:
        value = {"schema_version": 2, "kind": "flow-code", "binding": binding(journal.data, attempt), "slot": slot, "body": body}
        reference = store(journal.directory, "flow-code", value)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt.setdefault("flow_code_refs", {})[slot] = reference
        journal.save()
    except BaseException:
        attempt["flow_code_evidence_failed"] = True
        raise
    return body


def read(directory, data, attempt, slot):
    if attempt.get("flow_code_evidence_failed"):
        raise adapters.Blocked("Failed F05 persistence cannot authorize grading or cleanup")
    reference = attempt["flow_code_refs"][slot]
    value = load(directory, reference, "flow-code")
    if (set(value) != {"schema_version", "kind", "binding", "slot", "body"} or type(value["schema_version"]) is not int
            or value["schema_version"] != 2 or value["kind"] != "flow-code" or value["slot"] != slot
            or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("F05 retained evidence scope changed")
    return value["body"]


def bounded(response, maximum):
    if (not isinstance(response.get("items"), list) or len(response["items"]) > maximum
            or response.get("next_after") or response.get("next_before") or response.get("next_cursor")):
        raise adapters.Blocked("Complete bounded formal F05 observations are required")
    return response["items"]


def collect(platform, root, fixture):
    proof, path = platform.attest(), "/api/v1/work-runs/" + root["run_id"]
    run = platform.get(path)
    if (run["id"] != root["run_id"] or run["task_id"] != root["task_id"] or run["owner_id"] != platform.target.owner_id
            or run["model_id"] != fixture["model_id"] or run["environment"] != "coding"):
        raise ValueError("The exact owned fixed Work root differs")
    records = bounded(platform.get(path + "/artifacts"), 4)
    operations = bounded(platform.get(path + "/operations"), 3)
    approvals = bounded(platform.get(path + "/approvals"), 3)
    projected = []
    for row in approvals:
        actions = row["payload"].get("action_requests")
        if not isinstance(actions, list) or len(actions) != 1:
            raise adapters.Blocked("Exactly one fixed action per approval is required")
        action = actions[0]
        projected.append({"id": row["id"], "digest": row["digest"], "payload_digest": digest(row["payload"]),
                          "name": action["name"], "args_digest": digest(action["args"]), "decision": row["decision"]})
    rows = workflow.complete_items(platform.get(f"/api/v1/workflows/{fixture['id']}/runs", params={"run_id": root["workflow_run_id"]}))
    if len(rows) != 1 or rows[0]["id"] != root["workflow_run_id"]:
        raise ValueError("Exact fixed workflow root is missing")
    row = rows[0]
    snap = row["snapshot"]
    if (row["workflow_id"] != fixture["id"] or digest(snap["definition"]) != digest(contract.definition())
            or type(snap["revision"]) is not int or snap["revision"] != 1 or snap["model_id"] != fixture["model_id"]
            or snap.get("published_version") != root["version"] or type(snap.get("published_version")) is not type(root["version"])):
        raise ValueError("The immutable workflow business selection changed")
    events = bounded(platform.get(f"/api/v1/workflows/{fixture['id']}/runs/{root['workflow_run_id']}/events"), 16)
    observer = platform.get(f"/api/v1/agent-evaluations/profiles/code-workflows/{root['run_id']}/observations")
    return {"run_id": root["run_id"], "task_id": root["task_id"], "workflow_run_id": root["workflow_run_id"],
            "workflow_id": fixture["id"], "version": root["version"], "status": run["status"], "workflow_status": row["status"],
            "input_digest": hashlib.sha256(row["input"].encode()).hexdigest(),
            "output_digest": hashlib.sha256(row["output"].encode()).hexdigest() if isinstance(row["output"], str) else None,
            "usage": platform.get(path + "/usage")["total"], "events": complete_events(platform, path),
            "node_events": [{"event_id": event["event_id"], "node_id": event["node_id"], "event": event["event"],
                             "kind": event["data"].get("kind"), "error_code": event["data"].get("error_code")} for event in events],
            "approvals": projected, "operations": [{"id": op["id"], "name": op["name"], "status": op["status"],
                                                     "exit_code": op["exit_code"], "args_digest": digest(op["args"])} for op in operations],
            "artifacts": [{"id": item["id"], "path_digest": hashlib.sha256(item["path"].encode()).hexdigest(),
                           "digest": item["digest"], "size": item["size"]} for item in records],
            "counter": download_counter(platform, records), "observer": observer,
            "identity": {key: proof[key] for key in ("environment_id", "owner_id", "sandbox_namespace")}}


def validate(value, root, fixture, data, attempt):
    if (set(value) != {"run_id", "task_id", "workflow_run_id", "workflow_id", "version", "status", "workflow_status", "input_digest",
                      "output_digest", "usage", "events", "node_events", "approvals", "operations", "artifacts", "counter", "observer", "identity"}
            or value["identity"] != {"environment_id": data["environment_id"], "owner_id": data["owner_id"], "sandbox_namespace": attempt["sandbox_namespace"]}
            or any(value[key] != root[key] for key in ("run_id", "task_id", "workflow_run_id", "version"))
            or value["workflow_id"] != fixture["id"] or value["input_digest"] != hashlib.sha256(contract.INPUT.encode()).hexdigest()
            or set(value["usage"]) != workflow.TOTAL or any(type(n) is not int or n != 0 for n in value["usage"].values())):
        raise adapters.Blocked("F05 identity or zero-call native usage is not complete")
    for key in ("run_id", "task_id", "workflow_run_id"):
        UUID(value[key])
    observer = value["observer"]
    expected = {"schema_version": 2, "case_id": contract.CASE, "environment_id": data["environment_id"], "owner_id": data["owner_id"],
                "namespace": attempt["sandbox_namespace"], "run_id": root["run_id"], "task_id": root["task_id"],
                "workflow_id": fixture["id"], "workflow_run_id": root["workflow_run_id"], "workflow_status": value["workflow_status"], "status": value["status"]}
    if (any(type(observer.get(key)) is not type(expected_value) or observer.get(key) != expected_value for key, expected_value in expected.items())
            or observer.get("lease_free") is not True or observer.get("cancel_requested") is not False
            or observer.get("sessions_complete") is not True or not observer.get("snapshot_id")
            or digest(observer.get("provider")) != digest({"observed": True, "complete": True, "calls": 0, "tokens": 0, "pending": 0, "unknown_roots": 0})
            or not observer.get("sandbox", {}).get("storage_healthy") or observer["sandbox"].get("inventory") != {"complete": True, "count": 0}
            or type(observer["sandbox"].get("attempts")) is not int or not 0 <= observer["sandbox"]["attempts"] <= 10):
        raise adapters.Blocked("F05 owned resource/provider/namespace evidence is incomplete")
    UUID(observer["snapshot_id"])
    if (not isinstance(observer.get("sessions"), list) or len(observer["sessions"]) > 10
            or any(item["state"] != "RELEASED" or not item["sandbox_id"] for item in observer["sessions"])
            or len({item["id"] for item in observer["sessions"]}) != len(observer["sessions"])
            or len({item["sandbox_id"] for item in observer["sessions"]}) != len(observer["sessions"])):
        raise adapters.Blocked("F05 sandbox sessions are not exactly released")
    for name in ("events", "node_events"):
        previous = 0
        for event in value[name]:
            if type(event["event_id"]) is not int or event["event_id"] <= previous:
                raise ValueError("F05 events are unordered")
            previous = event["event_id"]
    for session in observer["sessions"]:
        UUID(session["id"])
        UUID(session["run_id"])
    for row in value["approvals"]:
        UUID(row["id"])
        if row["digest"] != row["payload_digest"]:
            raise ValueError("Approval reported digest does not match its original payload")
    for op in value["operations"]:
        UUID(op["id"])
        if op["status"] not in {"SUCCEEDED", "FAILED"} or type(op["exit_code"]) is not int:
            raise adapters.Blocked("An approval/code operation is unsettled")


def action(row, node):
    return (row["name"] == ("workflow_code" if node == "code" else "workflow_approval")
            and row["args_digest"] == digest(contract.arguments(node)) and row["digest"] == row["payload_digest"])


def waiting(value, index):
    nodes = ("before", "code", "after")
    if (value["status"] != "WAITING_APPROVAL" or value["workflow_status"] != "RUNNING"
            or len(value["approvals"]) != index + 1 or len(value["operations"]) != index
            or sum(row["run_id"] == value["run_id"] for row in value["observer"]["sessions"]) != index + 1
            or any(not action(row, nodes[position]) or row["decision"] != (None if position == index else "approve")
                   for position, row in enumerate(value["approvals"]))):
        return False
    return (not value["counter"]["downloaded"] and not value["artifacts"] if index < 2 else
            value["counter"] == {"downloaded": True, "size": 1, "sha256": hashlib.sha256(b"1").hexdigest(), "declared": "1"})


def start(fixtures, fixture, slot, version):
    check_deadline(fixtures.journal, fixtures.attempt)
    result = fixtures.mutate("f05-start-" + slot, "POST", f"/api/v1/workflows/{fixture['id']}/work-runs",
                             body={"text": contract.INPUT, **({"revision": 1} if version is None else {"version": version})}, accepted=(202,))
    root = {key: str(UUID(result[key])) for key in ("run_id", "task_id", "workflow_run_id")}
    return {**root, "version": version}


def decide(platform, journal, attempt, root, slot, index, reject):
    dispatchable(attempt)
    check_deadline(journal, attempt)
    fixture = attempt["fixture"]
    key = f"{slot}-waiting-{index}"
    if key not in attempt.get("flow_code_refs", {}):
        body = collect(platform, root, fixture)
        validate(body, root, fixture, journal.data, attempt)
        pending = [row for row in body["approvals"] if row["decision"] is None]
        if len(pending) != 1 or not waiting(body, index):
            raise adapters.Blocked("Fixed pre-decision target or zero-effect checkpoint differs")
        retain(journal, attempt, key, body)
    before = read(journal.directory, journal.data, attempt, key)
    validate(before, root, fixture, journal.data, attempt)
    if not waiting(before, index):
        raise adapters.Blocked("Retained approval checkpoint no longer proves the fixed pending target")
    for previous in before["approvals"][:index]:
        position = before["approvals"].index(previous)
        intent = attempt.get("submission_intents", {}).get(f"f05-{slot}-decision-{position}", {})
        if (intent.get("status") != "CONFIRMED" or intent.get("path") != f"/api/v1/work-approvals/{previous['id']}/decisions"
                or intent.get("payload_digest") != digest({"digest": previous["digest"], "decision": "approve"})):
            raise UnknownSubmission("An earlier approval has no confirmed local intent; do not advance")
    row = next(item for item in before["approvals"] if item["decision"] is None)
    journal.post(platform, attempt, f"/api/v1/work-approvals/{row['id']}/decisions",
                 {"digest": row["digest"], "decision": "reject" if reject else "approve"}, f"f05-{slot}-decision-{index}")


def drive(platform, journal, attempt, slot, root, *, reject=False, cancelled=lambda: False):
    dispatchable(attempt)
    check_deadline(journal, attempt)
    key = slot + "-final"
    if key in attempt.get("flow_code_refs", {}):
        value = read(journal.directory, journal.data, attempt, key)
        validate(value, root, attempt["fixture"], journal.data, attempt)
        validate_decisions(attempt, slot, value, reject=reject)
        return value
    while True:
        check_deadline(journal, attempt)
        platform.attest()
        run = platform.get("/api/v1/work-runs/" + root["run_id"])
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
            break
        if run["status"] == "WAITING_APPROVAL":
            rows = bounded(platform.get("/api/v1/work-runs/" + root["run_id"] + "/approvals"), 3)
            pending = [item for item in rows if item["decision"] is None]
            if len(pending) != 1:
                raise adapters.Blocked("A single pending fixed approval is required")
            index = len(rows) - 1
            if index not in range(1 if reject else 3):
                raise adapters.Blocked("Unexpected approval position")
            decide(platform, journal, attempt, root, slot, index, reject)
        if cancelled():
            raise adapters.Blocked("F05 stopped waiting; no cancellation or decision is guessed")
        time.sleep(0.1)
    body = collect(platform, root, attempt["fixture"])
    validate(body, root, attempt["fixture"], journal.data, attempt)
    return retain(journal, attempt, key, body)


def initialize(fixtures, model, specimen):
    attempt, journal, platform = fixtures.attempt, fixtures.journal, fixtures.platform
    dispatchable(attempt)
    check_deadline(journal, attempt)
    identifier = fixtures.create("f05-workflow", "/api/v1/workflows", {
        "name": "AE F05 " + fixtures.prefix, "model_id": model, "definition": contract.definition()})
    fixture = {"id": identifier, "model_id": model}
    attempt["fixture"] = fixture
    attempt.setdefault("flow_code_roots", {})
    journal.save()
    if "draft" not in attempt["flow_code_roots"]:
        attempt["flow_code_roots"]["draft"] = start(fixtures, fixture, "draft", None)
        journal.save()
    root = attempt["flow_code_roots"]["draft"]
    attempt.setdefault("prior_run_refs", {})["draft"] = root_ref(root, identifier)
    journal.save()
    result = drive(platform, journal, attempt, "draft", root)
    if not passed(result, reject=False):
        raise adapters.Blocked("Draft must complete real approvals/code/counter before publishing")
    version = fixtures.mutate("f05-publish", "POST", f"/api/v1/workflows/{identifier}/publish", body={"revision": 1})
    if type(version.get("version")) is not int or version["version"] != 1:
        raise ValueError("Only fixed published version 1 is supported")
    released = platform.get(f"/api/v1/workflows/{identifier}/versions/1")
    if (released["definition"] != contract.definition() or released["model_id"] != model or released["active"] is not True
            or released["draft_revision"] != 1):
        raise ValueError("Published fixed material changed")
    fixture.update(version=1, version_id=released["id"], version_digest=released["digest"])
    retain(journal, attempt, "publication", {key: released[key] for key in ("id", "workflow_id", "version", "draft_revision", "digest", "definition", "model_id", "active")})
    return fixture


def root_ref(root, workflow_id):
    return {"mode": "work", "id": root["run_id"], "fixture_id": root["task_id"], "path": "/api/v1/work-runs/" + root["run_id"]}


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    dispatchable(attempt)
    check_deadline(journal, attempt)
    initialization_ready(journal.directory, journal.data, attempt)
    fixtures = FixedFixtures(platform, journal, attempt)
    for slot in ("published", "reject"):
        check_deadline(journal, attempt)
        if cancelled():
            raise adapters.Blocked("Cancelled before next fixed root")
        if slot not in attempt["flow_code_roots"]:
            attempt["flow_code_roots"][slot] = start(fixtures, attempt["fixture"], slot, 1)
            journal.save()
        root = attempt["flow_code_roots"][slot]
        if slot == "published":
            attempt.update(platform_root=root_ref(root, attempt["fixture"]["id"]), started=True, submission_complete=True)
        else:
            attempt.setdefault("prior_run_refs", {})["reject"] = root_ref(root, attempt["fixture"]["id"])
        journal.save()
        final = drive(platform, journal, attempt, slot, root, reject=slot == "reject", cancelled=cancelled)
        if not passed(final, reject=slot == "reject"):
            attempt["fact_results"] = [Result(registered_id="approved_code_once", status="FAIL").model_dump()]
            journal.save()
            raise adapters.Blocked("Known F05 counter/code/decision control failed")
    return verified(journal.directory, journal.data, attempt, specimen)


def passed(value, *, reject):
    nodes = ("before",) if reject else ("before", "code", "after")
    names = ["workflow_approval", "workflow_code", "workflow_approval"]
    if (value["status"] != ("FAILED" if reject else "COMPLETED") or value["workflow_status"] != ("FAILED" if reject else "SUCCEEDED")
            or sum(row["run_id"] == value["run_id"] for row in value["observer"]["sessions"]) != (2 if reject else 4)
            or len(value["approvals"]) != len(nodes) or not all(action(row, node) and row["decision"] == ("reject" if reject else "approve")
                                                              for row, node in zip(value["approvals"], nodes, strict=True))):
        return False
    expected_events = [("start", "node.started"), ("start", "node.completed"), ("before", "node.started"),
                       ("before", "node.started"), ("before", "node.failed" if reject else "node.completed")]
    if not reject:
        for node in ("code", "after"):
            expected_events.extend([(node, "node.started"), (node, "node.started"), (node, "node.completed")])
        expected_events.extend([("end", "node.started"), ("end", "node.completed")])
    if [(event["node_id"], event["event"]) for event in value["node_events"]] != expected_events:
        return False
    if reject:
        return (not value["operations"] and not value["counter"]["downloaded"] and not value["artifacts"]
                and any(row["node_id"] == "before" and row["event"] == "node.failed" and row["error_code"] == "WORKFLOW_APPROVAL_REJECTED"
                        for row in value["node_events"]) and not any(row["node_id"] in {"code", "after", "end"} for row in value["node_events"]))
    expected_files = {hashlib.sha256(path.encode()).hexdigest(): (hashlib.sha256(raw).hexdigest(), len(raw)) for path, raw in (
        ("/counter.txt", b"1"), ("/workflow-code.py", contract.CODE.encode()), ("/workflow-code.input.txt", b"seed"))}
    return (value["output_digest"] == hashlib.sha256(contract.OUTPUT.encode()).hexdigest()
            and len(value["operations"]) == 3 and all(row["name"] == names[index] and row["status"] == "SUCCEEDED" and row["exit_code"] == 0
                and row["args_digest"] == digest(contract.arguments(nodes[index])) for index, row in enumerate(value["operations"]))
            and value["counter"] == {"downloaded": True, "size": 1, "sha256": hashlib.sha256(b"1").hexdigest(), "declared": "1"}
            and len(value["artifacts"]) == 3 and {item["path_digest"]: (item["digest"], item["size"]) for item in value["artifacts"]} == expected_files
            and {(event["node_id"], event["event"]) for event in value["node_events"] if event["event"] == "node.completed"}
                == {(node, "node.completed") for node in ("start", "before", "code", "after", "end")})


def validate_decisions(attempt, slot, value, *, reject):
    for index, row in enumerate(value["approvals"]):
        intent = attempt.get("submission_intents", {}).get(f"f05-{slot}-decision-{index}", {})
        expected = {"digest": row["digest"], "decision": "reject" if reject else "approve"}
        if (intent.get("status") != "CONFIRMED" or intent.get("path") != f"/api/v1/work-approvals/{row['id']}/decisions"
                or intent.get("payload_digest") != digest(expected)):
            raise UnknownSubmission("An original F05 decision is unknown or changed; never replay it")


def validate_publication(publication, fixture):
    if (set(publication) != {"id", "workflow_id", "version", "draft_revision", "digest", "definition", "model_id", "active"}
            or publication["id"] != fixture["version_id"] or publication["workflow_id"] != fixture["id"]
            or type(publication["version"]) is not int or publication["version"] != 1
            or type(publication["draft_revision"]) is not int or publication["draft_revision"] != 1
            or publication["digest"] != fixture["version_digest"] or digest(publication["definition"]) != digest(contract.definition())
            or publication["model_id"] != fixture["model_id"] or publication["active"] is not True):
        raise ValueError("F05 exact published material changed")


def initialization_ready(directory, data, attempt):
    slots = ["deadline", *(f"draft-waiting-{index}" for index in range(3)), "draft-final", "publication"]
    references = [attempt.get("flow_code_refs", {}).get(slot) for slot in slots]
    if not all(references) or attempt.get("evidence_refs", [])[:len(slots)] != references:
        raise adapters.Blocked("Complete original initialization evidence is required before main dispatch")
    publication = read(directory, data, attempt, "publication")
    validate_publication(publication, attempt["fixture"])
    root = attempt["flow_code_roots"]["draft"]
    final = read(directory, data, attempt, "draft-final")
    validate(final, root, attempt["fixture"], data, attempt)
    validate_decisions(attempt, "draft", final, reject=False)
    if not passed(final, reject=False):
        raise adapters.Blocked("The original draft did not complete the required fixed code")
    for index in range(3):
        before = read(directory, data, attempt, f"draft-waiting-{index}")
        validate(before, root, attempt["fixture"], data, attempt)
        if (not waiting(before, index) or digest({**before["approvals"][index], "decision": final["approvals"][index]["decision"]})
                != digest(final["approvals"][index])):
            raise ValueError("Original draft approval target changed before main dispatch")


def verified(directory, data, attempt, specimen):
    if specimen.case_id != contract.CASE or specimen.fixture_profile.version != 2:
        raise ValueError("Only the reviewed F05 v2 definition is supported")
    expected = ["deadline", *(f"draft-waiting-{index}" for index in range(3)), "draft-final", "publication",
                *(f"published-waiting-{index}" for index in range(3)), "published-final", "reject-waiting-0", "reject-final"]
    if (set(attempt.get("flow_code_refs", {})) != set(expected)
            or attempt.get("evidence_refs") != [attempt["flow_code_refs"][slot] for slot in expected]):
        raise adapters.Blocked("All original F05 approval stages must be sealed in order")
    bodies = {slot: read(directory, data, attempt, slot) for slot in expected}
    deadline_value(directory, data, attempt)
    publication = bodies["publication"]
    fixture = attempt["fixture"]
    validate_publication(publication, fixture)
    roots = attempt["flow_code_roots"]
    if set(roots) != {"draft", "published", "reject"} or any(len({row[key] for row in roots.values()}) != 3 for key in ("run_id", "task_id", "workflow_run_id")):
        raise ValueError("Draft/published/reject need three distinct real roots")
    for slot, root in roots.items():
        path = f"/api/v1/workflows/{fixture['id']}/work-runs"
        body = {"text": contract.INPUT, **({"revision": 1} if slot == "draft" else {"version": 1})}
        checksum = digest({"method": "POST", "path": path, "body": body, "content": None, "params": None, "files": []})
        intent = attempt.get("fixture_intents", {}).get("f05-start-" + slot, {})
        if (intent.get("status") != "CONFIRMED" or intent.get("method") != "POST" or intent.get("path") != path
                or intent.get("digest") != checksum or any(intent.get("response", {}).get(key) != root[key]
                                                         for key in ("run_id", "task_id", "workflow_run_id"))):
            raise UnknownSubmission("The exact original F05 root submission is not confirmed")
    if any(row.get("status") != "CONFIRMED" for row in attempt.get("fixture_intents", {}).values()):
        raise UnknownSubmission("An F05 initialization effect remains unknown")
    for slot in ("draft", "published", "reject"):
        final = bodies[slot + "-final"]
        validate(final, roots[slot], attempt["fixture"], data, attempt)
        if final["status"] not in {"COMPLETED", "FAILED", "CANCELLED"} or final["workflow_status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise adapters.Blocked("Unknown/nonterminal F05 execution cannot authorize cleanup")
        validate_decisions(attempt, slot, final, reject=slot == "reject")
        for index in range(1 if slot == "reject" else 3):
            before = bodies[f"{slot}-waiting-{index}"]
            validate(before, roots[slot], attempt["fixture"], data, attempt)
            if (not waiting(before, index) or digest({**before["approvals"][index], "decision": final["approvals"][index]["decision"]})
                    != digest(final["approvals"][index])):
                raise ValueError("A different approval checkpoint was substituted")
    last = bodies["reject-final"]["observer"]
    if (last["counts"] != {"work_runs": 3, "workflow_runs": 3, "model_calls": 0, "unknown_operations": 0, "active_runs": 0}
            or last["sandbox"]["attempts"] != 10 or len(last["sessions"]) != 10
            or any(sum(item["run_id"] == root["run_id"] for item in last["sessions"]) != (2 if slot == "reject" else 4)
                   for slot, root in roots.items())):
        raise adapters.Blocked("F05 complete three-root lifecycle scope is not proven")
    return {"roots": {slot: bodies[slot + "-final"] for slot in roots}, "usage": dict.fromkeys(workflow.TOTAL, 0)}


def evaluate(snapshot, specimen, attempt):
    roots = snapshot["roots"]
    good = all(passed(value, reject=slot == "reject") for slot, value in roots.items())
    return [Result(registered_id="approved_code_once", status="PASS" if good else "FAIL")], {
        "run": {"status": roots["published"]["status"]}, "usage": {"total": snapshot["usage"]}, "observed": ["usage"],
        "business_outcomes": [{"kind": slot, "run_id": row["run_id"], "status": row["status"]} for slot, row in roots.items()]}


def cleanup_ready(directory, data, attempt, specimen):
    verified(directory, data, attempt, specimen)
    return True


def cleanup(platform, journal, attempt, specimen):
    value = verified(journal.directory, journal.data, attempt, specimen)
    current = collect(platform, attempt["flow_code_roots"]["reject"], attempt["fixture"])
    return "CLEAN" if digest(current) == digest(value["roots"]["reject"]) else "NEEDS_REVIEW"
