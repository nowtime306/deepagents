"""A real local embedding endpoint verifies recall, revision pins and all paid-call ledgers."""

import json
import os
import socket
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from test_api import ORIGIN
from test_api import client as client

from app.ask_worker import AskWorker
from tests.unit_tests.test_harness import ToolModel


@pytest.fixture
def embedding(client):
    connection, app = client
    requests, behavior = [], {"disconnect": False, "usage": True, "invalid": False, "callback": None}

    class Embeddings(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if behavior["callback"]:
                behavior["callback"]()
            if behavior["disconnect"]:
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            vectors = [
                {
                    "index": i,
                    "embedding": [float("nan"), 0]
                    if behavior["invalid"]
                    else ([1, 0] if "中文" in text or "languagechoice" in text else [0, 1]),
                }
                for i, text in enumerate(body["input"])
            ]
            result = {"data": vectors}
            if behavior["usage"]:
                result["usage"] = {"prompt_tokens": 6 * len(vectors), "total_tokens": 6 * len(vectors)}
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Embeddings)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    identifier = "memory-vector-" + uuid4().hex[:12]
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,kind) VALUES(%s,%s,'local-vector',%s,'embedding')",
        (identifier, identifier, f"http://127.0.0.1:{server.server_port}/v1"),
    )
    try:
        configured = connection.put(
            "/api/v1/memories/settings",
            json={"embedding_model_id": identifier, "enabled": True, "revision": 0, "model_revision": 1},
            headers=ORIGIN,
        )
        assert configured.status_code == 200, configured.text
        yield connection, app, identifier, requests, behavior
    finally:
        connection.portal.call(
            app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (identifier,)
        )
        server.shutdown()
        server.server_close()
        thread.join(3)


def memory(connection, title, content, **extra):
    result = connection.post("/api/v1/memories", json={"title": title, "content": content, **extra}, headers=ORIGIN)
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_semantic_recall_scope_usage_prices_and_model_revision(embedding):
    connection, app, model, requests, _ = embedding
    chosen = memory(connection, "回复习惯", "用户偏好中文")
    memory(connection, "出行", "用户住在森林")
    project = connection.post("/api/v1/conversation-projects", json={"name": "隔离范围"}, headers=ORIGIN).json()["id"]
    private = memory(connection, "项目偏好", "项目需要中文", scope_kind="project", scope_id=project)
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_cost_rates(model_id,input_per_million,output_per_million,currency) VALUES(%s,2,0,'CNY')",
        (model,),
    )
    indexed = connection.post("/api/v1/memories/index", json={}, headers=ORIGIN)
    assert indexed.status_code == 200 and indexed.json()["indexed"] == 3, indexed.text
    result = connection.post("/api/v1/memories/recall", json={"query": "languagechoice"}, headers=ORIGIN)
    assert result.status_code == 200, result.text
    assert result.json()["mode"] == "hybrid" and result.json()["items"][0]["id"] == chosen
    assert private not in {row["id"] for row in result.json()["items"]}
    scoped = connection.post(
        "/api/v1/memories/recall", json={"query": "languagechoice", "project_id": project}, headers=ORIGIN
    )
    assert private in {row["id"] for row in scoped.json()["items"]}
    ledger = connection.portal.call(
        app.state.db.fetch,
        "SELECT mode,call_kind,status,input_tokens,output_tokens,input_rate,estimated_cost "
        "FROM model_usage WHERE run_id=%s",
        (indexed.json()["operation_id"],),
    )
    assert ledger[0] == {
        "mode": "resource",
        "call_kind": "embedding",
        "status": "KNOWN",
        "input_tokens": 18,
        "output_tokens": 0,
        "input_rate": Decimal(2),
        "estimated_cost": Decimal("0.000036"),
    }
    prior = len(requests)
    connection.portal.call(app.state.db.execute, "UPDATE model_configs SET revision=revision+1 WHERE id=%s", (model,))
    changed = connection.post("/api/v1/memories/recall", json={"query": "中文"}, headers=ORIGIN)
    assert changed.status_code == 200 and changed.json()["mode"] == "keyword" and changed.json()["warning"]
    assert len(requests) == prior


def test_index_unknown_requires_explicit_retry_and_missing_usage_remains_unknown(embedding):
    connection, _, _, requests, behavior = embedding
    memory(connection, "偏好", "用户偏好中文")
    behavior["disconnect"] = True
    first = connection.post("/api/v1/memories/index", json={}, headers=ORIGIN)
    assert first.status_code == 502 and first.json()["error"]["code"] == "EMBEDDING_RESULT_UNKNOWN"
    assert connection.post("/api/v1/memories/index", json={}, headers=ORIGIN).status_code == 409
    assert len(requests) == 1
    behavior.update(disconnect=False, usage=False)
    assert (
        connection.post("/api/v1/memories/index", json={"acknowledge_duplicate": True}, headers=ORIGIN).status_code
        == 200
    )
    rows = connection.get("/api/v1/memories/resource-operations").json()["items"]
    succeeded = next(row for row in rows if row["status"] == "SUCCEEDED")
    assert succeeded["usage"]["input_tokens"] is None and succeeded["usage"]["unknown_calls"] == 1


def test_invalid_vectors_never_cached_and_changed_memory_skipped(embedding):
    connection, app, _, _, behavior = embedding
    identifier = memory(connection, "偏好", "用户偏好中文")
    behavior["invalid"] = True
    assert connection.post("/api/v1/memories/index", json={}, headers=ORIGIN).status_code == 502
    assert (
        connection.portal.call(
            app.state.db.fetch, "SELECT vector_status FROM private_memories WHERE id=%s", (identifier,)
        )[0]["vector_status"]
        == "NONE"
    )
    behavior["invalid"] = False

    def update():
        result = connection.put(
            f"/api/v1/memories/{identifier}",
            json={"title": "新偏好", "content": "用户现在偏好英文", "revision": 1},
            headers=ORIGIN,
        )
        assert result.status_code == 200

    behavior["callback"] = update
    result = connection.post("/api/v1/memories/index", json={}, headers=ORIGIN)
    assert result.status_code == 200 and result.json()["indexed"] == 0 and result.json()["skipped_changed"] == 1


def test_parent_ask_counts_embedding_and_blocks_shared_budget_before_next_model(embedding):
    connection, app, model, requests, _ = embedding
    memory(connection, "偏好", "用户偏好中文")
    assert connection.post("/api/v1/memories/index", json={}, headers=ORIGIN).status_code == 200
    assert (
        connection.put(
            "/api/v1/capabilities/bindings",
            json={"knowledge_ids": [], "skill_ids": [], "mcp_ids": [], "memory_enabled": True},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    app.state.settings.ask_worker_enabled = True
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        json={"model_id": "test", "message": "Recall languagechoice"},
        headers=ORIGIN,
    ).json()["run_id"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE task_runs SET harness_policy=jsonb_set(harness_policy,'{model_calls}','2') WHERE id=%s",
        (run,),
    )
    called = []

    class Lead(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            called.append(True)
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def factory(_):
        return Lead(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "memory-search", "name": "search_memories", "args": {"query": "languagechoice"}}
                    ],
                    usage_metadata={"input_tokens": 9, "output_tokens": 3, "total_tokens": 12},
                )
            ]
        )

    worker = AskWorker(app.state.db, app.state.models, app.state.settings, factory)
    connection.portal.call(worker.tick)
    state = connection.get(f"/api/v1/runs/{run}").json()
    assert state["status"] == "FAILED" and state["error_code"] == "ASK_BUDGET_EXCEEDED", state
    assert len(called) == 1 and len(requests) == 2
    ledger = connection.portal.call(
        app.state.db.fetch,
        "SELECT model_id,call_kind,input_tokens,output_tokens FROM model_usage WHERE run_id=%s ORDER BY created_at",
        (run,),
    )
    assert [row["call_kind"] for row in ledger] == ["chat", "embedding"]
    assert ledger[1]["model_id"] == model and ledger[1]["input_tokens"] == 6 and ledger[1]["output_tokens"] == 0


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local sandbox acceptance")
@pytest.mark.parametrize("reported", [True, False])
def test_work_semantic_call_updates_shared_counters_once(embedding, reported):
    from opensandbox.config import ConnectionConfig

    from app.durable_sandboxes import DurableSandboxes
    from app.work_artifacts import WorkArtifacts
    from app.work_engine import WorkEngine

    connection, app, model, requests, behavior = embedding
    memory(connection, "偏好", "用户偏好中文")
    assert connection.post("/api/v1/memories/index", json={}, headers=ORIGIN).status_code == 200
    behavior["usage"] = reported
    connection.put(
        "/api/v1/capabilities/bindings",
        json={"knowledge_ids": [], "skill_ids": [], "mcp_ids": [], "memory_enabled": True},
        headers=ORIGIN,
    )
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post(
        "/api/v1/work-tasks", json={"title": "语义工作", "goal": "召回语言偏好"}, headers=ORIGIN
    ).json()["id"]
    started = connection.post(
        f"/api/v1/work-tasks/{task}/runs", json={"revision": 1, "model_id": "test"}, headers=ORIGIN
    )
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    sandbox = DurableSandboxes(
        app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)
    )
    observed = []

    class Lead(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            observed.append(str(messages))
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def factory(_):
        return Lead(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"id": "memory-read", "name": "search_memories", "args": {"query": "languagechoice"}}],
                    usage_metadata={"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
                ),
                AIMessage(
                    content="已召回中文偏好", usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
                ),
            ]
        )

    engine = WorkEngine(
        app.state.db,
        app.state.models,
        sandbox,
        WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts"),
        app.state.runtime.saver,
        factory,
        capabilities_factory=lambda _: app.state.capabilities,
    )

    async def perform():
        row = (
            await app.state.db.fetch(
                "UPDATE work_queue_runs SET status='RUNNING',lease_token=%s,lease_until=now()+interval '60 seconds' "
                "WHERE id=%s AND status='PENDING' RETURNING *",
                (uuid4(), run),
            )
        )[0]
        await engine.events.perform(row)

    try:
        connection.portal.call(perform)
        state = connection.get(f"/api/v1/work-runs/{run}").json()
        if reported:
            assert state["status"] == "COMPLETED" and state["used_tokens"] == 22 and state["model_calls"] == 3, state
            assert any("用户偏好中文" in value for value in observed)
        else:
            assert state["status"] == "NEEDS_REVIEW" and state["error_code"] == "WORK_USAGE_UNAVAILABLE", state
            assert state["usage_unknown"] and state["model_calls"] == 2
        assert len(requests) == 2
        rows = connection.portal.call(
            app.state.db.fetch, "SELECT model_id,call_kind FROM model_usage WHERE run_id=%s", (run,)
        )
        assert len(rows) == (3 if reported else 2)
        assert {r["model_id"] for r in rows if r["call_kind"] == "embedding"} == {model}
    finally:
        connection.portal.call(sandbox.close)
        connection.portal.call(
            app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s AND state='RELEASED'", (run,)
        )
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run,))


def test_member_embedding_keeps_embedding_model_and_role_in_ledger(embedding):
    from app.memory_vectors import MemoryRecall
    from app.team_context import active_member

    connection, app, model, requests, _ = embedding
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    run, token, member = uuid4(), uuid4(), uuid4()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO task_runs(id,conversation_id,model_id,status,lease_token,lease_until) "
        "VALUES(%s,%s,'test','RUNNING',%s,now()+interval '60 seconds')",
        (run, conversation, token),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO team_calls(id,owner_id,ask_run_id,role_name,version_id,model_id,tool_call_id,input_digest) "
        "VALUES(%s,%s,%s,'审阅员',%s,'test','vector-read','fixture')",
        (member, owner, run, uuid4()),
    )

    async def query():
        marker = active_member.set(
            {"model_id": "test", "revision": 1, "reserve": 4096, "role_name": "审阅员", "id": member}
        )
        try:
            config = await app.state.models.get(model)
            await MemoryRecall(app.state.db, app.state.models).vectors(
                owner, config, ["languagechoice"], {"id": run, "mode": "ask", "lease_token": token}
            )
        finally:
            active_member.reset(marker)

    connection.portal.call(query)
    rows = connection.portal.call(
        app.state.db.fetch,
        "SELECT model_id,role_name,team_call_id,call_kind,output_token_reserve FROM model_usage WHERE run_id=%s",
        (run,),
    )
    assert rows == [
        {
            "model_id": model,
            "role_name": "审阅员",
            "team_call_id": member,
            "call_kind": "embedding",
            "output_token_reserve": 0,
        }
    ]
    assert len(requests) == 1 and requests[0]["model"] == "local-vector"
