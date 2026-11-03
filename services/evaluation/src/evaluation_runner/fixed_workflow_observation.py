"""Formal, bounded observations for the two fixed readonly Workflow cases."""

from uuid import UUID

from . import adapters
from .fixed_evidence import SHA, text_digest
from .journal import digest

CASES = {"AET-F02", "AET-F04"}
COUNTS = {"workflow_runs", "workflow_events", "ask_runs", "work_tasks", "work_runs", "model_calls", "operations",
          "sandbox_sessions", "active_workflow_runs", "active_ask_runs", "active_work_runs", "active_sandboxes",
          "unknown_operations", "unknown_model_usage"}
TOKENS = {"hello", "HELLO", "true", "false", "bye", '{"name":4}', '{"name":"李四"}'}
TOTAL = {"calls", "reported_calls", "unknown_calls", "pending_calls", "input_tokens", "output_tokens"}


def definition(case_id):
    if case_id not in CASES:
        raise ValueError("Unregistered readonly workflow case")
    nodes = [{"id": "start", "kind": "start"}]
    edges = []
    if case_id == "AET-F02":
        nodes += [{"id": "upper", "kind": "transform", "input": "start", "parameters": {"operation": "upper"}},
                  {"id": "choose", "kind": "condition", "input": "upper", "parameters": {"operator": "contains", "value": "HELLO"}},
                  {"id": "yes", "kind": "end", "input": "upper"}, {"id": "no", "kind": "end"}]
        edges = [{"source": "start", "target": "upper"}, {"source": "upper", "target": "choose"},
                 {"source": "choose", "target": "yes", "case": "true"}, {"source": "choose", "target": "no", "case": "false"}]
    else:
        nodes += [{"id": "end", "kind": "end", "input": "start"}]
        edges = [{"source": "start", "target": "end"}]
    result = {"schema_version": 1, "execution": "readonly", "input_schema": {"type": "string"},
              "output_schema": {"type": "string"}, "node_budget": 30, "model_calls": 20, "token_limit": 128000,
              "nodes": [{"name": None, "input": "input", "parameters": {}, **node} for node in nodes],
              "edges": [{"case": None, **edge} for edge in edges]}
    if case_id == "AET-F04":
        result["input_schema"] = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 12}}, "required": ["name"]}
    return result


def projected_text(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 4096:
        raise adapters.Blocked("Readonly observation exceeds its fixed text bound")
    return {"sha256": text_digest(value), "declared": value if value in TOKENS else None}


def complete_items(response):
    if (not isinstance(response.get("items"), list) or "next_cursor" not in response
            or response["next_cursor"] is not None or len(response["items"]) > 16):
        raise adapters.Blocked("A complete bounded formal run collection is required")
    return response["items"]


def effects(platform, fixture, specimen):
    value = platform.get(f"/api/v1/agent-evaluations/profiles/workflows/{fixture['id']}/observations")
    validate_effects(value, fixture, specimen, platform.target.environment_id, platform.target.owner_id)
    return value


def validate_effects(value, fixture, specimen, environment_id, owner_id):
    if (set(value) != {"schema_version", "environment_id", "owner_id", "namespace", "case_id", "workflow_id", "complete",
                       "run_ids", "provider", "sandbox", "counts"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["environment_id"] != environment_id or value["owner_id"] != owner_id
            or value["case_id"] != specimen.case_id or value["workflow_id"] != fixture["id"]
            or value["complete"] is not True or set(value["counts"]) != COUNTS
            or any(type(count) is not int or count < 0 for count in value["counts"].values())
            or not isinstance(value["run_ids"], list) or len(value["run_ids"]) > 16
            or len(value["run_ids"]) != len(set(value["run_ids"]))):
        raise adapters.Blocked("Owned workflow effect evidence is incomplete or mismatched")
    UUID(value["namespace"])
    for identifier in value["run_ids"]:
        UUID(identifier)
    for name, keys in (("provider", {"observed", "complete", "calls", "pending", "unknown_roots"}),
                       ("sandbox", {"observed", "complete", "attempts"})):
        row = value[name]
        if (set(row) != keys or row["observed"] is not True or row["complete"] is not True
                or any(type(row[key]) is not int or row[key] < 0 for key in keys - {"observed", "complete"})):
            raise adapters.Blocked("Provider/sandbox observations are incomplete")


def collect(platform, fixture, specimen, run_id, *, published=True):
    identifier = str(UUID(run_id))
    base = f"/api/v1/workflows/{fixture['id']}"
    version = platform.get(base + f"/versions/{fixture['version']}")
    if (version["id"] != fixture["version_id"] or version["workflow_id"] != fixture["id"]
            or type(version["version"]) is not int or version["version"] != fixture["version"]
            or version["model_id"] != fixture["model_id"] or version["active"] is not True
            or type(version["draft_revision"]) is not int
            or version["definition"] != definition(specimen.case_id) or not SHA.fullmatch(version["digest"])
            or version["digest"] != fixture["version_digest"]):
        raise adapters.Blocked("Published workflow is not the fixed reviewed definition")
    rows = complete_items(platform.get(base + "/runs", params={"run_id": identifier}))
    if len(rows) != 1 or rows[0]["id"] != identifier or rows[0]["workflow_id"] != fixture["id"]:
        raise adapters.Blocked("Official workflow run identity is unavailable")
    row = rows[0]
    snapshot = row["snapshot"]
    if (snapshot["definition"] != definition(specimen.case_id) or snapshot["model_id"] != fixture["model_id"]
            or type(snapshot["revision"]) is not int or snapshot["revision"] != version["draft_revision"]
            or published and type(snapshot.get("published_version")) is not int
            or snapshot.get("published_version") != (fixture["version"] if published else None)):
        raise adapters.Blocked("Workflow run business snapshot differs")
    native = platform.get(f"/api/v1/agent-evaluations/workflow-runs/{identifier}/usage")
    if native.get("complete") is not True or native.get("scope") != {"mode": "workflow", "run_id": identifier}:
        raise adapters.Blocked("Complete standalone native usage is required")
    usage = adapters.known_usage({"observed": ["usage"], "usage": native}, specimen)
    if set(usage) != TOTAL:
        raise adapters.Blocked("Unexpected native usage fields")
    events = platform.get(base + f"/runs/{identifier}/events")
    if set(events) != {"items"} or not isinstance(events["items"], list) or not 1 <= len(events["items"]) <= 64:
        raise adapters.Blocked("Complete bounded node events are required")
    nodes = {node["id"]: node["kind"] for node in definition(specimen.case_id)["nodes"]}
    projected = []
    for event in events["items"]:
        if (type(event["event_id"]) is not int or event["event_id"] <= 0 or event["node_id"] not in nodes
                or event["event"] not in {"node.started", "node.completed", "node.failed", "node.handled"}
                or event["data"].get("kind") != nodes[event["node_id"]]):
            raise adapters.Blocked("Unrecognized fixed workflow event")
        data = event["data"]
        projected.append({"event_id": event["event_id"], "node_id": event["node_id"], "event": event["event"],
                          "kind": nodes[event["node_id"]], "input": projected_text(data.get("input")),
                          "output": projected_text(data.get("output"))})
    return {"id": identifier, "workflow_id": fixture["id"], "status": row["status"], "input": projected_text(row["input"]),
            "output": projected_text(row["output"]), "source": base + "/runs", "events_source": base + f"/runs/{identifier}/events",
            "version_id": version["id"], "version": version["version"] if published else None,
            "definition_digest": digest(version["definition"]), "business_digest": version["digest"],
            "events": projected, "usage": usage}
