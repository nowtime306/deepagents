"""Real API/PG proof of one shared nested node budget; not the formal F06 sample."""

import json

from test_api import client as client
from test_workflow_composition import create, definition, publish, run


def test_parallel_children_spend_the_same_parent_node_budget(client):
    connection, app = client
    calls = []

    def forbidden_model(_):
        calls.append("model")
        raise AssertionError("Fixed transform/parallel contracts must not invoke a model")

    app.state.runtime.model_factory = forbidden_model
    _, lower = publish(connection, definition(operation="lower"))
    _, upper = publish(connection, definition(operation="upper"))
    graph = definition("parallel", version_ids=[lower, upper, lower])
    good = create(connection, {**graph, "node_budget": 12})
    response = run(connection, good, "Mixed")
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["output"]) == ["mixed", "MIXED", "mixed"]
    root = response.json()["id"]
    events = connection.get(f"/api/v1/workflows/{good}/runs/{root}/events").json()["items"]
    started = [row for row in events if row["event"] == "node.started"]
    assert len(started) == 12
    assert {row["node_id"] for row in started} == {"start", "action", "end"} | {
        f"action.{index}.{node}" for index in range(3) for node in ("start", "action", "end")}

    short = create(connection, {**graph, "node_budget": 11})
    failed = run(connection, short, "Mixed")
    assert failed.status_code == 422, failed.text
    assert failed.json()["error"]["code"] == "WORKFLOW_NODE_BUDGET_EXCEEDED"
    rows = connection.get(f"/api/v1/workflows/{short}/runs").json()
    assert rows["next_cursor"] is None and len(rows["items"]) == 1
    row = rows["items"][0]
    assert row["status"] == "FAILED" and row["output"] is None
    events = connection.get(f"/api/v1/workflows/{short}/runs/{row['id']}/events").json()["items"]
    assert len([event for event in events if event["event"] == "node.started"]) == 11
    assert not any(event["node_id"] == "end" and event["event"] == "node.completed" for event in events)
    assert calls == []
    owner = connection.get("/api/v1/auth/me").json()["id"]
    counts = connection.portal.call(app.state.db.fetch,
        "SELECT (SELECT count(*) FROM model_usage WHERE owner_id=%s) AS model_calls,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s) AS work_runs,"
        "(SELECT count(*) FROM work_sandbox_sessions WHERE owner_id=%s) AS sessions", (owner, owner, owner))[0]
    assert counts == {"model_calls": 0, "work_runs": 0, "sessions": 0}
