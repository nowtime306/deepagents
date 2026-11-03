"""Retained readonly Workflow initialization, rejection and node facts; no queued-work claim."""

from uuid import UUID, uuid4

import httpx

from . import adapters
from . import fixed_workflow_observation as observation
from .contracts import Result
from .fixed_evidence import SHA, binding, load, store, text_digest
from .journal import UnknownSubmission, digest


def retain(journal, attempt, kind, body):
    snapshot = {"schema_version": 1, "kind": kind, "binding": binding(journal.data, attempt), "body": body}
    return persist(journal, attempt, kind, snapshot)


def persist(journal, attempt, kind, snapshot):
    try:
        reference = store(journal.directory, kind, snapshot)
        attempt.setdefault("evidence_refs", []).append(reference)
        journal.save()
    except OSError:
        attempt["workflow_evidence_failed"] = True
        raise
    return reference


def read(directory, data, attempt, reference, kind):
    value = load(directory, reference, kind)
    if (set(value) != {"schema_version", "kind", "binding", "body"} or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != kind or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("Readonly workflow evidence identity changed")
    return value["body"]


def ensure_baseline(platform, journal, attempt, specimen):
    fixture = attempt["fixture"]
    if attempt.get("workflow_baseline_ref"):
        body = read(journal.directory, journal.data, attempt, attempt["workflow_baseline_ref"], "workflow-baseline")
        validate_baseline(body, journal.data, attempt, specimen)
        return body
    body = {"initialization": observation.collect(platform, fixture, specimen, fixture["init_run_id"], published=False),
            "effects": observation.effects(platform, fixture, specimen)}
    validate_baseline(body, journal.data, attempt, specimen)
    attempt["workflow_baseline_ref"] = retain(journal, attempt, "workflow-baseline", body)
    journal.save()
    return body


def reject_input(platform, journal, attempt, specimen):
    fixture = attempt["fixture"]
    identifier = attempt.setdefault("invalid_run_id", str(uuid4()))
    path = f"/api/v1/workflows/{fixture['id']}/versions/{fixture['version']}/runs"
    body = {"text": specimen.input, "run_id": identifier}
    key, checksum = "reject-workflow-schema", digest({"path": path, "body": body})
    intents = attempt.setdefault("fixture_intents", {})
    if key in intents:
        row = intents[key]
        if row["digest"] != checksum or row["status"] != "REJECTED":
            raise UnknownSubmission("The schema request cannot be automatically repeated")
        return row
    platform.attest()
    intents[key] = {"status": "SUBMITTING", "digest": checksum, "path": path, "run_id": identifier}
    journal.save()
    try:
        response = platform.http.post(path, json=body)
        value = response.json()
    except (httpx.HTTPError, ValueError):
        intents[key]["status"] = "UNKNOWN"
        journal.save()
        raise UnknownSubmission("Schema request response is unknown; never automatically resubmit") from None
    error = value.get("error", {})
    intents[key].update(status="REJECTED" if 400 <= response.status_code < 500 else "UNKNOWN",
                        http_status=response.status_code,
                        error_code="WORKFLOW_INPUT_INVALID" if error.get("code") == "WORKFLOW_INPUT_INVALID" else "UNEXPECTED_ERROR",
                        field_name_reported=isinstance(error.get("message"), str) and "name" in error["message"])
    journal.save()
    if intents[key]["status"] == "UNKNOWN":
        raise UnknownSubmission("The expected schema rejection was not observed")
    return intents[key]


def rejection(platform, journal, attempt, specimen):
    if attempt.get("workflow_rejection_ref"):
        return read(journal.directory, journal.data, attempt, attempt["workflow_rejection_ref"], "workflow-rejection")
    row = reject_input(platform, journal, attempt, specimen)
    fixture = attempt["fixture"]
    path = f"/api/v1/workflows/{fixture['id']}/runs"
    absent = observation.complete_items(platform.get(path, params={"run_id": attempt["invalid_run_id"]}))
    body = {"run_id": attempt["invalid_run_id"], "input_digest": text_digest(specimen.input),
            "http_status": row["http_status"], "error_code": row["error_code"], "field_name_reported": row["field_name_reported"],
            "invalid_run_query": {"source": path, "run_id": attempt["invalid_run_id"], "complete": True,
                                  "ids": [str(UUID(item["id"])) for item in absent]},
            "effects": observation.effects(platform, fixture, specimen)}
    attempt["workflow_rejection_ref"] = retain(journal, attempt, "workflow-rejection", body)
    journal.save()
    return body


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    if attempt.get("workflow_evidence_failed"):
        raise adapters.Blocked("Failed evidence persistence requires manual review")
    baseline = ensure_baseline(platform, journal, attempt, specimen)
    if cancelled():
        raise adapters.Blocked("Cancelled before readonly dispatch")
    rejected = rejection(platform, journal, attempt, specimen) if specimen.case_id == "AET-F04" else None
    if rejected is not None and not valid_rejection(rejected, baseline, attempt, specimen):
        attempt["fact_results"] = [Result(registered_id="schema_rejects_no_effect", status="FAIL").model_dump()]
        journal.save()
        raise adapters.Blocked("Observed schema rejection or its no-effect constraint failed")
    if cancelled():
        raise adapters.Blocked("Cancelled before the legal contrast")
    fixture = attempt["fixture"]
    identifier = attempt.setdefault("client_run_id", str(uuid4()))
    if not attempt.get("submission_complete"):
        body = {"run_id": identifier, "text": specimen.fixture_parameters["fixed_inputs"].get("valid_contrast", specimen.input)}
        path = f"/api/v1/workflows/{fixture['id']}/versions/{fixture['version']}/runs"
        row = journal.post(platform, attempt, path, body, "submit-run")
        if row.get("id") != identifier:
            raise UnknownSubmission("Published execution returned a different run identity")
        attempt.update(platform_root={"mode": "workflow", "id": identifier, "fixture_id": fixture["id"],
                                      "path": f"/api/v1/workflows/{fixture['id']}/runs"}, started=True, submission_complete=True)
        journal.save()
    body = {"baseline": baseline, "rejection": rejected,
            "execution": observation.collect(platform, fixture, specimen, identifier),
            "after": observation.effects(platform, fixture, specimen)}
    return capture(platform, journal, attempt, specimen, body)


def capture(platform, journal, attempt, specimen, evidence):
    snapshot = {"schema_version": 1, "kind": "workflow-facts", "binding": binding(journal.data, attempt),
                "body": evidence, "usage": aggregate(evidence)}
    persist(journal, attempt, "workflow-facts", snapshot)
    return verified(journal.directory, journal.data, attempt, specimen)


def aggregate(body):
    return {key: body["baseline"]["initialization"]["usage"][key] + body["execution"]["usage"][key]
            for key in observation.TOTAL}


def verified(directory, data, attempt, specimen):
    if attempt.get("workflow_evidence_failed"):
        raise adapters.Blocked("Failed evidence persistence cannot authorize cleanup")
    value = None
    for reference in attempt.get("evidence_refs", []):
        kind = reference["kind"]
        if kind in {"workflow-baseline", "workflow-rejection"}:
            body = read(directory, data, attempt, reference, kind)
            if kind == "workflow-baseline":
                validate_baseline(body, data, attempt, specimen)
            else:
                baseline = read(directory, data, attempt, attempt["workflow_baseline_ref"], "workflow-baseline")
                if not valid_rejection(body, baseline, attempt, specimen):
                    raise adapters.Blocked("A retained rejection proof is not complete")
        elif kind == "workflow-facts":
            value = load(directory, reference, kind)
            if (set(value) != {"schema_version", "kind", "binding", "body", "usage"}
                    or type(value["schema_version"]) is not int or value["schema_version"] != 1
                    or digest(value["binding"]) != digest(binding(data, attempt)) or value["kind"] != kind):
                raise ValueError("Readonly run snapshot identity differs")
            evaluate(value, specimen, attempt)
        else:
            raise ValueError("Unexpected readonly evidence kind")
    if value is None:
        raise adapters.Blocked("Retained readonly execution facts are required")
    baseline = read(directory, data, attempt, attempt["workflow_baseline_ref"], "workflow-baseline")
    if baseline != value["body"]["baseline"]:
        raise ValueError("Initialization baseline changed")
    if specimen.case_id == "AET-F04":
        rejected = read(directory, data, attempt, attempt["workflow_rejection_ref"], "workflow-rejection")
        if rejected != value["body"]["rejection"]:
            raise ValueError("The pre-contrast rejection proof changed")
    return value


def cleanup(platform, journal, attempt, specimen):
    snapshot = verified(journal.directory, journal.data, attempt, specimen)
    current = observation.effects(platform, attempt["fixture"], specimen)
    return "CLEAN" if current == snapshot["body"]["after"] and cleanup_ready(
        journal.directory, journal.data, attempt, specimen) else "NEEDS_REVIEW"


def cleanup_ready(directory, data, attempt, specimen):
    """A stable known business failure may be disposed; unsettled operations may not."""
    after = verified(directory, data, attempt, specimen)["body"]["after"]
    counts, provider, sandbox = after["counts"], after["provider"], after["sandbox"]
    unsettled = ("active_workflow_runs", "active_ask_runs", "active_work_runs", "active_sandboxes",
                 "unknown_operations", "unknown_model_usage")
    return (all(counts[key] == 0 for key in unsettled) and provider["pending"] == provider["unknown_roots"] == 0
            and sandbox["attempts"] <= counts["sandbox_sessions"])


def validate_record(row, fixture, specimen, *, published):
    if set(row) != {"id", "workflow_id", "status", "input", "output", "source", "events_source", "version_id", "version",
                    "definition_digest", "business_digest", "events", "usage"}:
        raise ValueError("Unexpected readonly run fields")
    UUID(row["id"])
    base = f"/api/v1/workflows/{fixture['id']}"
    if (row["workflow_id"] != fixture["id"] or row["source"] != base + "/runs"
            or row["events_source"] != base + f"/runs/{row['id']}/events" or row["version_id"] != fixture["version_id"]
            or type(fixture["version"]) is not int or fixture["version"] < 1
            or published and type(row["version"]) is not int
            or row["version"] != (fixture["version"] if published else None)
            or row["definition_digest"] != digest(observation.definition(specimen.case_id))
            or not SHA.fullmatch(row["business_digest"]) or row["business_digest"] != fixture["version_digest"]
            or row["status"] not in adapters.TERMINAL
            or not isinstance(row["events"], list) or not 1 <= len(row["events"]) <= 64
            or set(row["usage"]) != observation.TOTAL
            or any(type(count) is not int or count < 0 for count in row["usage"].values())):
        raise adapters.Blocked("Readonly run observation is incomplete or mismatched")
    nodes = {node["id"]: node["kind"] for node in observation.definition(specimen.case_id)["nodes"]}
    last = 0
    for event in row["events"]:
        if (set(event) != {"event_id", "node_id", "event", "kind", "input", "output"}
                or type(event["event_id"]) is not int or event["event_id"] <= last
                or event["node_id"] not in nodes or event["kind"] != nodes[event["node_id"]]
                or event["event"] not in {"node.started", "node.completed", "node.failed", "node.handled"}):
            raise adapters.Blocked("Unordered or unrecognized formal workflow events")
        last = event["event_id"]
        validate_text(event["input"])
        validate_text(event["output"])
        if (event["event"] == "node.started" and event["input"] is None
                or event["event"] == "node.completed" and event["output"] is None):
            raise adapters.Blocked("Required event input/output was not observed")
    validate_text(row["input"])
    validate_text(row["output"])
    if row["input"] is None or row["status"] == "SUCCEEDED" and row["output"] is None:
        raise adapters.Blocked("Required official input/output is missing")
    adapters.known_usage({"observed": ["usage"], "usage": {"total": row["usage"]}}, specimen)


def validate_text(value):
    if value is not None and (set(value) != {"sha256", "declared"} or not SHA.fullmatch(value["sha256"])
                              or value["declared"] is not None and (value["declared"] not in observation.TOKENS
                                  or text_digest(value["declared"]) != value["sha256"])):
        raise ValueError("Unexpected or altered readonly text projection")


def sequence(row, case_id):
    source = "hello" if case_id == "AET-F02" else '{"name":"李四"}'
    pairs = [("start", source, source), ("upper", source, "HELLO"), ("choose", "HELLO", "true"), ("yes", "HELLO", "HELLO")]
    if case_id == "AET-F04":
        pairs = [("start", source, source), ("end", source, source)]
    expected = [(node, event, value) for node, start, end in pairs for event, value in (("node.started", start), ("node.completed", end))]
    if len(row["events"]) != len(expected):
        return False
    return all(event["node_id"] == node and event["event"] == name
               and event["input" if name == "node.started" else "output"]["sha256"] == text_digest(text)
               for event, (node, name, text) in zip(row["events"], expected, strict=True))


def no_effects(value, *, runs, events):
    counts = value["counts"]
    return (all(count == (runs if key == "workflow_runs" else events if key == "workflow_events" else 0)
                for key, count in counts.items()) and value["provider"]["calls"] == 0
            and value["provider"]["pending"] == 0 and value["provider"]["unknown_roots"] == 0 and value["sandbox"]["attempts"] == 0)


def validate_baseline(body, data, attempt, specimen):
    if set(body) != {"initialization", "effects"}:
        raise ValueError("Unexpected initialization evidence")
    fixture = attempt["fixture"]
    validate_record(body["initialization"], fixture, specimen, published=False)
    observation.validate_effects(body["effects"], fixture, specimen, data["environment_id"], data["owner_id"])
    initial = body["initialization"]
    if (initial["id"] != fixture["init_run_id"] or initial["status"] != "SUCCEEDED" or not sequence(initial, specimen.case_id)
            or body["effects"]["run_ids"] != [fixture["init_run_id"]]
            or body["effects"]["namespace"] != attempt["sandbox_namespace"]
            or not no_effects(body["effects"], runs=1, events=len(initial["events"])) or any(initial["usage"].values())):
        raise adapters.Blocked("Initialization must be terminal, correctly observed and effect-free before the baseline")


def valid_rejection(row, baseline, attempt, specimen):
    if set(row) != {"run_id", "input_digest", "http_status", "error_code", "field_name_reported", "invalid_run_query", "effects"}:
        raise ValueError("Unexpected rejection evidence")
    query = row["invalid_run_query"]
    if set(query) != {"source", "run_id", "complete", "ids"}:
        raise ValueError("Unexpected rejection query projection")
    observation.validate_effects(row["effects"], attempt["fixture"], specimen,
                                 baseline["effects"]["environment_id"], baseline["effects"]["owner_id"])
    return (row["run_id"] == attempt["invalid_run_id"] and row["input_digest"] == text_digest(specimen.input)
            and type(row["http_status"]) is int and row["http_status"] == 422
            and row["error_code"] == "WORKFLOW_INPUT_INVALID" and row["field_name_reported"] is True
            and query["complete"] is True
            and query == {"source": f"/api/v1/workflows/{attempt['fixture']['id']}/runs", "run_id": row["run_id"], "complete": True, "ids": []}
            and row["effects"] == baseline["effects"])


def evaluate(snapshot, specimen, attempt):
    body, fixture, identity = snapshot["body"], attempt["fixture"], snapshot["binding"]
    if set(body) != {"baseline", "rejection", "execution", "after"}:
        raise ValueError("Unexpected readonly factual body")
    validate_baseline(body["baseline"], identity, attempt, specimen)
    row = body["execution"]
    validate_record(row, fixture, specimen, published=True)
    root = attempt["platform_root"]
    if root != {"mode": "workflow", "id": row["id"], "fixture_id": fixture["id"], "path": f"/api/v1/workflows/{fixture['id']}/runs"}:
        raise ValueError("Readonly root identity differs")
    observation.validate_effects(body["after"], fixture, specimen, identity["environment_id"], identity["owner_id"])
    if (body["after"]["namespace"] != attempt["sandbox_namespace"] or snapshot["usage"] != aggregate(body)
            or set(snapshot["usage"]) != observation.TOTAL
            or any(type(count) is not int or count < 0 for count in snapshot["usage"].values())):
        raise ValueError("Readonly namespace or aggregate native usage changed")
    source = "hello" if specimen.case_id == "AET-F02" else '{"name":"李四"}'
    output = "HELLO" if specimen.case_id == "AET-F02" else source
    passed = (row["status"] == "SUCCEEDED" and row["input"]["sha256"] == text_digest(source)
              and row["output"] is not None and row["output"]["sha256"] == text_digest(output)
              and sequence(row, specimen.case_id) and not any(snapshot["usage"].values())
              and set(body["after"]["run_ids"]) == {fixture["init_run_id"], row["id"]}
              and no_effects(body["after"], runs=2, events=len(body["baseline"]["initialization"]["events"]) + len(row["events"])))
    if specimen.case_id == "AET-F04":
        observation.validate_effects(body["rejection"]["effects"], fixture, specimen, identity["environment_id"], identity["owner_id"])
        passed = passed and valid_rejection(body["rejection"], body["baseline"], attempt, specimen)
    elif body["rejection"] is not None:
        raise ValueError("Unexpected schema-rejection body for a branch case")
    name = "branch_and_variables" if specimen.case_id == "AET-F02" else "schema_rejects_no_effect"
    outcomes = [{"kind": "published-workflow" if specimen.case_id == "AET-F02" else "legal-contrast",
                 "run_id": row["id"], "status": row["status"]}]
    if specimen.case_id == "AET-F04":
        outcomes.insert(0, {"kind": "invalid-input", "status": "REJECTED", "http_status": body["rejection"]["http_status"],
                            "submission_request_id": body["rejection"]["run_id"], "platform_root": None})
    return [Result(registered_id=name, status="PASS" if passed else "FAIL")], {
        "run": {"status": row["status"]}, "usage": {"total": snapshot["usage"]}, "observed": ["usage"], "business_outcomes": outcomes}
