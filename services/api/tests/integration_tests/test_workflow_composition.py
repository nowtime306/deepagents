"""Published read-only composition retains order, ownership and shared limits."""

import json
from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client


def definition(kind="transform", **parameters):
    return {
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "action", "kind": kind, "parameters": parameters or {"operation": "upper"}},
            {"id": "end", "kind": "end", "input": "action"},
        ],
        "edges": [{"source": "start", "target": "action"}, {"source": "action", "target": "end"}],
    }


def create(connection, graph):
    response = connection.post(
        "/api/v1/workflows",
        headers=ORIGIN,
        json={
            "name": "组合-" + uuid4().hex,
            "model_id": "test",
            "definition": graph,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def run(connection, identifier, text):
    return connection.post(f"/api/v1/workflows/{identifier}/test-runs", json={"text": text}, headers=ORIGIN)


def publish(connection, graph):
    identifier = create(connection, graph)
    assert run(connection, identifier, "hello").status_code == 200
    assert (
        connection.post(f"/api/v1/workflows/{identifier}/publish", json={"revision": 1}, headers=ORIGIN).status_code
        == 201
    )
    return identifier, connection.get(f"/api/v1/workflows/{identifier}/versions").json()["items"][0]["id"]


def test_iteration_parallel_order_pins_and_node_budget(client):
    connection, _ = client
    upper, upper_version = publish(connection, definition(operation="upper"))
    _, lower_version = publish(connection, definition(operation="lower"))
    loop = definition("iteration", version_id=upper_version, max_items=3)
    identifier = create(connection, loop)
    response = run(connection, identifier, '["hello", "World"]')
    assert response.status_code == 200, response.text
    assert json.loads(response.json()["output"]) == ["HELLO", "WORLD"]
    events = connection.get(f"/api/v1/workflows/{identifier}/runs/{response.json()['id']}/events").json()["items"]
    assert {"action.0.action", "action.1.action"} <= {item["node_id"] for item in events}
    assert run(connection, identifier, "[1,2,3,4]").status_code == 502
    parallel = create(connection, definition("parallel", version_ids=[lower_version, upper_version, lower_version]))
    result = run(connection, parallel, "Mixed")
    assert result.status_code == 200, result.text
    assert json.loads(result.json()["output"]) == ["mixed", "MIXED", "mixed"]
    budgeted = create(connection, {**loop, "node_budget": 5})
    assert run(connection, budgeted, '["a", "b"]').status_code in {422, 502}
    connection.put(f"/api/v1/workflows/{upper}/versions/1/state", json={"active": False}, headers=ORIGIN)
    assert run(connection, identifier, '["hello"]').status_code == 403


def test_structured_input_rejected_before_run_and_output_checked(client):
    connection, app = client
    graph = definition(operation="text")
    schema = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 12}}, "required": ["name"]}
    identifier = create(connection, {**graph, "input_schema": schema, "output_schema": schema})
    assert run(connection, identifier, '{"name":"李四"}').status_code == 200
    assert run(connection, identifier, '{"name":4}').status_code == 422
    assert len(connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"]) == 1
    bad_output = create(connection, {**graph, "output_schema": {"type": "integer"}})
    assert run(connection, bad_output, "hello").status_code == 422
    response = connection.post(
        "/api/v1/workflows",
        headers=ORIGIN,
        json={
            "name": "坏 Schema",
            "model_id": "test",
            "definition": {**graph, "input_schema": {"type": "object", "$ref": "https://example.com"}},
        },
    )
    assert response.status_code == 422


def test_nested_depth_limit_and_foreign_version(client):
    from test_api import DATABASE, add_user, run_async

    connection, _ = client
    _, leaf = publish(connection, definition())
    _, middle = publish(connection, definition("subworkflow", version_id=leaf))
    _, outer = publish(connection, definition("subworkflow", version_id=middle))
    response = connection.post(
        "/api/v1/workflows",
        headers=ORIGIN,
        json={
            "name": "过深",
            "model_id": "test",
            "definition": definition("subworkflow", version_id=outer),
        },
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "WORKFLOW_NESTING_LIMIT"
    username = "组合越权-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"username": username, "password": "integration-password"}
    )
    assert connection.get("/api/v1/workflows/options").json()["items"] == []
    response = connection.post(
        "/api/v1/workflows",
        headers=ORIGIN,
        json={
            "name": "越权复用",
            "model_id": "test",
            "definition": definition("subworkflow", version_id=leaf),
        },
    )
    assert response.status_code == 403


def test_actual_parallel_calls_share_one_atomic_root_budget(client):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.graphs import resolve_model
    from app.models import ModelInput

    connection, app = client
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            encoded = json.dumps(
                {
                    "id": "test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "local",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "known output"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = connection.portal.call(
            app.state.models.save,
            ModelInput(
                name="流程本机-" + uuid4().hex,
                model="local",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="local-only",
                max_output_tokens=128,
            ),
        )
        app.state.runtime.model_factory = resolve_model
        leaf_response = connection.post(
            "/api/v1/workflows",
            headers=ORIGIN,
            json={
                "name": "真实接口子流程-" + uuid4().hex,
                "model_id": model,
                "definition": definition("llm", model_id=model),
            },
        )
        leaf = leaf_response.json()["id"]
        assert run(connection, leaf, "hello").status_code == 200
        assert (
            connection.post(f"/api/v1/workflows/{leaf}/publish", headers=ORIGIN, json={"revision": 1}).status_code
            == 201
        )
        version = connection.get(f"/api/v1/workflows/{leaf}/versions").json()["items"][0]["id"]
        requests.clear()
        graph = {**definition("parallel", version_ids=[version, version]), "model_calls": 1}
        root = create(connection, graph)
        response = run(connection, root, "parallel")
        assert response.status_code == 502
        assert len(requests) <= 1
        run_id = connection.get(f"/api/v1/workflows/{root}/runs").json()["items"][0]["id"]
        rows = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (run_id,))
        assert len(rows) == 1 and rows[0]["model_id"] == model
        # Successful composition preserves actual per-call usage under the parent id.
        requests.clear()
        root = create(connection, {**graph, "model_calls": 2})
        response = run(connection, root, "parallel")
        assert response.status_code == 200, response.text
        assert len(requests) == 2 and json.loads(response.json()["output"]) == ["known output", "known output"]
        rows = connection.portal.call(
            app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (response.json()["id"],)
        )
        assert len(rows) == 2 and all(
            row["status"] == "KNOWN" and row["input_tokens"] == 7 and row["output_tokens"] == 3 for row in rows
        )
        from test_ask_workflows import tool_context

        from app.errors import APIError
        from app.workflow_compiler import WorkflowNodeFailed

        requests.clear()
        ask_run, _, tool = tool_context(connection, app, version)
        connection.portal.call(
            app.state.db.execute,
            "UPDATE task_runs SET harness_policy=jsonb_set(harness_policy,'{model_calls}','1') WHERE id=%s",
            (ask_run,),
        )
        first = json.loads(connection.portal.call(tool.ainvoke, {"version_id": version, "text": "shared ask"}))
        repeated = json.loads(connection.portal.call(tool.ainvoke, {"version_id": version, "text": "shared ask"}))
        assert repeated["reused"] and first["output"] == "known output" and len(requests) == 1
        with pytest.raises(WorkflowNodeFailed) as error:
            connection.portal.call(tool.ainvoke, {"version_id": version, "text": "second distinct input"})
        assert isinstance(error.value.__cause__, APIError) and error.value.__cause__.code == "ASK_BUDGET_EXCEEDED"
        assert len(requests) == 1
        rows = connection.portal.call(app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (ask_run,))
        assert (
            len(rows) == 1
            and rows[0]["mode"] == "ask"
            and rows[0]["input_tokens"] == 7
            and rows[0]["output_tokens"] == 3
        )
    finally:
        if "model" in locals():
            connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (model,))
        server.shutdown()
        server.server_close()
        thread.join(3)
