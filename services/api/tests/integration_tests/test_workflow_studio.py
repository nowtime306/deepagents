"""Incomplete canvas drafts and real single-node previews preserve execution boundaries."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def draft():
    return {"nodes": [{"id": "start", "kind": "start"},
                      {"id": "upper", "kind": "transform", "parameters": {"operation": "upper"}},
                      {"id": "unconfigured", "kind": "rag"},
                      {"id": "end", "kind": "end"}], "edges": []}


def test_partial_draft_saves_but_cannot_run_or_publish_and_single_node_does_not_unlock_it(client):
    connection, _ = client
    body = {"name": "画布草稿-" + str(uuid4()), "model_id": "test", "definition": draft()}
    assert connection.post("/api/v1/workflows", json=body, headers=ORIGIN).status_code == 422
    result = connection.post("/api/v1/workflows", json={**body, "allow_incomplete": True}, headers=ORIGIN)
    assert result.status_code == 201
    prefix = "/api/v1/workflows/" + result.json()["id"]
    assert connection.post(prefix + "/test-runs", json={"text": "hello"}, headers=ORIGIN).status_code == 422
    tested = connection.post(prefix + "/nodes/upper/test-runs",
        json={"text": "hello", "revision": 1}, headers=ORIGIN)
    assert tested.status_code == 200 and tested.json()["output"] == "HELLO"
    run = connection.get(prefix + "/runs").json()["items"][0]
    assert run["snapshot"]["debug_node_id"] == "upper"
    events = connection.get(prefix + "/runs/" + run["id"] + "/events").json()["items"]
    assert any(event["node_id"] == "upper" and event["event"] == "node.completed" for event in events)
    assert not any(event["node_id"] == "unconfigured" for event in events)
    workflow = next(row for row in connection.get("/api/v1/workflows").json()["items"]
                    if row["id"] == result.json()["id"])
    assert workflow["tested_revision"] is None
    assert connection.post(prefix + "/publish", json={"revision": 1}, headers=ORIGIN).status_code == 422
    assert connection.post(prefix + "/nodes/upper/test-runs",
        json={"text": "hello", "revision": 2}, headers=ORIGIN).status_code == 409
    updated = {**body, "revision": 1, "allow_incomplete": True}
    assert connection.put(prefix, json=updated, headers=ORIGIN).status_code == 200
    assert connection.post(prefix + "/test-runs", json={"text": "hello", "revision": 1},
        headers=ORIGIN).status_code == 409
    username = "studio-private-" + str(uuid4())
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": username, "password": "integration-password"},
        headers=ORIGIN)
    assert connection.post(prefix + "/nodes/upper/test-runs",
        json={"text": "hello", "revision": 2}, headers=ORIGIN).status_code == 404
    assert connection.get(prefix + "/runs/" + run["id"] + "/events").status_code == 404


def test_preview_condition_start_end_failure_and_work_nodes_use_real_events(client):
    connection, _ = client
    definition = {"execution": "work", "nodes": [
        {"id": "start", "kind": "start"}, {"id": "end", "kind": "end", "input": "missing"},
        {"id": "choose", "kind": "condition", "parameters": {"operator": "contains", "value": "yes"}},
        {"id": "broken", "kind": "transform", "parameters": {"operation": "json_get", "path": "missing"}},
        {"id": "code", "kind": "code", "parameters": {"code": "print('must not execute')"}},
        {"id": "approve", "kind": "approval", "parameters": {"prompt": "Must approve"}}], "edges": []}
    created = connection.post("/api/v1/workflows", json={"name": "单步边界-" + str(uuid4()),
        "model_id": "test", "definition": definition, "allow_incomplete": True}, headers=ORIGIN)
    assert created.status_code == 201
    prefix = "/api/v1/workflows/" + created.json()["id"]
    for node, text, expected in [("start", "hello", "hello"), ("end", "hello", "hello"),
                                ("choose", "yes", "true"), ("choose", "no", "false")]:
        response = connection.post(prefix + f"/nodes/{node}/test-runs",
            json={"text": text, "revision": 1}, headers=ORIGIN)
        assert response.status_code == 200 and response.json()["output"] == expected
    for node in ("code", "approve"):
        response = connection.post(prefix + f"/nodes/{node}/test-runs",
            json={"text": "hello", "revision": 1}, headers=ORIGIN)
        assert response.status_code == 503 and response.json()["error"]["code"] == "WORKER_OFFLINE"
    for node in ("absent",):
        assert connection.post(prefix + f"/nodes/{node}/test-runs",
            json={"text": "hello", "revision": 1}, headers=ORIGIN).status_code == 422
    assert connection.post(prefix + "/nodes/broken/test-runs",
        json={"text": "{}", "revision": 1}, headers=ORIGIN).status_code == 502
    failed = connection.get(prefix + "/runs").json()["items"][0]
    assert failed["status"] == "FAILED" and failed["error_node"] == "broken"
    events = connection.get(prefix + "/runs/" + failed["id"] + "/events").json()["items"]
    event = next(event for event in events if event["event"] == "node.failed")
    assert event["data"]["duration_ms"] >= 0


def test_client_run_identifier_correlates_live_events_and_duplicate_request_never_reexecutes(client):
    connection, _ = client
    graph = {"nodes": [{"id": "start", "kind": "start"}, {"id": "end", "kind": "end"}],
             "edges": [{"source": "start", "target": "end"}]}
    created = connection.post("/api/v1/workflows", json={"name": "实时定位-" + str(uuid4()),
        "model_id": "test", "definition": graph}, headers=ORIGIN)
    assert created.status_code == 201
    prefix = "/api/v1/workflows/" + created.json()["id"]
    identifier = str(uuid4())
    response = connection.post(prefix + "/test-runs", json={"text": "first", "run_id": identifier,
        "revision": 1}, headers=ORIGIN)
    assert response.status_code == 200 and response.json()["id"] == identifier
    events = connection.get(prefix + "/runs/" + identifier + "/events").json()["items"]
    assert len(events) == 4 and {event["node_id"] for event in events} == {"start", "end"}
    duplicate = connection.post(prefix + "/test-runs", json={"text": "second", "run_id": identifier,
        "revision": 1}, headers=ORIGIN)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "WORKFLOW_RUN_EXISTS"
    assert connection.get(prefix + "/runs/" + identifier + "/events").json()["items"] == events
    assert connection.get(prefix + "/runs").json()["items"][0]["input"] == "first"
