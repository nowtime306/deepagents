"""F01 model/version maintenance and F03 known nested failure; formal HTTP only."""

from uuid import UUID, uuid4

from . import adapters
from . import fixed_workflow_observation as observation
from .contracts import Result
from .fixed_evidence import SHA, binding, load, store, text_digest
from .journal import UnknownSubmission, digest

CASES = {"AET-F01", "AET-F03"}
LIMITS = {"AET-F01": {"max_model_calls": 4, "max_tokens": 60, "max_roots": 4},
          "AET-F03": {"max_model_calls": 0, "max_tokens": 0, "max_roots": 3}}
INPUT = "MiXeD"
GOOD = '{"name":"ok"}'
TOKENS = {INPUT, "MIXED", "mixed", GOOD, "{}", "ok"}


def graph(kind, child=None):
    if kind not in {"upper", "lower", "child", "parent"} or (kind == "parent") != (child is not None):
        raise ValueError("Only fixed F01/F03 definitions are allowed")
    if child:
        UUID(child)
    node_kind = "llm" if kind in {"upper", "lower"} else "transform" if kind == "child" else "subworkflow"
    parameters = ({"instructions": "AE-F01-" + kind + "-v1"} if node_kind == "llm" else
                  {"operation": "json_get", "path": "name"} if kind == "child" else {"version_id": child})
    return {"schema_version": 1, "execution": "readonly", "input_schema": {"type": "string"},
            "output_schema": {"type": "string"}, "node_budget": 6, "model_calls": 1, "token_limit": 8192 if kind in {"upper", "lower"} else 1000,
            "nodes": [{"id": "start", "name": None, "kind": "start", "input": "input", "parameters": {}},
                      {"id": "action", "name": None, "kind": node_kind, "input": "input", "parameters": parameters},
                      {"id": "end", "name": None, "kind": "end", "input": "action", "parameters": {}}],
            "edges": [{"source": "start", "target": "action", "case": None},
                      {"source": "action", "target": "end", "case": None}]}


def publish(fixtures, identifier, revision, definition, text, key):
    tested = fixtures.mutate(key + "-test", "POST", f"/api/v1/workflows/{identifier}/test-runs",
                             body={"text": text, "revision": revision})
    if tested.get("status") != "SUCCEEDED":
        raise adapters.Blocked("Fixed initialization did not succeed")
    require_usage(fixtures.platform, tested["id"], fixtures.attempt["case_id"])
    released = fixtures.mutate(key + "-publish", "POST", f"/api/v1/workflows/{identifier}/publish",
                               body={"revision": revision})
    versions = fixtures.platform.get(f"/api/v1/workflows/{identifier}/versions")
    row = next(item for item in versions["items"] if item["version"] == released["version"])
    if row["definition"] != definition or row["draft_revision"] != revision or row["active"] is not True:
        raise ValueError("Published initialization definition changed")
    return {"id": identifier, "version": row["version"], "version_id": row["id"], "revision": revision,
            "version_digest": row["digest"], "init_run_id": tested["id"], "definition": definition}


def initialize(fixtures, model, specimen):
    case = specimen.case_id
    if case == "AET-F01":
        first = graph("upper")
        identifier = fixtures.create("workflow-root", "/api/v1/workflows", {
            "name": "AE F01 " + fixtures.prefix, "model_id": model, "definition": first})
        v1 = publish(fixtures, identifier, 1, first, INPUT, "v1")
        fixtures.mutate("workflow-v2", "PUT", f"/api/v1/workflows/{identifier}", body={
            "name": "AE F01 " + fixtures.prefix, "model_id": model, "revision": 1, "definition": graph("lower")})
        v2 = publish(fixtures, identifier, 2, graph("lower"), INPUT, "v2")
        selection = fixtures.platform.get(f"/api/v1/workflows/{identifier}/versions")
        fixtures.mutate("current-v1", "POST", f"/api/v1/workflows/{identifier}/versions/1/set-current",
                        body={"release_revision": selection["release_revision"]})
        fixtures.mutate("maintain-f01-model", "PUT", f"/api/v1/admin/models/{model}", body={
            "name": f"AE {case} lead {fixtures.prefix}", "model": f"eval::{case}::v1", "kind": "chat",
            "provider": "custom", "protocol": "openai", "base_url": fixtures.platform.target.api_base_url + "/controlled-model",
            "enabled": True, "supports_tools": True, "max_output_tokens": 4096, "temperature": 0.4, "revision": 1})
        result = {"id": identifier, "model_id": model, "versions": [v1, v2]}
    elif case == "AET-F03":
        child = fixtures.create("workflow-child", "/api/v1/workflows", {
            "name": "AE F03 child " + fixtures.prefix, "model_id": model, "definition": graph("child")})
        leaf = publish(fixtures, child, 1, graph("child"), GOOD, "child")
        definition = graph("parent", leaf["version_id"])
        parent = fixtures.create("workflow-root", "/api/v1/workflows", {
            "name": "AE F03 parent " + fixtures.prefix, "model_id": model, "definition": definition})
        result = {"id": parent, "model_id": model, "versions": [leaf, publish(fixtures, parent, 1, definition, GOOD, "parent")]}
    else:
        raise adapters.Blocked("Only F01/F03 are implemented by this module")
    result["origin"] = fixtures.platform.target.api_base_url
    validate_fixture(result, case)
    return result


def readiness(platform, specimen, capabilities=None):
    proof = platform.attest()
    capabilities = capabilities or platform.get("/api/v1/agent-evaluations/profiles/capabilities")
    expected = LIMITS[specimen.case_id]
    limits = capabilities.get("case_limits", {})
    root = proof.get("execution_limits", {}).get("profiles", {}).get(specimen.case_id)
    if (specimen.mode != "workflow" or specimen.metrics or specimen.approval_policy != "none"
            or specimen.input != (INPUT if specimen.case_id == "AET-F01" else "{}")
            or limits.get("enforcement") != "controlled-case-reserve-v1"
            or limits.get("scope") != "evaluation-owner-and-case" or digest(limits.get("profiles", {}).get(specimen.case_id)) != digest(expected)
            or digest(root) != digest({"mode": "workflow", "max_model_calls": 1 if specimen.case_id == "AET-F01" else 0,
                                      "max_tokens": 8192 if specimen.case_id == "AET-F01" else 0})
            or "readonly-workflow-effects-v1" not in capabilities.get("observers", [])
            or specimen.budgets.max_model_calls < expected["max_model_calls"] or specimen.budgets.max_tokens < expected["max_tokens"]):
        raise adapters.Blocked("Fixed workflow execution/budget capability is incomplete")


def validate_fixture(fixture, case):
    if set(fixture) != {"id", "model_id", "versions", "origin"} or not isinstance(fixture["versions"], list) or len(fixture["versions"]) != 2:
        raise ValueError("Fixed workflow fixture is incomplete")
    UUID(fixture["id"])
    rows = fixture["versions"]
    definitions = [graph("upper"), graph("lower")] if case == "AET-F01" else [graph("child"), graph("parent", rows[0]["version_id"])]
    for index, row in enumerate(rows):
        for field in ("id", "version_id", "init_run_id"):
            UUID(row[field])
        if (set(row) != {"id", "version", "version_id", "revision", "version_digest", "init_run_id", "definition"}
                or type(row["version"]) is not int or row["version"] != (index + 1 if case == "AET-F01" else 1)
                or type(row["revision"]) is not int or row["revision"] != (index + 1 if case == "AET-F01" else 1)
                or digest(row["definition"]) != digest(definitions[index]) or not SHA.fullmatch(row["version_digest"])):
            raise ValueError("Fixed business version changed")
    if (len({row["init_run_id"] for row in rows}) != 2 or len({row["version_id"] for row in rows}) != 2
            or (rows[0]["id"] == rows[1]["id"]) != (case == "AET-F01") or rows[-1]["id"] != fixture["id"]):
        raise ValueError("Fixed root/version identities changed")


def validate_initialization(attempt, specimen):
    fixture, prefix, case = attempt["fixture"], attempt["attempt_id"][:12], specimen.case_id
    model = fixture["model_id"]
    # The target origin is not reconstructed from arbitrary client text: the original model creation digest
    # and later maintenance must share the sealed origin kept in the fixture.
    origin = fixture.get("origin")
    if not isinstance(origin, str):
        raise TypeError("The fixture target origin is required")
    expected = {}

    def add(key, method, path, body, response):
        expected[key] = (method, path, digest({"method": method, "path": path, "body": body,
                                             "content": None, "params": None, "files": []}), response)

    model_body = {"name": f"AE {case} lead {prefix}", "model": f"eval::{case}::v1", "base_url": origin + "/controlled-model",
                  "kind": "chat", "provider": "custom", "protocol": "openai", "enabled": True, "supports_tools": True,
                  "max_output_tokens": 4096}
    add("model-lead", "POST", "/api/v1/admin/models", model_body, {"id": model})
    add("seal-model-lead", "POST", "/api/v1/agent-evaluations/profiles/model-bindings",
        {"model_id": model, "case_id": case, "role": "lead"}, {"sealed": True})
    for index, item in enumerate(fixture["versions"]):
        key = ("v1", "v2")[index] if case == "AET-F01" else ("child", "parent")[index]
        slot = "root" if case == "AET-F01" else "child" if index == 0 else "root"
        name = "AE F01 " + prefix if case == "AET-F01" else "AE F03 " + ("child " if index == 0 else "parent ") + prefix
        if index == 0 or case == "AET-F03":
            add("workflow-" + slot, "POST", "/api/v1/workflows", {"name": name, "model_id": model, "definition": item["definition"]}, {"id": item["id"]})
        else:
            add("workflow-v2", "PUT", f"/api/v1/workflows/{item['id']}",
                {"name": name, "model_id": model, "revision": 1, "definition": item["definition"]}, {"id": item["id"]})
        add(key + "-test", "POST", f"/api/v1/workflows/{item['id']}/test-runs",
            {"text": INPUT if case == "AET-F01" else GOOD, "revision": item["revision"]}, {"id": item["init_run_id"], "status": "SUCCEEDED"})
        add(key + "-publish", "POST", f"/api/v1/workflows/{item['id']}/publish", {"revision": item["revision"]}, {"version": item["version"]})
    if case == "AET-F01":
        add("current-v1", "POST", f"/api/v1/workflows/{fixture['id']}/versions/1/set-current", {"release_revision": 3}, {})
        add("maintain-f01-model", "PUT", f"/api/v1/admin/models/{model}", {**model_body, "temperature": 0.4, "revision": 1}, {"id": model})
    intents = attempt.get("fixture_intents", {})
    if set(intents) != set(expected):
        raise UnknownSubmission("Exact confirmed initialization writes are required")
    for key, (method, path, checksum, response) in expected.items():
        row = intents[key]
        if (row.get("status") != "CONFIRMED" or row.get("method") != method or row.get("path") != path or row.get("digest") != checksum
                or any(type(row.get("response", {}).get(field)) is not type(value) or row["response"][field] != value
                       for field, value in response.items())):
            raise UnknownSubmission("Original initialization mutation is unknown or changed")


def text(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise adapters.Blocked("Fixed workflow text exceeds its bound")
    return {"sha256": text_digest(value), "declared": value if value in TOKENS else None}


def trace(platform, run_id, model):
    value = platform.get(f"/api/v1/resource-runs/workflow/{run_id}/configurations")
    if value.get("configuration_scope") != {"mode": "workflow", "run_id": run_id} or value.get("run_id") != run_id:
        raise adapters.Blocked("Native model trace belongs to a different root")
    rows = [item for item in value["items"] if item["resource_kind"] == "model"]
    if len(rows) != 1 or rows[0]["resource_id"] != model:
        raise adapters.Blocked("Exactly one real model snapshot is required")
    row = rows[0]
    return {"snapshot_id": row["snapshot_id"], "phase": row["phase"], "revision": row["revision"],
            "state": row["state"], "used": row["used_at"] is not None, "model_id": row["resource_id"],
            "temperature": row["snapshot"].get("temperature"),
            "calls": [{**{key: call[key] for key in ("call_id", "run_id", "mode", "model_id", "model_revision", "status",
                                                    "input_tokens", "output_tokens")},
                       "effective_parameters": {"temperature": call["effective_parameters"].get("temperature")}}
                      for call in row["calls"]]}


def collect(platform, fixture, item, run_id, *, published=False, case):
    base = "/api/v1/workflows/" + item["id"]
    version = platform.get(base + f"/versions/{item['version']}")
    if (version["id"] != item["version_id"] or version["workflow_id"] != item["id"] or version["model_id"] != fixture["model_id"]
            or version["active"] is not True or digest(version["definition"]) != digest(item["definition"])
            or version["digest"] != item["version_digest"] or type(version["draft_revision"]) is not int
            or version["draft_revision"] != item["revision"]):
        raise adapters.Blocked("Official immutable version differs")
    rows = observation.complete_items(platform.get(base + "/runs", params={"run_id": run_id}))
    if len(rows) != 1 or rows[0]["id"] != run_id or rows[0]["workflow_id"] != item["id"]:
        raise adapters.Blocked("Exact owned formal run is missing")
    row = rows[0]
    expected = {"definition": item["definition"], "revision": item["revision"], "model_id": fixture["model_id"]}
    snapshot = row["snapshot"]
    if (any(type(snapshot.get(key)) is not type(value) or digest(snapshot.get(key)) != digest(value) for key, value in expected.items())
            or type(snapshot.get("published_version")) is not (int if published else type(None))
            or snapshot.get("published_version") != (item["version"] if published else None)):
        raise adapters.Blocked("Formal execution business snapshot differs")
    events = platform.get(base + f"/runs/{run_id}/events")
    if set(events) != {"items"} or not isinstance(events["items"], list) or not 1 <= len(events["items"]) <= 16:
        raise adapters.Blocked("Bounded complete workflow events are required")
    projected = [{"event_id": event["event_id"], "node_id": event["node_id"], "event": event["event"],
                  "kind": event["data"].get("kind"), "input": text(event["data"].get("input")),
                  "output": text(event["data"].get("output")), "error_code": event["data"].get("error_code")}
                 for event in events["items"]]
    usage = require_usage(platform, run_id, case)
    return {"id": run_id, "workflow_id": item["id"], "version_id": item["version_id"], "version": item["version"] if published else None,
            "revision": item["revision"], "definition_digest": digest(item["definition"]), "business_digest": item["version_digest"],
            "status": row["status"], "error_node": row.get("error_node"), "input": text(row["input"]), "output": text(row["output"]),
            "events": projected, "usage": usage, "trace": trace(platform, run_id, fixture["model_id"]) if case == "AET-F01" else None}


def require_usage(platform, run_id, case):
    native = platform.get(f"/api/v1/agent-evaluations/workflow-runs/{run_id}/usage")
    expected = dict.fromkeys(observation.TOTAL, 0)
    if case == "AET-F01":
        expected.update(calls=1, reported_calls=1, input_tokens=10, output_tokens=5)
    value = native.get("total", {})
    if (native.get("complete") is not True or native.get("scope") != {"mode": "workflow", "run_id": run_id}
            or set(value) != observation.TOTAL or any(type(count) is not int or count < 0 for count in value.values())
            or value != expected):
        raise adapters.Blocked("Complete exact native usage is required before any subsequent mutation")
    return value


def retain(journal, attempt, stage, body):
    try:
        value = {"schema_version": 1, "kind": "versioned-" + stage, "binding": binding(journal.data, attempt), "body": body}
        if stage == "facts":
            value["usage"] = aggregate(body)
        reference = store(journal.directory, value["kind"], value)
        attempt.setdefault("evidence_refs", []).append(reference)
        attempt["versioned_" + stage + "_ref"] = reference
        journal.save()
    except BaseException:
        attempt["versioned_evidence_failed"] = True
        raise
    return value


def read(directory, data, attempt, stage):
    if attempt.get("versioned_evidence_failed"):
        raise adapters.Blocked("Failed evidence persistence blocks further effects")
    reference = attempt["versioned_" + stage + "_ref"]
    kind = "versioned-" + stage
    value = load(directory, reference, kind)
    if (set(value) != {"schema_version", "kind", "binding", "body"} | ({"usage"} if stage == "facts" else set())
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["kind"] != kind
            or digest(value["binding"]) != digest(binding(data, attempt))):
        raise ValueError("Versioned workflow evidence binding changed")
    return value


def effects(platform, fixture, specimen):
    return observation.effects(platform, fixture, specimen)


def baseline(platform, journal, attempt, specimen):
    if attempt.get("versioned_evidence_failed"):
        raise adapters.Blocked("Failed evidence persistence blocks further effects")
    if attempt.get("versioned_baseline_ref"):
        value = read(journal.directory, journal.data, attempt, "baseline")
    else:
        fixture = attempt["fixture"]
        body = {"initialization": [collect(platform, fixture, item, item["init_run_id"], case=specimen.case_id)
                                   for item in fixture["versions"]], "effects": effects(platform, fixture, specimen)}
        validate_baseline(body, journal.data, attempt, specimen)
        value = retain(journal, attempt, "baseline", body)
    validate_baseline(value["body"], journal.data, attempt, specimen)
    return value["body"]


def selection(platform, fixture):
    value = platform.get(f"/api/v1/workflows/{fixture['id']}/versions")
    if (type(value.get("current_published_version")) is not int or value["current_published_version"] != 1
            or type(value.get("release_revision")) is not int or value["release_revision"] != 4
            or not isinstance(value.get("items"), list) or len(value["items"]) != 2):
        raise adapters.Blocked("Current v1 pointer must be read from the official version list")
    for item in fixture["versions"]:
        row = next(row for row in value["items"] if row["version"] == item["version"])
        if (row["id"] != item["version_id"] or row["definition"] != item["definition"] or row["active"] is not True
                or row["digest"] != item["version_digest"] or row["is_current"] is not (item["version"] == 1)):
            raise ValueError("Current selection or immutable alternatives changed")
    return {"source": f"/api/v1/workflows/{fixture['id']}/versions", "current_published_version": 1,
            "release_revision": value["release_revision"], "version_ids": [item["version_id"] for item in fixture["versions"]]}


def submit_failure(platform, journal, attempt, item, identifier):
    path = f"/api/v1/workflows/{item['id']}/versions/{item['version']}/runs"
    body = {"text": "{}", "run_id": identifier}
    intents = attempt.setdefault("submission_intents", {})
    previous = intents.get("submit-failure")
    if previous:
        validate_intent(previous, path, body, identifier, failed=True)
        return
    platform.attest()
    intents["submit-failure"] = {"status": "SUBMITTING", "path": path, "payload_digest": digest(body)}
    journal.save()
    try:
        response = platform.post(path, json=body)
        value = response.json()
        if response.status_code != 502 or value.get("error", {}).get("code") != "WORKFLOW_RUN_FAILED":
            raise UnknownSubmission("Expected known failure response was not observed")
        rows = observation.complete_items(platform.get(f"/api/v1/workflows/{item['id']}/runs", params={"run_id": identifier}))
        events = platform.get(f"/api/v1/workflows/{item['id']}/runs/{identifier}/events")
        failed = {(row["node_id"], row["data"].get("kind"), row["data"].get("error_code")) for row in events["items"]
                  if row["event"] == "node.failed"}
        if (len(rows) != 1 or rows[0]["id"] != identifier or rows[0]["workflow_id"] != item["id"]
                or rows[0]["status"] != "FAILED" or rows[0].get("error_node") != "action"
                or failed != {("action.0.action", "transform", "NODE_EXECUTION_FAILED"),
                              ("action", "subworkflow", "NODE_EXECUTION_FAILED")}):
            raise UnknownSubmission("502 is not corroborated by the exact persisted nested failure")
    except BaseException:
        intents["submit-failure"]["status"] = "UNKNOWN"
        journal.save()
        raise
    intents["submit-failure"].update(status="CONFIRMED", http_status=502, error_code="WORKFLOW_RUN_FAILED",
                                     response={"id": identifier, "status": "FAILED"})
    journal.save()


def validate_intent(row, path, payload, identifier, *, failed=False):
    if (row.get("status") != "CONFIRMED" or row.get("path") != path or row.get("payload_digest") != digest(payload)
            or row.get("response", {}).get("id") != identifier
            or failed and (row.get("http_status") != 502 or row.get("error_code") != "WORKFLOW_RUN_FAILED"
                           or row.get("response", {}).get("status") != "FAILED")):
        raise UnknownSubmission("Unconfirmed or changed workflow submission cannot be replayed")


def execute(platform, journal, attempt, specimen, *, cancelled=lambda: False):
    before = baseline(platform, journal, attempt, specimen)
    if attempt.get("versioned_facts_ref"):
        return verified(journal.directory, journal.data, attempt, specimen)
    fixture = attempt["fixture"]
    if not attempt.get("submission_intents") and effects(platform, fixture, specimen) != before["effects"]:
        raise adapters.Blocked("Owned target changed before dispatch")
    if specimen.case_id == "AET-F01":
        if not attempt.get("versioned_selection_ref"):
            retain(journal, attempt, "selection", selection(platform, fixture))
        selected = read(journal.directory, journal.data, attempt, "selection")["body"]
        if selection(platform, fixture) != selected:
            raise adapters.Blocked("Current publication pointer changed")
        items = fixture["versions"]
    else:
        items = fixture["versions"][-1:]
    rows = []
    for index, item in enumerate(items):
        if cancelled():
            raise adapters.Blocked("Cancelled before another fixed root")
        identifier = attempt.setdefault("versioned_run_ids", {}).setdefault(str(index), str(uuid4()))
        journal.save()
        if specimen.case_id == "AET-F03":
            submit_failure(platform, journal, attempt, item, identifier)
        else:
            payload = {"text": INPUT, "run_id": identifier}
            path = f"/api/v1/workflows/{item['id']}/versions/{item['version']}/runs"
            key = "submit-v" + str(item["version"])
            if key in attempt.get("submission_intents", {}):
                validate_intent(attempt["submission_intents"][key], path, payload, identifier)
            returned = journal.post(platform, attempt, path, payload, key)
            if returned.get("id") != identifier:
                raise UnknownSubmission("Workflow returned a different root")
        root = {"mode": "workflow", "id": identifier, "fixture_id": item["id"], "path": f"/api/v1/workflows/{item['id']}/runs"}
        attempt.update(platform_root=root, started=True, submission_complete=True)
        if index == 0 and len(items) > 1:
            attempt.setdefault("prior_run_refs", {})["current-v1"] = root
        journal.save()
        row = collect(platform, fixture, item, identifier, published=True, case=specimen.case_id)
        validate_record(row, fixture, item, specimen.case_id, index, published=True)
        rows.append(row)
        if not sequence(row, specimen.case_id, index, published=True):
            attempt["fact_results"] = [Result(registered_id=specimen.validators[0].registered_id, status="FAIL").model_dump()]
            journal.save()
    body = {"baseline": before, "execution": rows, "selection": read(journal.directory, journal.data, attempt, "selection")["body"]
            if specimen.case_id == "AET-F01" else None, "after": effects(platform, fixture, specimen)}
    retain(journal, attempt, "facts", body)
    return verified(journal.directory, journal.data, attempt, specimen)


def event_spec(case, index, *, published=False):
    if case == "AET-F01":
        out = "MIXED" if index == 0 else "mixed"
        nodes = [("start", "start", INPUT, INPUT), ("action", "llm", INPUT, out), ("end", "end", out, out)]
    elif published:
        return [("start", "start", "node.started", "{}", None), ("start", "start", "node.completed", "{}", None),
                ("action", "subworkflow", "node.started", "{}", None),
                ("action.0.start", "start", "node.started", "{}", None), ("action.0.start", "start", "node.completed", "{}", None),
                ("action.0.action", "transform", "node.started", "{}", None),
                ("action.0.action", "transform", "node.failed", None, "NODE_EXECUTION_FAILED"),
                ("action", "subworkflow", "node.failed", None, "NODE_EXECUTION_FAILED")]
    else:
        nodes = [("start", "start", GOOD, GOOD), ("action", "transform" if index == 0 else "subworkflow", GOOD, "ok"),
                 ("end", "end", "ok", "ok")]
        if index == 1:
            nodes = nodes[:2] + [("action.0.start", "start", GOOD, GOOD), ("action.0.action", "transform", GOOD, "ok"),
                                ("action.0.end", "end", "ok", "ok")] + nodes[2:]
    events = []
    for node, kind, source, out in nodes:
        events.extend([(node, kind, "node.started", source, None), (node, kind, "node.completed", out, None)])
    if case == "AET-F03" and index == 1:
        # A subworkflow completes after its nested nodes, not before them.
        parent_done = events.pop(3)
        events.insert(-2, parent_done)
    return events


def sequence(row, case, index, *, published=False):
    failed = case == "AET-F03" and published
    expected_input = INPUT if case == "AET-F01" else "{}" if failed else GOOD
    output = ("MIXED" if index == 0 else "mixed") if case == "AET-F01" else None if failed else "ok"
    if (row["status"] != ("FAILED" if failed else "SUCCEEDED") or row["error_node"] != ("action" if failed else None)
            or row["input"] != text(expected_input) or row["output"] != text(output)):
        return False
    expected = event_spec(case, index, published=published)
    return len(row["events"]) == len(expected) and all(
        (event["node_id"], event["kind"], event["event"], event["output" if event["event"] == "node.completed" else "input"], event["error_code"])
        == (node, kind, name, text(value), error)
        for event, (node, kind, name, value, error) in zip(row["events"], expected, strict=True))


def validate_record(row, fixture, item, case, index, *, published=False):
    UUID(row["id"])
    if (set(row) != {"id", "workflow_id", "version_id", "version", "revision", "definition_digest", "business_digest", "status",
                    "error_node", "input", "output", "events", "usage", "trace"}
            or row["workflow_id"] != item["id"] or row["version_id"] != item["version_id"]
            or type(row["version"]) is not (int if published else type(None))
            or row["version"] != (item["version"] if published else None)
            or type(row["revision"]) is not int or row["revision"] != item["revision"]
            or row["definition_digest"] != digest(item["definition"]) or row["business_digest"] != item["version_digest"]
            or row["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"} or set(row["usage"]) != observation.TOTAL
            or any(type(n) is not int or n < 0 for n in row["usage"].values())
            or not isinstance(row["events"], list) or not 1 <= len(row["events"]) <= 16):
        raise adapters.Blocked("Fixed workflow record is incomplete or mismatched")
    previous = 0
    for event in row["events"]:
        if (set(event) != {"event_id", "node_id", "event", "kind", "input", "output", "error_code"}
                or type(event["event_id"]) is not int or event["event_id"] <= previous):
            raise ValueError("Fixed events are unordered or incomplete")
        previous = event["event_id"]
    expected_usage = dict.fromkeys(observation.TOTAL, 0)
    if case == "AET-F01":
        expected_usage.update(calls=1, reported_calls=1, input_tokens=10, output_tokens=5)
        validate_trace(row["trace"], row["id"], fixture["model_id"], 2 if published else 1)
    elif row["trace"] is not None:
        raise ValueError("A zero-provider case cannot claim a model trace")
    if row["usage"] != expected_usage:
        raise adapters.Blocked("Fixed native usage is not complete and exact")


def validate_trace(value, run, model, revision):
    if (not isinstance(value, dict) or set(value) != {"snapshot_id", "phase", "revision", "state", "used", "model_id", "temperature", "calls"}
            or type(value["phase"]) is not int or value["phase"] != 1 or value["revision"] != str(revision)
            or type(value["revision"]) is not str or value["state"] != "used" or value["used"] is not True
            or value["model_id"] != model or value["temperature"] != (0.4 if revision == 2 else None)
            or not isinstance(value["calls"], list) or len(value["calls"]) != 1):
        raise adapters.Blocked("The actual used model revision/configuration differs")
    UUID(value["snapshot_id"])
    call = value["calls"][0]
    UUID(call["call_id"])
    if (set(call) != {"call_id", "run_id", "mode", "model_id", "model_revision", "status", "input_tokens", "output_tokens", "effective_parameters"}
            or call["run_id"] != run or call["mode"] != "workflow" or call["model_id"] != model
            or type(call["model_revision"]) is not int or call["model_revision"] != revision or call["status"] != "KNOWN"
            or type(call["input_tokens"]) is not int or call["input_tokens"] != 10
            or type(call["output_tokens"]) is not int or call["output_tokens"] != 5
            or call["effective_parameters"].get("temperature") != (0.4 if revision == 2 else None)):
        raise adapters.Blocked("Actual native call configuration differs from the used snapshot")


def validate_effects(value, data, attempt, specimen, records):
    fixture = attempt["fixture"]
    observation.validate_effects(value, fixture, specimen, data["environment_id"], data["owner_id"])
    calls = sum(row["usage"]["calls"] for row in records)
    roots = [row["id"] for row in records if row["workflow_id"] == fixture["id"]]
    if (value["namespace"] != attempt["sandbox_namespace"] or set(value["run_ids"]) != set(roots)
            or any(count != (len(records) if key == "workflow_runs" else sum(len(row["events"]) for row in records)
                             if key == "workflow_events" else calls if key == "model_calls" else 0)
                   for key, count in value["counts"].items())
            or value["provider"]["calls"] != calls or value["provider"]["pending"] != 0
            or value["provider"]["unknown_roots"] != 0 or value["sandbox"]["attempts"] != 0):
        raise adapters.Blocked("Additional, missing or unsettled owner effects exist")


def validate_baseline(body, data, attempt, specimen):
    fixture = attempt["fixture"]
    validate_fixture(fixture, specimen.case_id)
    validate_initialization(attempt, specimen)
    if set(body) != {"initialization", "effects"} or len(body["initialization"]) != 2:
        raise ValueError("Both real initialization roots are required")
    for index, (row, item) in enumerate(zip(body["initialization"], fixture["versions"], strict=True)):
        validate_record(row, fixture, item, specimen.case_id, index)
        if row["id"] != item["init_run_id"] or not sequence(row, specimen.case_id, index):
            raise adapters.Blocked("Fixed initialization did not actually succeed")
    validate_effects(body["effects"], data, attempt, specimen, body["initialization"])


def aggregate(body):
    rows = [*body["baseline"]["initialization"], *body["execution"]]
    return {key: sum(row["usage"][key] for row in rows) for key in observation.TOTAL}


def evaluate(snapshot, specimen, attempt):
    body, fixture, case = snapshot["body"], attempt["fixture"], specimen.case_id
    validate_baseline(body["baseline"], snapshot["binding"], attempt, specimen)
    if set(body) != {"baseline", "execution", "selection", "after"} or len(body["execution"]) != (2 if case == "AET-F01" else 1):
        raise ValueError("All required main roots must be retained")
    if case == "AET-F01":
        selected = body["selection"]
        if (set(selected) != {"source", "current_published_version", "release_revision", "version_ids"}
                or type(selected["current_published_version"]) is not int or selected["current_published_version"] != 1
                or type(selected["release_revision"]) is not int or selected["release_revision"] != 4
                or selected["source"] != f"/api/v1/workflows/{fixture['id']}/versions"
                or selected["version_ids"] != [row["version_id"] for row in fixture["versions"]]):
            raise ValueError("Official current selection proof changed")
    elif body["selection"] is not None:
        raise ValueError("Unexpected selection in F03")
    items = fixture["versions"] if case == "AET-F01" else fixture["versions"][-1:]
    passed = True
    for index, (row, item) in enumerate(zip(body["execution"], items, strict=True)):
        validate_record(row, fixture, item, case, index, published=True)
        path = f"/api/v1/workflows/{item['id']}/versions/{item['version']}/runs"
        key = "submit-v" + str(item["version"]) if case == "AET-F01" else "submit-failure"
        if attempt["versioned_run_ids"].get(str(index)) != row["id"]:
            raise ValueError("The original submission root changed")
        validate_intent(attempt["submission_intents"][key], path, {"run_id": row["id"], "text": INPUT if case == "AET-F01" else "{}"},
                        row["id"], failed=case == "AET-F03")
        passed = passed and sequence(row, case, index, published=True)
    last = body["execution"][-1]
    if attempt.get("platform_root") != {"mode": "workflow", "id": last["id"], "fixture_id": fixture["id"],
                                       "path": f"/api/v1/workflows/{fixture['id']}/runs"}:
        raise ValueError("Main platform root changed")
    records = [*body["baseline"]["initialization"], *body["execution"]]
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("Initialization cannot substitute a main execution")
    validate_effects(body["after"], snapshot["binding"], attempt, specimen, records)
    usage = aggregate(body)
    if digest(snapshot["usage"]) != digest(usage):
        raise ValueError("Aggregate native usage changed")
    evidence = {"run": {"status": last["status"]}, "usage": {"total": usage}, "observed": ["usage"],
                "business_outcomes": [{"run_id": row["id"], "status": row["status"]} for row in body["execution"]]}
    if case == "AET-F03":
        evidence["expected_failure_control"] = passed
    return [Result(registered_id=specimen.validators[0].registered_id, status="PASS" if passed else "FAIL")], evidence


def verified(directory, data, attempt, specimen):
    stages = ["baseline", *( ["selection"] if specimen.case_id == "AET-F01" else []), "facts"]
    references = [attempt.get("versioned_" + stage + "_ref") for stage in stages]
    if not all(references) or attempt.get("evidence_refs") != references:
        raise adapters.Blocked("Exact complete sealed workflow evidence is required")
    values = {stage: read(directory, data, attempt, stage) for stage in stages}
    final = values["facts"]
    if (digest(final["body"]["baseline"]) != digest(values["baseline"]["body"])
            or "selection" in values and digest(final["body"]["selection"]) != digest(values["selection"]["body"])):
        raise ValueError("Pre-dispatch evidence was replaced")
    evaluate(final, specimen, attempt)
    return final


def cleanup_ready(directory, data, attempt, specimen):
    verified(directory, data, attempt, specimen)
    return True


def cleanup(platform, journal, attempt, specimen):
    value = verified(journal.directory, journal.data, attempt, specimen)
    current = effects(platform, attempt["fixture"], specimen)
    return "CLEAN" if digest(current) == digest(value["body"]["after"]) else "NEEDS_REVIEW"
