"""Candidate approval, scope/expiry, source deletion and unknown-call boundaries."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.ask_queue import AskQueue
from app.ask_worker import AskWorker
from app.memory_api import search
from tests.unit_tests.test_harness import ToolModel


def source(connection, app):
    conversation = connection.post("/api/v1/conversations", json={"title": "授权记忆来源"}, headers=ORIGIN).json()["id"]
    message, run = uuid4(), uuid4()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES(%s,%s,'test','SUCCEEDED')",
        (run, conversation),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) VALUES(%s,%s,%s,'user',%s)",
        (message, conversation, run, Jsonb({"text": "我偏好中文回复。我常用 Windows 11。"})),
    )
    return conversation, str(message)


def start(connection, conversation, **changes):
    response = connection.post(
        "/api/v1/memories/jobs", json={"conversation_id": conversation, "model_id": "test", **changes}, headers=ORIGIN
    )
    assert response.status_code == 202, response.text
    return response.json()


def generate(connection, app, message, *, output=None, broken=False):
    tools, calls = [], []

    class Extractor(ToolModel):
        def bind_tools(self, available, **kwargs):
            tools.extend(tool.name for tool in available)
            return self

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(messages)
            if broken:
                raise TimeoutError("local deterministic interruption")
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    text = (
        output
        if output is not None
        else json.dumps(
            {"items": [{"title": "回复偏好", "content": "用户偏好中文回复", "source_message_ids": [message]}]},
            ensure_ascii=False,
        )
    )
    worker = AskWorker(
        app.state.db,
        app.state.models,
        app.state.settings,
        lambda _: Extractor(
            responses=[
                AIMessage(content=text, usage_metadata={"input_tokens": 9, "output_tokens": 3, "total_tokens": 12})
            ]
        ),
    )
    assert connection.portal.call(worker.tick)
    return tools, calls


def test_extract_review_provenance_usage_and_private_internal_history(client):
    connection, app = client
    conversation, message = source(connection, app)
    job = start(connection, conversation)
    assert start(connection, conversation)["id"] == job["id"]
    row = connection.portal.call(
        app.state.db.fetch, "SELECT internal_conversation_id FROM memory_jobs WHERE id=%s", (job["id"],)
    )[0]
    internal = str(row["internal_conversation_id"])
    assert internal not in {r["id"] for r in connection.get("/api/v1/conversations").json()["items"]}
    assert (
        connection.post(
            f"/api/v1/conversations/{internal}/runs", json={"model_id": "test", "message": "不能追加"}, headers=ORIGIN
        ).status_code
        == 409
    )
    tools, calls = generate(connection, app, message)
    assert len(calls) == 1 and not {"task", "search_memories", "search_knowledge", "execute", "web_search"} & set(tools)
    result = connection.get("/api/v1/memories/jobs").json()["items"][0]
    assert result["status"] == "SUCCEEDED"
    assert not connection.get("/api/v1/memories").json()["items"]
    candidate = connection.get("/api/v1/memories/candidates").json()["items"][0]
    assert candidate["source_conversation_id"] == conversation and candidate["source_message_ids"] == [message]
    reviewed = connection.post(
        f"/api/v1/memories/candidates/{candidate['id']}/review",
        json={"revision": 1, "decision": "accept", "content": "用户要求优先中文回复"},
        headers=ORIGIN,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert (
        connection.post(
            f"/api/v1/memories/candidates/{candidate['id']}/review",
            json={"revision": 1, "decision": "accept"},
            headers=ORIGIN,
        ).status_code
        == 409
    )
    memory = connection.get("/api/v1/memories").json()["items"][0]
    assert memory["content"] == "用户要求优先中文回复" and memory["origin"] == "candidate"
    usage = connection.portal.call(
        app.state.db.fetch, "SELECT input_tokens,output_tokens FROM model_usage WHERE run_id=%s", (job["run_id"],)
    )
    assert usage == [{"input_tokens": 9, "output_tokens": 3}]
    assert connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN).status_code == 200
    assert not connection.get("/api/v1/memories/jobs").json()["items"]
    assert connection.get(f"/api/v1/conversations/{internal}").status_code == 404
    memory = connection.get("/api/v1/memories").json()["items"][0]
    assert memory["candidate_id"] is None and memory["origin"] == "candidate"


def test_memory_scope_expiry_dedup_conflict_and_legacy_edit_preservation(client):
    connection, app = client
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    project = connection.post("/api/v1/conversation-projects", json={"name": "独立项目"}, headers=ORIGIN).json()["id"]
    agent = connection.post("/api/v1/agents", json={"name": "独立智能体", "model_id": "test"}, headers=ORIGIN).json()[
        "id"
    ]
    global_memory = connection.post("/api/v1/memories", json={"title": "全局", "content": "偏好中文"}, headers=ORIGIN)
    assert global_memory.status_code == 201
    scoped = connection.post(
        "/api/v1/memories",
        json={
            "title": "项目偏好",
            "content": "项目中文",
            "scope_kind": "project",
            "scope_id": project,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
        headers=ORIGIN,
    )
    assert scoped.status_code == 201, scoped.text
    assert (
        connection.post(
            "/api/v1/memories",
            json={"title": "重复", "content": "项目中文", "scope_kind": "project", "scope_id": project},
            headers=ORIGIN,
        ).json()["id"]
        == scoped.json()["id"]
    )
    assert (
        connection.post(
            "/api/v1/memories",
            json={"title": "项目偏好", "content": "不同内容", "scope_kind": "project", "scope_id": project},
            headers=ORIGIN,
        ).status_code
        == 409
    )
    connection.post(
        "/api/v1/memories",
        json={"title": "Agent偏好", "content": "Agent中文", "scope_kind": "agent", "scope_id": agent},
        headers=ORIGIN,
    )
    assert len(connection.portal.call(search, app.state.db, owner, "中文")) == 1
    from functools import partial

    assert len(connection.portal.call(partial(search, app.state.db, owner, "中文", project_id=UUID(project)))) == 2
    assert len(connection.portal.call(partial(search, app.state.db, owner, "中文", agent_id=UUID(agent)))) == 2
    updated = connection.put(
        f"/api/v1/memories/{scoped.json()['id']}",
        json={"revision": 1, "title": "项目偏好", "content": "更偏好中文"},
        headers=ORIGIN,
    )
    assert updated.status_code == 200, updated.text
    row = next(r for r in connection.get("/api/v1/memories").json()["items"] if r["id"] == scoped.json()["id"])
    assert row["scope_kind"] == "project" and row["scope_id"] == project and row["expires_at"]
    connection.portal.call(
        app.state.db.execute,
        "UPDATE private_memories SET expires_at=now()-interval '1 second' WHERE id=%s",
        (scoped.json()["id"],),
    )
    assert len(connection.portal.call(partial(search, app.state.db, owner, "中文", project_id=UUID(project)))) == 1
    connection.portal.call(app.state.db.execute, "UPDATE agents SET enabled=false WHERE id=%s", (agent,))
    assert len(connection.portal.call(partial(search, app.state.db, owner, "中文", agent_id=UUID(agent)))) == 1
    assert (
        connection.post(
            "/api/v1/memories",
            json={"title": "越权", "content": "资料", "scope_kind": "project", "scope_id": str(uuid4())},
            headers=ORIGIN,
        ).status_code
        == 403
    )


def test_invalid_output_and_unknown_request_never_generate_accepted_memories(client):
    connection, app = client
    conversation, message = source(connection, app)
    job = start(connection, conversation)
    generate(connection, app, message, broken=True)
    result = connection.get("/api/v1/memories/jobs").json()["items"][0]
    assert result["status"] == "UNKNOWN", result
    assert start(connection, conversation)["id"] == job["id"]
    assert not connection.get("/api/v1/memories/candidates").json()["items"]
    conversation2, message2 = source(connection, app)
    start(connection, conversation2)
    generate(
        connection,
        app,
        message2,
        output=json.dumps({"items": [{"title": "伪造", "content": "无来源", "source_message_ids": [str(uuid4())]}]}),
    )
    results = connection.get("/api/v1/memories/jobs").json()["items"]
    assert any(r["error_code"] == "MEMORY_OUTPUT_INVALID" for r in results)
    assert not connection.get("/api/v1/memories").json()["items"]


def test_pending_extraction_cancel_and_source_deletion(client):
    connection, app = client
    conversation, _ = source(connection, app)
    job = start(connection, conversation)
    assert connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN).status_code == 409
    assert connection.post(f"/api/v1/memories/jobs/{job['id']}/cancel", headers=ORIGIN).status_code == 200
    connection.portal.call(AskQueue(app.state.db).reconcile)
    assert connection.delete(f"/api/v1/conversations/{conversation}", headers=ORIGIN).status_code == 200
    assert not connection.get("/api/v1/memories/jobs").json()["items"]


def test_memory_extraction_permission_is_rechecked_by_worker(client):
    connection, app = client
    conversation, _ = source(connection, app)
    job = start(connection, conversation)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    role = str(uuid4())
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,'无记忆管理',%s)",
        (role, Jsonb(["chat.read", "chat.run", "memory.read"])),
    )
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id=%s WHERE id=%s", (role, owner))
    calls = []

    def factory(_):
        calls.append(True)
        return ToolModel(responses=[])

    worker = AskWorker(app.state.db, app.state.models, app.state.settings, factory)
    connection.portal.call(worker.tick)
    assert not calls
    row = connection.get("/api/v1/memories/jobs").json()["items"][0]
    assert row["status"] == "FAILED" and row["error_code"] == "PERMISSION_REQUIRED"
    assert not connection.portal.call(app.state.db.fetch, "SELECT 1 FROM model_usage WHERE run_id=%s", (job["run_id"],))


def test_foreign_account_cannot_extract_review_or_scope_memory(client):
    from test_api import run_async

    from app.manage import add_user

    connection, app = client
    conversation, message = source(connection, app)
    job = start(connection, conversation)
    generate(connection, app, message)
    connection.get("/api/v1/memories/jobs")
    candidate = connection.get("/api/v1/memories/candidates").json()["items"][0]
    project = connection.post("/api/v1/conversation-projects", json={"name": "私有项目"}, headers=ORIGIN).json()["id"]
    username = "memory-other-" + uuid4().hex[:12]
    run_async(add_user(app.state.settings.database_url, username, "isolated-test-password"))
    connection.cookies.clear()
    assert (
        connection.post(
            "/api/v1/auth/login", json={"username": username, "password": "isolated-test-password"}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert not connection.get("/api/v1/memories/jobs").json()["items"]
    assert not connection.get("/api/v1/memories/candidates").json()["items"]
    assert (
        connection.post(
            "/api/v1/memories/jobs", json={"conversation_id": conversation, "model_id": "test"}, headers=ORIGIN
        ).status_code
        == 404
    )
    assert (
        connection.post(
            f"/api/v1/memories/candidates/{candidate['id']}/review",
            json={"revision": 1, "decision": "accept"},
            headers=ORIGIN,
        ).status_code
        == 409
    )
    assert (
        connection.post(
            "/api/v1/memories",
            json={"title": "越权范围", "content": "私人资料", "scope_kind": "project", "scope_id": project},
            headers=ORIGIN,
        ).status_code
        == 403
    )
    assert connection.post(f"/api/v1/memories/jobs/{job['id']}/cancel", headers=ORIGIN).status_code == 409
