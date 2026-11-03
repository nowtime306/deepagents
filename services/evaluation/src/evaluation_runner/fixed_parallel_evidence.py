"""Four real roots and three nested branches for the fixed F06 readonly case."""

import json
from uuid import UUID, uuid4

from . import adapters
from . import fixed_workflow_observation as observation
from .contracts import Result
from .fixed_evidence import SHA, binding, load, store, text_digest
from .journal import UnknownSubmission, digest

TEXT = "Mixed"
OUTPUT = json.dumps(["mixed", "MIXED", "mixed"])
TOKENS = {TEXT, "mixed", "MIXED", OUTPUT}


def graph(operation=None, versions=()):
    if operation not in {None, "lower", "upper"} or (operation is None and len(versions) != 3) or operation and versions:
        raise ValueError("Only fixed F06 transforms and three published branches are allowed")
    for identifier in versions:
        UUID(identifier)
    return {"schema_version": 1, "execution": "readonly", "input_schema": {"type": "string"},
            "output_schema": {"type": "string"}, "node_budget": 3 if operation else 12,
            "model_calls": 1, "token_limit": 1000,
            "nodes": [{"id": "start", "name": None, "kind": "start", "input": "input", "parameters": {}},
                      {"id": "action", "name": None, "kind": "transform" if operation else "parallel", "input": "input",
                       "parameters": {"operation": operation} if operation else {"version_ids": list(versions)}},
                      {"id": "end", "name": None, "kind": "end", "input": "action", "parameters": {}}],
            "edges": [{"source": "start", "target": "action", "case": None},
                      {"source": "action", "target": "end", "case": None}]}


def initialize(fixtures, model):
    children = [{"operation": operation, **fixtures.readonly_workflow(
        "AET-F06", model, graph(operation), TEXT, slot=operation)} for operation in ("lower", "upper")]
    versions = [children[index]["version_id"] for index in (0, 1, 0)]
    return {"model_id": model, "children": children, **fixtures.readonly_workflow(
        "AET-F06", model, graph(versions=versions), TEXT)}


def specifications(fixture):
    children = fixture["children"]
    if (not isinstance(children, list) or len(children) != 2
            or [item["operation"] for item in children] != ["lower", "upper"]):
        raise ValueError("The two immutable child fixtures are required")
    items = [*children, fixture]
    for field in ("id", "version_id", "init_run_id"):
        identifiers = [str(UUID(item[field])) for item in items]
        if len(set(identifiers)) != 3:
            raise ValueError("Parent and child fixtures must have distinct identities")
    versions = [children[index]["version_id"] for index in (0, 1, 0)]
    return [(item, graph(item["operation"])) for item in children] + [(fixture, graph(versions=versions))]


def text(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise adapters.Blocked("F06 text is unavailable or exceeds the fixed bound")
    return {"sha256": text_digest(value), "declared": value if value in TOKENS else None}


def collect(platform, fixture, item, definition, run_id, *, published=False):
    base = "/api/v1/workflows/" + item["id"]
    version = platform.get(base + f"/versions/{item['version']}")
    if (version["id"] != item["version_id"] or version["workflow_id"] != item["id"]
            or version["model_id"] != fixture["model_id"] or version["definition"] != definition
            or version["digest"] != item["version_digest"] or not SHA.fullmatch(version["digest"])
            or type(version["version"]) is not int or version["version"] != item["version"] or version["active"] is not True):
        raise adapters.Blocked("The fixed F06 published definition changed")
    rows = observation.complete_items(platform.get(base + "/runs", params={"run_id": run_id}))
    if len(rows) != 1 or rows[0]["id"] != run_id or rows[0]["workflow_id"] != item["id"]:
        raise adapters.Blocked("The exact formal F06 root is missing")
    row, snapshot = rows[0], rows[0]["snapshot"]
    if (snapshot["definition"] != definition or snapshot["model_id"] != fixture["model_id"]
            or type(snapshot["revision"]) is not int or type(version["draft_revision"]) is not int
            or snapshot["revision"] != version["draft_revision"]
            or published and type(snapshot.get("published_version")) is not int
            or snapshot.get("published_version") != (item["version"] if published else None)):
        raise adapters.Blocked("F06 runtime business snapshot differs")
    native = platform.get(f"/api/v1/agent-evaluations/workflow-runs/{run_id}/usage")
    if native.get("complete") is not True or native.get("scope") != {"mode": "workflow", "run_id": run_id}:
        raise adapters.Blocked("Complete native F06 root usage is required")
    events = platform.get(base + f"/runs/{run_id}/events")
    if set(events) != {"items"} or not isinstance(events["items"], list) or not 1 <= len(events["items"]) <= 64:
        raise adapters.Blocked("The complete bounded F06 event collection is required")
    projected = [{"event_id": event["event_id"], "node_id": event["node_id"], "event": event["event"],
                  "kind": event["data"].get("kind"), "input": text(event["data"].get("input")),
                  "output": text(event["data"].get("output"))} for event in events["items"]]
    return {"id": run_id, "workflow_id": item["id"], "status": row["status"], "source": base + "/runs",
            "events_source": base + f"/runs/{run_id}/events", "version_id": version["id"],
            "version": version["version"] if published else None, "business_digest": version["digest"],
            "definition_digest": digest(definition), "input": text(row["input"]), "output": text(row["output"]),
            "events": projected, "usage": native["total"]}


def retain(journal, attempt, kind, body):
    try:
        value = {"schema_version": 1, "kind": kind, "binding": binding(journal.data, attempt), "body": body}
        if kind == "parallel-facts":
            value["usage"] = aggregate(body)
        reference = store(journal.directory, kind, value)
        attempt.setdefault("evidence_refs", []).append(reference)
        if kind == "parallel-baseline":
            attempt["parallel_baseline_ref"] = reference
        journal.save()
    except BaseException:
        attempt["parallel_evidence_failed"] = True
        raise
    return value


def read(directory, data, attempt, reference):
    value = load(directory, reference, reference["kind"])
    fields = {"schema_version", "kind", "binding", "body"} | ({"usage"} if value.get("kind") == "parallel-facts" else set())
    if (set(value) != fields or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != reference["kind"]
            or value["kind"] not in {"parallel-baseline", "parallel-facts"}
            or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("F06 retained evidence identity differs")
    return value


def baseline(platform, journal, attempt, specimen):
    if attempt.get("parallel_evidence_failed"):
        raise adapters.Blocked("F06 evidence persistence failed; do not dispatch or clean")
    if attempt.get("parallel_baseline_ref"):
        value = read(journal.directory, journal.data, attempt, attempt["parallel_baseline_ref"])
        if value["kind"] != "parallel-baseline":
            raise ValueError("The pre-dispatch F06 reference is not a baseline")
    else:
        fixture = attempt["fixture"]
        body = {"initialization": [collect(platform, fixture, item, definition, item["init_run_id"])
                                   for item, definition in specifications(fixture)],
                "effects": observation.effects(platform, fixture, specimen)}
        validate_baseline(body, journal.data, attempt, specimen)
        value = retain(journal, attempt, "parallel-baseline", body)
    validate_baseline(value["body"], journal.data, attempt, specimen)
    return value["body"]


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    before = baseline(platform, journal, attempt, specimen)
    if cancelled():
        raise adapters.Blocked("Cancelled before F06 dispatch")
    fixture = attempt["fixture"]
    identifier = attempt.setdefault("client_run_id", str(uuid4()))
    if not attempt.get("submission_complete"):
        body = {"run_id": identifier, "text": TEXT}
        path = f"/api/v1/workflows/{fixture['id']}/versions/{fixture['version']}/runs"
        prior = attempt.get("submission_intents", {}).get("submit-run")
        if prior is not None:
            if prior.get("status") != "CONFIRMED":
                raise UnknownSubmission("The original F06 submission is unsettled; never replay it")
            if (prior.get("path") != path or prior.get("payload_digest") != digest(body)
                    or prior.get("response", {}).get("id") != identifier):
                raise ValueError("The confirmed F06 submission changed")
        elif observation.effects(platform, fixture, specimen) != before["effects"]:
            raise adapters.Blocked("The F06 target changed after initialization; stop before dispatch")
        row = journal.post(platform, attempt, path, body, "submit-run")
        if row.get("id") != identifier:
            raise UnknownSubmission("F06 returned a different root")
        attempt.update(platform_root={"mode": "workflow", "id": identifier, "fixture_id": fixture["id"],
                                      "path": f"/api/v1/workflows/{fixture['id']}/runs"}, started=True, submission_complete=True)
        journal.save()
    definition = specifications(fixture)[-1][1]
    body = {"baseline": before, "execution": collect(platform, fixture, fixture, definition, identifier, published=True),
            "after": observation.effects(platform, fixture, specimen)}
    retain(journal, attempt, "parallel-facts", body)
    return verified(journal.directory, journal.data, attempt, specimen)


def validate_text(value):
    if value is not None and (set(value) != {"sha256", "declared"} or not isinstance(value["sha256"], str)
                              or not SHA.fullmatch(value["sha256"]) or value["declared"] is not None
                              and (value["declared"] not in TOKENS or text_digest(value["declared"]) != value["sha256"])):
        raise ValueError("F06 text projection changed")


def validate_record(row, item, definition, *, published=False):
    keys = {"id", "workflow_id", "status", "source", "events_source", "version_id", "version", "business_digest",
            "definition_digest", "input", "output", "events", "usage"}
    base = "/api/v1/workflows/" + item["id"]
    UUID(row["id"])
    if (set(row) != keys or row["workflow_id"] != item["id"] or row["source"] != base + "/runs"
            or row["events_source"] != base + f"/runs/{row['id']}/events" or row["version_id"] != item["version_id"]
            or type(item["version"]) is not int or item["version"] < 1
            or published and type(row["version"]) is not int or row["version"] != (item["version"] if published else None)
            or row["business_digest"] != item["version_digest"] or not SHA.fullmatch(row["business_digest"])
            or row["definition_digest"] != digest(definition) or row["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}
            or not isinstance(row["events"], list) or not 1 <= len(row["events"]) <= 64
            or set(row["usage"]) != observation.TOTAL or any(type(n) is not int or n < 0 for n in row["usage"].values())):
        raise ValueError("F06 record scope, immutable version or native usage differs")
    nodes = {"start": "start", "action": definition["nodes"][1]["kind"], "end": "end"}
    if nodes["action"] == "parallel":
        nodes.update({f"action.{index}.{node}": kind for index in range(3)
                      for node, kind in (("start", "start"), ("action", "transform"), ("end", "end"))})
    last = 0
    for event in row["events"]:
        if (set(event) != {"event_id", "node_id", "event", "kind", "input", "output"}
                or type(event["event_id"]) is not int or event["event_id"] <= last or event["node_id"] not in nodes
                or event["kind"] != nodes[event["node_id"]]
                or event["event"] not in {"node.started", "node.completed", "node.failed"}):
            raise ValueError("Unordered or unknown fixed F06 events")
        last = event["event_id"]
        validate_text(event["input"])
        validate_text(event["output"])
    validate_text(row["input"])
    validate_text(row["output"])


def sequence(row, operation=None):
    output = TEXT.lower() if operation == "lower" else TEXT.upper() if operation == "upper" else OUTPUT
    pairs = {"start": (TEXT, TEXT), "action": (TEXT, output), "end": (output, output)}
    if operation is None:
        for index, end in enumerate(("mixed", "MIXED", "mixed")):
            pairs.update({f"action.{index}.start": (TEXT, TEXT), f"action.{index}.action": (TEXT, end),
                          f"action.{index}.end": (end, end)})
    if row["status"] != "SUCCEEDED" or row["input"] != text(TEXT) or row["output"] != text(output):
        return False
    found = {}
    for event in row["events"]:
        key = (event["node_id"], event["event"])
        if key in found or key[0] not in pairs or key[1] not in {"node.started", "node.completed"}:
            return False
        expected = pairs[key[0]][key[1] == "node.completed"]
        if event["input" if key[1] == "node.started" else "output"] != text(expected):
            return False
        found[key] = event["event_id"]
    if len(found) != 2 * len(pairs):
        return False
    chains = [["start", "action", "end"]] if operation else [[f"action.{i}.{n}" for n in ("start", "action", "end")] for i in range(3)]
    for chain in chains:
        values = [found[node, event] for node in chain for event in ("node.started", "node.completed")]
        if values != sorted(values):
            return False
    if operation is not None:
        return True
    ordered = [found["start", "node.started"], found["start", "node.completed"], found["action", "node.started"],
               min(found[f"action.{i}.start", "node.started"] for i in range(3)),
               max(found[f"action.{i}.end", "node.completed"] for i in range(3)), found["action", "node.completed"],
               found["end", "node.started"], found["end", "node.completed"]]
    return ordered == sorted(ordered)


def validate_baseline(body, data, attempt, specimen):
    if set(body) != {"initialization", "effects"} or not isinstance(body["initialization"], list) or len(body["initialization"]) != 3:
        raise ValueError("All three formal initialization roots must be retained")
    fixture = attempt["fixture"]
    for row, (item, definition) in zip(body["initialization"], specifications(fixture), strict=True):
        validate_record(row, item, definition)
        if row["id"] != item["init_run_id"] or not sequence(row, item.get("operation")) or any(row["usage"].values()):
            raise adapters.Blocked("F06 initialization was not successful, known and effect-free")
    effects(body["effects"], data, attempt, specimen, body["initialization"], [fixture["init_run_id"]])


def effects(value, data, attempt, specimen, records, root_ids):
    observation.validate_effects(value, attempt["fixture"], specimen, data["environment_id"], data["owner_id"])
    if (value["namespace"] != attempt["sandbox_namespace"] or set(value["run_ids"]) != set(root_ids)
            or not all(count == (len(records) if key == "workflow_runs" else sum(len(row["events"]) for row in records)
                                 if key == "workflow_events" else 0) for key, count in value["counts"].items())
            or any(value["provider"][key] for key in ("calls", "pending", "unknown_roots")) or value["sandbox"]["attempts"] != 0):
        raise adapters.Blocked("F06 has additional, missing or unsettled effects")


def verified(directory, data, attempt, specimen):
    if attempt.get("parallel_evidence_failed"):
        raise adapters.Blocked("Failed F06 persistence cannot authorize grading or cleanup")
    baseline_ref = attempt.get("parallel_baseline_ref")
    if not baseline_ref or baseline_ref not in attempt.get("evidence_refs", []):
        raise adapters.Blocked("The F06 pre-dispatch baseline must be retained")
    initial = read(directory, data, attempt, baseline_ref)
    if initial["kind"] != "parallel-baseline":
        raise ValueError("Wrong F06 baseline kind")
    validate_baseline(initial["body"], data, attempt, specimen)
    final = None
    for reference in attempt.get("evidence_refs", []):
        value = read(directory, data, attempt, reference)
        if value["kind"] == "parallel-facts":
            if value["body"]["baseline"] != initial["body"]:
                raise ValueError("The F06 pre-dispatch baseline changed")
            evaluate(value, specimen, attempt)
            final = value
        elif value != initial:
            raise ValueError("An unrelated F06 initialization was retained")
    if final is None:
        raise adapters.Blocked("F06 execution facts must be retained")
    return final


def evaluate(snapshot, specimen, attempt):
    body, fixture, identity = snapshot["body"], attempt["fixture"], snapshot["binding"]
    if specimen.case_id != "AET-F06" or set(body) != {"baseline", "execution", "after"}:
        raise ValueError("Unexpected fixed F06 evidence")
    validate_baseline(body["baseline"], identity, attempt, specimen)
    row = body["execution"]
    validate_record(row, fixture, specifications(fixture)[-1][1], published=True)
    root = {"mode": "workflow", "id": row["id"], "fixture_id": fixture["id"], "path": f"/api/v1/workflows/{fixture['id']}/runs"}
    intent = attempt.get("submission_intents", {}).get("submit-run", {})
    if (attempt.get("platform_root") != root or row["id"] != attempt["client_run_id"] or row["id"] == fixture["init_run_id"]
            or intent.get("status") != "CONFIRMED" or intent.get("response", {}).get("id") != row["id"]
            or intent.get("path") != f"/api/v1/workflows/{fixture['id']}/versions/{fixture['version']}/runs"
            or intent.get("payload_digest") != digest({"run_id": row["id"], "text": TEXT})):
        raise ValueError("The actual confirmed F06 submission differs")
    records = [*body["baseline"]["initialization"], row]
    effects(body["after"], identity, attempt, specimen, records, [fixture["init_run_id"], row["id"]])
    usage = aggregate(body)
    if (set(snapshot["usage"]) != observation.TOTAL or any(type(n) is not int or n < 0 for n in snapshot["usage"].values())
            or snapshot["usage"] != usage):
        raise ValueError("F06 aggregate native usage changed")
    passed = sequence(row) and not any(usage.values())
    return [Result(registered_id="all_branches_join", status="PASS" if passed else "FAIL")], {
        "run": {"status": row["status"]}, "usage": {"total": usage}, "observed": ["usage"]}


def aggregate(body):
    records = [*body["baseline"]["initialization"], body["execution"]]
    return {key: sum(record["usage"][key] for record in records) for key in observation.TOTAL}


def cleanup_ready(directory, data, attempt, specimen):
    verified(directory, data, attempt, specimen)
    return True  # The same strict terminal/effect checks above cover all four real roots.


def cleanup(platform, journal, attempt, specimen):
    value = verified(journal.directory, journal.data, attempt, specimen)
    return "CLEAN" if observation.effects(platform, attempt["fixture"], specimen) == value["body"]["after"] else "NEEDS_REVIEW"
