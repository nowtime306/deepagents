"""Configuration checks and bounded templates preserve execution and debug boundaries."""
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_workflow_composition import create, definition, run

from app.workflow_definition import Definition, render_template, validate


def report():
    return {"nodes": [
        {"id": "start", "kind": "start"},
        {"id": "upper", "kind": "transform", "parameters": {"operation": "upper"}},
        {"id": "trim", "kind": "transform", "input": "upper", "parameters": {"operation": "trim"}},
        {"id": "report", "kind": "transform", "input": "upper", "parameters": {
            "operation": "template", "template": "原文{{input}}；大写{{upper}}；清理{{trim}}；重复{{trim}}"}},
        {"id": "end", "kind": "end", "input": "report"}],
        "edges": [{"source": a, "target": b} for a, b in
                  [("start", "upper"), ("upper", "trim"), ("trim", "report"), ("report", "end")]]}


def test_validation_collects_errors_without_executing_or_writing(client):
    connection, app = client
    def counts():
        return connection.portal.call(app.state.db.fetch,
            "SELECT (SELECT count(*) FROM workflows) AS drafts, (SELECT count(*) FROM workflow_runs) AS runs, "
            "(SELECT count(*) FROM model_usage) AS usage")
    before = counts()
    graph = report()
    graph["nodes"][1]["parameters"]["operation"] = "invalid"
    graph["nodes"][2]["input"] = "future"
    response = connection.post("/api/v1/workflows/validate", headers=ORIGIN, json={"definition": graph})
    assert response.status_code == 200
    result = response.json()
    assert result["scope"] == "configuration" and result["valid"] is False
    assert {(item["code"], item["node_id"]) for item in result["issues"]} == {
        ("NODE_PARAMETERS", "upper"), ("VARIABLE_UNAVAILABLE", "trim")}
    assert counts() == before
    graph = report()
    assert connection.post("/api/v1/workflows/validate", headers=ORIGIN,
                           json={"definition": graph}).json()["valid"] is True
    assert counts() == before
    connection.cookies.clear()
    assert connection.post("/api/v1/workflows/validate", headers=ORIGIN,
                           json={"definition": graph}).status_code == 401


def test_template_runs_and_published_version_preserves_root_and_upstream_values(client):
    connection, _ = client
    identifier = create(connection, report())
    expected = "原文 hello ；大写 HELLO ；清理HELLO；重复HELLO"
    result = run(connection, identifier, " hello ")
    assert result.status_code == 200 and result.json()["output"] == expected
    prefix = f"/api/v1/workflows/{identifier}"
    assert connection.post(prefix + "/publish", headers=ORIGIN, json={"revision": 1}).status_code == 201
    published = connection.post(prefix + "/versions/1/runs", headers=ORIGIN, json={"text": " hello "})
    assert published.status_code == 200 and published.json()["output"] == expected


def test_single_node_uses_explicit_variables_without_reinterpreting_values_or_unlocking_publish(client):
    connection, _ = client
    identifier = create(connection, report())
    prefix = f"/api/v1/workflows/{identifier}"
    body = {"text": "UPPER", "revision": 1, "variables": {"trim": "{{future}}"}}
    response = connection.post(prefix + "/nodes/report/test-runs", headers=ORIGIN, json=body)
    assert response.status_code == 422 and "input" in response.json()["error"]["message"]
    body["variables"]["input"] = "original"
    response = connection.post(prefix + "/nodes/report/test-runs", headers=ORIGIN, json=body)
    assert response.status_code == 200
    assert response.json()["output"] == "原文original；大写UPPER；清理{{future}}；重复{{future}}"
    events = connection.get(prefix + "/runs/" + response.json()["id"] + "/events").json()["items"]
    assert not any(event["node_id"] in {"upper", "trim"} for event in events)
    original = next(item for item in connection.get("/api/v1/workflows").json()["items"] if item["id"] == identifier)
    assert original["definition"]["nodes"][3]["parameters"] == report()["nodes"][3]["parameters"]
    assert original["tested_revision"] is None
    assert connection.post(prefix + "/publish", headers=ORIGIN, json={"revision": 1}).status_code == 409


@pytest.mark.parametrize("template", ["{{ input }}", "{{input.foo}}", "{{input", "input}}", "{{{input}}}",
                                    "{{input()}}", "{{env}}", "{{report}}", "{{end}}"])
def test_invalid_or_unavailable_template_references_block_real_execution(client, template):
    connection, _ = client
    graph = definition(operation="template", template=template)
    result = connection.post("/api/v1/workflows/validate", headers=ORIGIN, json={"definition": graph})
    assert result.status_code == 200 and not result.json()["valid"]
    response = connection.post("/api/v1/workflows", headers=ORIGIN,
        json={"name": "拒绝模板" + uuid4().hex, "model_id": "test", "definition": graph})
    assert response.status_code == 422


def test_template_limits_and_literal_values_are_checked_before_expansion():
    assert render_template("{普通}{{input}}", {"input": "{{missing}}"}) == "{普通}{{missing}}"
    assert render_template("{{input}}", {"input": "a" * 40000}) == "a" * 40000
    with pytest.raises(ValueError):
        render_template("{{input}}x", {"input": "a" * 40000})
    for template in ["a" * 12001, "{{input}}" * 101]:
        with pytest.raises(ValueError):
            render_template(template, {"input": ""})
    # A legacy reference to the actual start node remains valid.
    validate(Definition.model_validate(definition(operation="template", template="{{start}}")))


def test_template_cannot_read_an_optional_branch_output(client):
    connection, _ = client
    graph = {"nodes": [{"id": "start", "kind": "start"},
        {"id": "choice", "kind": "condition", "parameters": {"operator": "not_empty"}},
        {"id": "yes", "kind": "transform", "parameters": {"operation": "upper"}},
        {"id": "no", "kind": "transform", "parameters": {"operation": "lower"}},
        {"id": "join", "kind": "transform", "parameters": {"operation": "template", "template": "{{yes}}"}},
        {"id": "end", "kind": "end", "input": "join"}],
        "edges": [{"source": "start", "target": "choice"},
            {"source": "choice", "target": "yes", "case": "true"},
            {"source": "choice", "target": "no", "case": "false"},
            {"source": "yes", "target": "join"}, {"source": "no", "target": "join"},
            {"source": "join", "target": "end"}]}
    checked = connection.post("/api/v1/workflows/validate", headers=ORIGIN, json={"definition": graph})
    assert checked.status_code == 200 and checked.json()["issues"] == [{
        "code": "VARIABLE_UNAVAILABLE", "node_id": "join", "field": "parameters.template",
        "message": "节点 join 引用了尚不可用的变量 yes"}]
    with pytest.raises(ValueError):
        validate(Definition.model_validate(graph))


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires actual local Coding sandbox")
def test_template_executes_through_the_actual_durable_work_adapter(client):
    from opensandbox.config import ConnectionConfig
    from test_workflow_tasks import enable

    from app.durable_sandboxes import DurableSandboxes
    from app.work_artifacts import WorkArtifacts
    from app.work_engine import WorkEngine

    connection, app = client
    enable(connection, app)
    graph = {**report(), "execution": "work"}
    identifier = create(connection, graph)
    accepted = connection.post(f"/api/v1/workflows/{identifier}/work-runs", headers=ORIGIN,
                              json={"text": " hello ", "revision": 1})
    assert accepted.status_code == 202
    root = accepted.json()
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    service = DurableSandboxes(app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key,
                                                             use_server_proxy=True))
    artifacts = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")
    engine = WorkEngine(app.state.db, app.state.models, service, artifacts, app.state.runtime.saver,
                       app.state.runtime.model_factory, capabilities_factory=lambda _: app.state.capabilities)
    try:
        for _ in range(10):
            assert connection.portal.call(engine.events.tick)
            current = connection.get(f"/api/v1/work-runs/{root['run_id']}").json()
            if current["status"] != "PENDING" or current["error_code"] != "WORK_CAPACITY_WAIT":
                break
            time.sleep(5)
            connection.portal.call(app.state.db.execute,
                "UPDATE work_queue_runs SET available_after=now() WHERE id=%s AND status='PENDING'", (root["run_id"],))
        assert current["status"] == "COMPLETED", current
        rows = connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"]
        row = next(item for item in rows if item["id"] == root["workflow_run_id"])
        assert row["status"] == "SUCCEEDED" and row["output"] == "原文 hello ；大写 HELLO ；清理HELLO；重复HELLO"
        usage = connection.get(f"/api/v1/work-runs/{root['run_id']}/usage").json()["total"]
        assert usage["calls"] == 0
    finally:
        sessions = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (root["run_id"],))
        for session in sessions:
            connection.portal.call(service.release, session["id"])
        connection.portal.call(service.close)


@pytest.mark.parametrize("problem,node", [("disconnected", "upper"), ("condition", "action"),
                                         ("outgoing", "action"), ("dangling", "action")])
def test_known_structure_errors_locate_nodes_and_preserve_strict_rejection_without_writes(client, problem, node):
    connection, app = client
    graph = definition(operation="upper")
    if problem == "disconnected":
        graph = report()
        graph["edges"][0]["target"] = "trim"
    elif problem == "condition":
        graph["nodes"][1].update(kind="condition", parameters={"operator": "not_empty"})
        graph["edges"][1]["case"] = "true"
    elif problem == "outgoing":
        graph["edges"].pop()
    else:
        graph["edges"][1]["target"] = "missing"
    query = "SELECT (SELECT count(*) FROM workflow_runs) AS runs, (SELECT count(*) FROM model_usage) AS usage"
    before = connection.portal.call(app.state.db.fetch, query)
    response = connection.post("/api/v1/workflows/validate", headers=ORIGIN, json={"definition": graph})
    assert response.status_code == 200
    issue = response.json()["issues"][0]
    assert not response.json()["valid"]
    assert issue["code"] == "WORKFLOW_STRUCTURE" and issue["node_id"] == node and issue["field"] == "edges"
    with pytest.raises(ValueError):
        validate(Definition.model_validate(graph))
    assert connection.portal.call(app.state.db.fetch, query) == before


@pytest.mark.parametrize("kind,parameters", [("http", {"url": "  "}),
    ("mcp", {"server_id": str(uuid4()), "tool": "", "arguments": {}})])
def test_blank_required_resource_parameters_are_configuration_errors(client, kind, parameters):
    connection, _ = client
    graph = definition(kind, **parameters)
    checked = connection.post("/api/v1/workflows/validate", headers=ORIGIN, json={"definition": graph})
    assert checked.status_code == 200 and checked.json()["issues"][0]["node_id"] == "action"
    assert checked.json()["issues"][0]["code"] == "NODE_PARAMETERS"
    with pytest.raises(ValueError):
        validate(Definition.model_validate(graph))
