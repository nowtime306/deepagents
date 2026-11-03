"""Bounded progressive loops stop safely and handle only known preflight/limit failures."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client
from test_workflow_composition import create, definition, publish, run

from app.graphs import resolve_model
from app.models import ModelInput


@pytest.fixture
def provider(client):
    connection, app = client
    calls, control = [], {"unknown": False, "usage": (7, 3), "after": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            hook, control["after"] = control["after"], None
            if hook:
                hook()
            tokens_in, tokens_out = control["usage"]
            source = body["messages"][-1]["content"]
            payload = (
                {"error": {"message": "local result unknown", "type": "server_error"}}
                if control["unknown"]
                else {
                    "id": "local-loop",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "local",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "draft" if source == "seed" else "done"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": tokens_in,
                        "completion_tokens": tokens_out,
                        "total_tokens": tokens_in + tokens_out,
                    },
                }
            )
            encoded = json.dumps(payload).encode()
            self.send_response(503 if control["unknown"] else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    model = None
    try:
        model = connection.portal.call(
            app.state.models.save,
            ModelInput(
                name="条件循环本机-" + uuid4().hex,
                model="local",
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="local-only",
                max_output_tokens=128,
            ),
        )
        app.state.runtime.model_factory = resolve_model
        _, version = publish(connection, definition("llm", model_id=model))
        calls.clear()
        yield SimpleNamespace(
            connection=connection, app=app, calls=calls, control=control, version=version, model=model
        )
    finally:
        if model:
            connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (model,))
        server.shutdown()
        server.server_close()
        thread.join(3)


def handled(graph):
    graph["nodes"].insert(
        2, {"id": "recover", "kind": "transform", "input": "action", "parameters": {"operation": "text"}}
    )
    graph["edges"] += [
        {"source": "action", "target": "recover", "case": "error"},
        {"source": "recover", "target": "end"},
    ]
    return graph


def events(connection, identifier, response):
    run_id = (
        response.json().get("id") or connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"][0]["id"]
    )
    return connection.get(f"/api/v1/workflows/{identifier}/runs/{run_id}/events").json()["items"]


def test_progressive_loop_converges_and_keeps_per_round_evidence(client):
    connection, _ = client
    _, version = publish(connection, definition(operation="replace", old="aa", new="a"))
    identifier = create(
        connection, definition("loop", version_id=version, max_iterations=3, operator="equals", value="a")
    )
    response = run(connection, identifier, "aaaaaaaa")
    assert response.status_code == 200 and response.json()["output"] == "a"
    progress = [item["data"] for item in events(connection, identifier, response) if item["event"] == "loop.progress"]
    assert progress == [
        {"iteration": 1, "maximum": 3, "matched": False},
        {"iteration": 2, "maximum": 3, "matched": False},
        {"iteration": 3, "maximum": 3, "matched": True},
    ]


def test_loop_limit_is_not_success_unless_explicitly_handled(client):
    connection, _ = client
    _, version = publish(connection, definition(operation="replace", old="aa", new="a"))
    graph = definition("loop", version_id=version, max_iterations=2, operator="equals", value="a")
    identifier = create(connection, graph)
    response = run(connection, identifier, "aaaaaaaa")
    assert response.status_code == 502
    failures = [
        item["data"]["error_code"]
        for item in events(connection, identifier, response)
        if item["event"] == "node.failed"
    ]
    assert "WORKFLOW_LOOP_LIMIT" in failures
    identifier = create(connection, handled(graph))
    response = run(connection, identifier, "aaaaaaaa")
    assert response.status_code == 200
    assert json.loads(response.json()["output"]) == {"error_code": "WORKFLOW_LOOP_LIMIT"}
    assert any(item["event"] == "node.handled" for item in events(connection, identifier, response))
    identifier = create(
        connection,
        {**definition("loop", version_id=version, max_iterations=3, operator="equals", value="a"), "node_budget": 5},
    )
    response = run(connection, identifier, "aaaaaaaa")
    assert response.status_code == 502
    assert not any(item["node_id"].startswith("action.1.") for item in events(connection, identifier, response))


@pytest.mark.parametrize(
    "text,code",
    [
        ("broken", "WORKFLOW_ITERATION_INPUT"),
        ("[NaN]", "WORKFLOW_ITERATION_INPUT"),
        ("[1e999]", "WORKFLOW_ITERATION_INPUT"),
        ("{}", "WORKFLOW_ITERATION_LIMIT"),
        ('["one","two"]', "WORKFLOW_ITERATION_LIMIT"),
    ],
)
def test_iteration_preflight_error_branch_never_starts_child(client, text, code):
    connection, _ = client
    _, version = publish(connection, definition(operation="upper"))
    identifier = create(connection, handled(definition("iteration", version_id=version, max_items=1)))
    response = run(connection, identifier, text)
    assert response.status_code == 200 and json.loads(response.json()["output"]) == {"error_code": code}
    assert not any(item["node_id"].startswith("action.") for item in events(connection, identifier, response))


def test_loop_actual_calls_share_one_root_ledger(provider):
    p = provider
    graph = definition("loop", version_id=p.version, max_iterations=3, operator="equals", value="done")
    identifier = create(p.connection, graph)
    response = run(p.connection, identifier, "seed")
    assert response.status_code == 200 and response.json()["output"] == "done" and len(p.calls) == 2
    rows = p.connection.portal.call(
        p.app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (response.json()["id"],)
    )
    assert len(rows) == 2 and all(row["status"] == "KNOWN" and row["model_id"] == p.model for row in rows)
    assert sum(row["input_tokens"] for row in rows) == 14 and sum(row["output_tokens"] for row in rows) == 6


def test_end_can_return_the_executed_branch_without_reading_an_unexecuted_variable(client):
    connection, _ = client
    _, version = publish(connection, definition(operation="replace", old="aa", new="a"))
    graph = handled(definition("loop", version_id=version, max_iterations=2, operator="equals", value="a"))
    legacy = create(connection, graph)
    assert json.loads(run(connection, legacy, "aaaaaaaa").json()["output"])["error_code"] == "WORKFLOW_LOOP_LIMIT"
    graph["nodes"][2]["parameters"] = {"operation": "json_get", "path": "error_code"}
    graph["nodes"][-1].update(input="recover", parameters={"use_previous": True})
    identifier = create(connection, graph)
    assert run(connection, identifier, "aa").json()["output"] == "a"
    assert run(connection, identifier, "aaaaaaaa").json()["output"] == "WORKFLOW_LOOP_LIMIT"
    graph["nodes"][-1]["parameters"]["use_previous"] = False
    assert (
        connection.post(
            "/api/v1/workflows",
            headers=ORIGIN,
            json={"name": "拒绝读取另一分支变量", "model_id": "test", "definition": graph},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("value", ["true", 1, None])
def test_end_branch_mode_requires_a_real_boolean(client, value):
    graph = definition(operation="text")
    graph["nodes"][-1]["parameters"] = {"use_previous": value}
    assert (
        client[0]
        .post(
            "/api/v1/workflows",
            headers=ORIGIN,
            json={"name": "拒绝隐式转换结束配置", "model_id": "test", "definition": graph},
        )
        .status_code
        == 422
    )


@pytest.mark.parametrize("mode", ["calls", "tokens", "unknown"])
def test_loop_does_not_handle_budget_or_unknown_as_safe_failure(provider, mode):
    p = provider
    graph = handled(definition("loop", version_id=p.version, max_iterations=3, operator="equals", value="done"))
    if mode == "calls":
        graph["model_calls"] = 1
    elif mode == "tokens":
        graph["token_limit"] = 1000
        p.control["usage"] = (700, 400)
    else:
        p.control["unknown"] = True
    identifier = create(p.connection, graph)
    response = run(p.connection, identifier, "seed")
    assert response.status_code == 502 and len(p.calls) == 1
    log = events(p.connection, identifier, response)
    assert not any(item["event"] == "node.handled" for item in log)
    run_id = p.connection.get(f"/api/v1/workflows/{identifier}/runs").json()["items"][0]["id"]
    rows = p.connection.portal.call(p.app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (run_id,))
    assert len(rows) == 1 and rows[0]["status"] == ("UNKNOWN" if mode == "unknown" else "KNOWN")
    if mode == "tokens":
        assert rows[0]["input_tokens"] == 700 and rows[0]["output_tokens"] == 400


@pytest.mark.parametrize("mode", ["cancel", "revoke"])
def test_ask_loop_stops_between_rounds_on_live_authority_change(provider, mode):
    from test_ask_workflows import tool_context

    p = provider
    from app.errors import APIError
    from app.workflow_compiler import WorkflowNodeFailed

    _, version = publish(
        p.connection, definition("loop", version_id=p.version, max_iterations=3, operator="equals", value="done")
    )
    ask_run, _, tool = tool_context(p.connection, p.app, version)
    p.calls.clear()

    def stop():
        if mode == "cancel":
            p.connection.portal.call(
                p.app.state.db.execute, "UPDATE task_runs SET cancel_requested=true WHERE id=%s", (ask_run,)
            )
        else:
            p.connection.portal.call(
                p.app.state.db.execute, "UPDATE workflow_versions SET active=false WHERE id=%s", (p.version,)
            )

    p.control["after"] = stop
    with pytest.raises((APIError, WorkflowNodeFailed)):
        p.connection.portal.call(tool.ainvoke, {"version_id": version, "text": "seed"})
    assert len(p.calls) == 1
    rows = p.connection.portal.call(p.app.state.db.fetch, "SELECT * FROM model_usage WHERE run_id=%s", (ask_run,))
    assert len(rows) == 1 and rows[0]["status"] == "KNOWN" and rows[0]["input_tokens"] == 7
