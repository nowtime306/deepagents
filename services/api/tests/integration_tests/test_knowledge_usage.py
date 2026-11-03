"""Actual local embedding/rerank requests, unknown results and parent budget admission."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from test_api import ORIGIN
from test_api import client as client

from app.models import ModelInput
from tests.unit_tests.test_harness import ToolModel


@pytest.fixture
def provider(client):
    connection, app = client
    requests, behavior = [], {"disconnect": None, "usage": True}

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if behavior["disconnect"] == self.path:
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            if self.path == "/v1/embeddings":
                result = {"data": [{"index": i, "embedding": [1, 0]} for i, _ in enumerate(body["input"])]}
                if behavior["usage"]:
                    result["usage"] = {"prompt_tokens": 6, "total_tokens": 6}
            else:
                result = {
                    "results": [{"index": i, "relevance_score": 1 - i / 100} for i, _ in enumerate(body["documents"])]
                }
                if behavior["usage"]:
                    result["meta"] = {"tokens": {"input_tokens": 150, "output_tokens": 10}}
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    models = []
    try:
        for kind in ["embedding", "rerank"]:
            models.append(
                connection.portal.call(
                    app.state.models.save,
                    ModelInput(
                        name="知识计账-" + uuid4().hex[:12],
                        kind=kind,
                        model="local-" + kind,
                        base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    ),
                )
            )
        identifier = connection.post(
            "/api/v1/capabilities/knowledge",
            json={
                "name": "带用量的检索",
                "embedding_model_id": models[0],
                "rerank_model_id": models[1],
            },
            headers=ORIGIN,
        ).json()["id"]
        yield connection, app, identifier, models, requests, behavior
    finally:
        for identifier in models:
            connection.portal.call(
                app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (identifier,)
            )
        server.shutdown()
        server.server_close()
        thread.join(3)


def upload(connection, identifier):
    response = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/documents",
        headers=ORIGIN,
        files={"file": ("资料.txt", "产品价格是200元，保修三年。".encode())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_actual_embedding_rerank_usage_and_duplicate_import(provider):
    connection, app, identifier, models, requests, _ = provider
    uploaded = upload(connection, identifier)
    assert uploaded["index"]["status"] == "SUCCEEDED"
    assert upload(connection, identifier)["duplicate"] and len(requests) == 1
    result = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "价格"}, headers=ORIGIN
    )
    assert result.status_code == 200 and result.json()["mode"] == "hybrid" and result.json()["reranked"]
    operations = connection.get(f"/api/v1/capabilities/knowledge/{identifier}/operations").json()["items"]
    query = next(row for row in operations if row["action"] == "query")
    assert query["id"] == result.json()["operation_id"] and query["status"] == "SUCCEEDED"
    assert query["usage"]["input_tokens"] == 156 and query["usage"]["output_tokens"] == 10
    ledger = connection.portal.call(
        app.state.db.fetch,
        "SELECT model_id,call_kind,status,input_tokens,output_tokens FROM model_usage "
        "WHERE run_id=%s ORDER BY call_kind",
        (query["id"],),
    )
    assert ledger == [
        {"model_id": models[0], "call_kind": "embedding", "status": "KNOWN", "input_tokens": 6, "output_tokens": 0},
        {"model_id": models[1], "call_kind": "rerank", "status": "KNOWN", "input_tokens": 150, "output_tokens": 10},
    ]


def test_unknown_index_requires_explicit_ack_and_rerank_never_replays(provider):
    connection, _, identifier, _, requests, behavior = provider
    behavior["disconnect"] = "/v1/embeddings"
    uploaded = upload(connection, identifier)
    assert uploaded["index"]["status"] == "FAILED" and len(requests) == 1
    target = f"/api/v1/capabilities/knowledge/{identifier}/documents/{uploaded['id']}/reindex"
    assert connection.post(target, headers=ORIGIN).status_code == 409 and len(requests) == 1
    behavior["disconnect"] = None
    assert connection.post(target, json={"acknowledge_duplicate": True}, headers=ORIGIN).status_code == 200
    behavior["disconnect"] = "/v1/rerank"
    result = connection.post(
        f"/api/v1/capabilities/knowledge/{identifier}/search", json={"query": "价格"}, headers=ORIGIN
    )
    assert result.status_code == 200 and not result.json()["reranked"] and result.json()["warning"]
    assert [path for path, _ in requests].count("/v1/rerank") == 1
    operations = connection.get(f"/api/v1/capabilities/knowledge/{identifier}/operations").json()["items"]
    query = next(row for row in operations if row["action"] == "query")
    assert query["status"] == "UNKNOWN" and query["usage"]["unknown_calls"] == 1


def test_ask_resource_calls_share_budget_before_rerank_request(provider):
    connection, app, identifier, _, requests, _ = provider
    upload(connection, identifier)
    assert (
        connection.put(
            "/api/v1/capabilities/bindings", json={"knowledge_ids": [identifier]}, headers=ORIGIN
        ).status_code
        == 200
    )
    app.state.settings.ask_worker_enabled = True
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "请检索价格"}, headers=ORIGIN
    ).json()["run_id"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE task_runs SET harness_policy=jsonb_set(harness_policy,'{model_calls}','2') WHERE id=%s",
        (run,),
    )
    response = AIMessage(
        content="",
        tool_calls=[{"id": "lookup", "name": "search_knowledge", "args": {"query": "价格"}}],
        usage_metadata={"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
    )
    app.state.runtime.model_factory = lambda _: ToolModel(responses=[response, AIMessage(content="不应继续")])

    async def perform():
        await app.state.db.execute("UPDATE task_runs SET status='RUNNING' WHERE id=%s", (run,))
        await app.state.runtime.run(UUID(run), UUID(conversation), "请检索价格", await app.state.models.get("test"))

    connection.portal.call(perform)
    state = connection.get(f"/api/v1/runs/{run}").json()
    assert state["status"] == "FAILED" and state["error_code"] == "ASK_BUDGET_EXCEEDED", state
    assert [path for path, _ in requests] == ["/v1/embeddings", "/v1/embeddings"]
    ledger = connection.portal.call(app.state.db.fetch, "SELECT call_kind FROM model_usage WHERE run_id=%s", (run,))
    assert sorted(row["call_kind"] for row in ledger) == ["chat", "embedding"]
