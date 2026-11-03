"""Published teams use native delegation with separate models and a shared ledger."""

import asyncio
import os
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage
from test_api import ORIGIN
from test_api import client as client

from app.errors import APIError
from app.harness import ASK_POLICY
from app.team_runtime import TeamRun
from app.usage import UsageRecorder
from tests.unit_tests.test_harness import ToolModel


@pytest.fixture(autouse=True)
def private_model_fixtures(client):
    connection, app = client
    app.state.team_test_models = []
    yield
    # Model configuration is platform-wide; finished team fixtures must not
    # alter the enabled choices seen by subsequent, independently owned tests.
    connection.portal.call(
        app.state.db.execute,
        "UPDATE model_configs SET enabled=false WHERE id=ANY(%s::text[])",
        (app.state.team_test_models,),
    )


def agent(connection, name, model="test", **extra):
    response = connection.post("/api/v1/agents", json={"name": name, "model_id": model, **extra}, headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def publish(connection, identifier):
    response = connection.post(f"/api/v1/agents/{identifier}/publish", json={"revision": 1}, headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_tavily_enabled_team_builds_with_owned_identity_and_no_external_call(client, monkeypatch):
    connection, app = client
    previous = connection.portal.call(app.state.db.fetch, "SELECT * FROM search_settings")

    def forbidden_network(*args, **kwargs):
        raise AssertionError("No search request is authorized in this assembly test")

    monkeypatch.setattr("app.web_search.httpx.AsyncClient", forbidden_network)
    try:
        connection.portal.call(
            app.state.db.execute,
            "INSERT INTO search_settings(singleton,api_key_ciphertext,enabled) VALUES(true,%s,true) "
            "ON CONFLICT(singleton) DO UPDATE SET api_key_ciphertext=excluded.api_key_ciphertext,enabled=true",
            (app.state.models.vault.encrypt("local-assembly-only"),),
        )
        child = agent(connection, "只读成员")
        root = agent(
            connection,
            "联网团队",
            web_enabled=True,
            web_mode="tavily",
            team=[{"name": "helper", "version_id": publish(connection, child), "goal": "只读审阅"}],
        )
        app.state.runtime.model_factory = lambda _: ToolModel(
            responses=[
                AIMessage(
                    content="团队和联网工具已正确装配",
                    usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                )
            ]
        )
        conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
        started = connection.post(
            f"/api/v1/conversations/{conversation}/runs",
            json={"model_id": "test", "message": "只回复，不调用外部搜索"},
            headers=ORIGIN,
        )
        assert started.status_code == 202, started.text
        run = started.json()["run_id"]
        assert "run.completed" in connection.get(f"/api/v1/runs/{run}/events").text
        assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "SUCCEEDED"
    finally:
        if previous:
            connection.portal.call(
                app.state.db.execute,
                "UPDATE search_settings SET api_key_ciphertext=%s,enabled=%s WHERE singleton=true",
                (previous[0]["api_key_ciphertext"], previous[0]["enabled"]),
            )
        else:
            connection.portal.call(app.state.db.execute, "DELETE FROM search_settings")


def model(app, connection):
    identifier = "team-" + uuid4().hex[:12]
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext) "
        "SELECT %s,%s,model,base_url,api_key_ciphertext FROM model_configs WHERE id='test'",
        (identifier, identifier),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO model_capability_declarations(model_id,supports_tools) VALUES(%s,true)",
        (identifier,),
    )
    app.state.team_test_models.append(identifier)
    return identifier


def test_three_roles_share_two_parallel_slots_and_keep_separate_usage(client):
    connection, app = client
    first_model, second_model = model(app, connection), model(app, connection)
    first = agent(connection, "研究角色", first_model, instructions="原发布研究指令")
    second = agent(connection, "审阅角色", second_model)
    first_version, second_version = publish(connection, first), publish(connection, second)
    root = agent(
        connection,
        "协调团队",
        team=[
            {"name": "researcher", "version_id": first_version, "goal": "收集独立证据"},
            {"name": "reviewer", "version_id": second_version, "goal": "交叉核对证据"},
            {"name": "审阅者", "version_id": second_version, "goal": "独立审阅"},
        ],
    )
    assert (
        connection.put(
            f"/api/v1/agents/{first}",
            json={
                "name": "新研究角色",
                "model_id": first_model,
                "revision": 1,
                "instructions": "修改后的草稿，不应运行",
            },
            headers=ORIGIN,
        ).status_code
        == 200
    )
    observed = []
    child_tools = []
    concurrency = {"active": 0, "peak": 0}

    class Child(ToolModel):
        def bind_tools(self, tools, **kwargs):
            child_tools.append({tool.name for tool in tools})
            return self

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            observed.append(str(messages))
            concurrency["active"] += 1
            concurrency["peak"] = max(concurrency["active"], concurrency["peak"])
            try:
                await asyncio.sleep(0.08)
                return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            finally:
                concurrency["active"] -= 1

    def factory(configuration):
        if configuration.id == "test":
            return ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "research",
                                "name": "task",
                                "args": {"subagent_type": "researcher", "description": "调查"},
                            },
                            {
                                "id": "review",
                                "name": "task",
                                "args": {"subagent_type": "reviewer", "description": "复核"},
                            },
                            {
                                "id": "audit",
                                "name": "task",
                                "args": {"subagent_type": "审阅者", "description": "审阅"},
                            },
                        ],
                        usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
                    ),
                    AIMessage(
                        content="两份检查已完成",
                        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
                    ),
                ]
            )
        incoming, outgoing = (10, 1) if configuration.id == first_model else (20, 2)
        return Child(
            responses=[
                AIMessage(
                    content="成员证据",
                    usage_metadata={
                        "input_tokens": incoming,
                        "output_tokens": outgoing,
                        "total_tokens": incoming + outgoing,
                    },
                )
            ]
        )

    app.state.runtime.model_factory = factory
    conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
    response = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "分析一下"}, headers=ORIGIN
    )
    assert response.status_code == 202, response.text
    run = response.json()["run_id"]
    events = connection.get(f"/api/v1/runs/{run}/events").text
    assert connection.get(f"/api/v1/runs/{run}").json()["status"] == "SUCCEEDED", events
    rows = connection.get(f"/api/v1/runs/{run}/team").json()["items"]
    assert len(rows) == 3 and all(row["status"] == "SUCCEEDED" for row in rows), rows
    assert concurrency["peak"] == 2
    assert child_tools and all(
        not tools & {"task", "execute", "write_file", "edit_file", "delete"} for tools in child_tools
    )
    assert {row["model_id"] for row in rows} == {first_model, second_model}
    assert {row["input_tokens"] for row in rows} == {10, 20}
    assert "lease_token" not in str(rows)
    assert any("原发布研究指令" in text for text in observed)
    assert not any("修改后的草稿" in text for text in observed)
    usage = connection.get(f"/api/v1/conversations/{conversation}/usage").json()["total"]
    assert usage["calls"] == 5 and usage["input_tokens"] == 60 and usage["output_tokens"] == 10
    calls = connection.get(f"/api/v1/runs/{run}/diagnostics").json()["calls"]
    assert {call["role_name"] for call in calls} == {"lead", "researcher", "reviewer", "审阅者"}
    history = connection.get(f"/api/v1/conversations/{conversation}/messages").json()["items"]
    assert history[-1]["content"]["data"]["content"] == "两份检查已完成"
    assert not any(item["role"] == "assistant" and item["content"]["data"]["content"] == "成员证据" for item in history)


def test_team_rejects_nested_self_duplicate_foreign_and_revoked_versions(client):
    connection, app = client
    child = agent(connection, "独立成员")
    version = publish(connection, child)
    spec = {"name": "reviewer", "version_id": version, "goal": "检查"}
    root = agent(connection, "团队", team=[spec])
    root_version = publish(connection, root)
    for name, team in [
        ("嵌套", [{**spec, "version_id": root_version}]),
        ("重复", [spec, spec]),
        ("越权", [{**spec, "version_id": str(uuid4())}]),
    ]:
        result = connection.post(
            "/api/v1/agents", json={"name": name, "model_id": "test", "team": team}, headers=ORIGIN
        )
        assert result.status_code in {403, 422}, result.text
    assert (
        connection.put(
            f"/api/v1/agents/{child}",
            json={"name": "独立成员", "model_id": "test", "revision": 1, "team": [spec]},
            headers=ORIGIN,
        ).status_code
        == 422
    )
    assert (
        connection.put(
            f"/api/v1/agents/{child}/versions/{version}/state", json={"active": False}, headers=ORIGIN
        ).status_code
        == 200
    )
    conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
    assert (
        connection.post(
            f"/api/v1/conversations/{conversation}/runs",
            json={"model_id": "test", "message": "不能执行"},
            headers=ORIGIN,
        ).status_code
        == 403
    )


def test_non_chat_published_model_is_rejected_for_team_execution(client):
    connection, app = client
    child_model = model(app, connection)
    child = agent(connection, "类型变化成员", child_model)
    version = publish(connection, child)
    connection.portal.call(
        app.state.db.execute,
        "UPDATE model_configs SET kind='embedding',revision=revision+1 WHERE id=%s",
        (child_model,),
    )
    rejected = connection.post(f"/api/v1/agents/{child}/publish", json={"revision": 1}, headers=ORIGIN)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "AGENT_MODEL_UNSUPPORTED"
    # A snapshot written by an older release must still be rejected at team admission.
    connection.portal.call(
        app.state.db.execute,
        "UPDATE agent_versions SET snapshot=jsonb_set(snapshot,'{model_revision}',"
        "to_jsonb((SELECT revision FROM model_configs WHERE id=%s))) WHERE id=%s",
        (child_model, version),
    )
    assert version not in {item["id"] for item in connection.get("/api/v1/agents/team-options").json()["items"]}
    response = connection.post(
        "/api/v1/agents",
        json={
            "name": "不能执行向量成员",
            "model_id": "test",
            "team": [{"name": "helper", "version_id": version, "goal": "分析"}],
        },
        headers=ORIGIN,
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "TEAM_MODEL_UNSUPPORTED"


def test_parallel_call_admission_reserves_one_shared_ask_budget(client, monkeypatch):
    connection, app = client
    conversation = connection.post("/api/v1/conversations", json={}, headers=ORIGIN).json()["id"]
    app.state.settings.ask_worker_enabled = True
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "预算"}, headers=ORIGIN
    ).json()["run_id"]
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    monkeypatch.setattr("app.usage.ASK_POLICY", type(ASK_POLICY)("ask", 12, 5000))

    async def exercise():
        revision = (await app.state.models.get("test")).revision
        recorder = UsageRecorder(app.state.db, owner, UUID(run), "test", "ask", revision=revision, reserve=4096)
        results = await asyncio.gather(
            *[recorder.on_chat_model_start({}, [[]], run_id=uuid4()) for _ in range(2)], return_exceptions=True
        )
        assert sum(item is None for item in results) == 1
        assert sum(isinstance(item, APIError) and item.code == "ASK_BUDGET_EXCEEDED" for item in results) == 1
        rows = await app.state.db.fetch("SELECT count(*) AS n FROM model_usage WHERE run_id=%s", (run,))
        assert rows[0]["n"] == 1

    connection.portal.call(exercise)


def test_member_cancel_keeps_reported_usage_and_parent_can_finish(client):
    connection, app = client
    child_model = model(app, connection)
    child = agent(connection, "可取消成员", child_model)
    root = agent(
        connection, "取消团队", team=[{"name": "helper", "version_id": publish(connection, child), "goal": "分析"}]
    )
    ready, release = threading.Event(), threading.Event()

    class SlowChild(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            ready.set()
            for _ in range(1000):
                if release.is_set():
                    break
                await asyncio.sleep(0.01)
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def factory(configuration):
        if configuration.id == "test":
            return ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "slow",
                                "name": "task",
                                "args": {"subagent_type": "helper", "description": "独立分析"},
                            }
                        ],
                        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    ),
                    AIMessage(
                        content="成员取消后完成汇总",
                        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    ),
                ]
            )
        return SlowChild(
            responses=[
                AIMessage(
                    content="已产生但不展示的成员结果",
                    usage_metadata={"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                )
            ]
        )

    app.state.runtime.model_factory = factory
    conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "开始"}, headers=ORIGIN
    ).json()["run_id"]
    try:
        assert ready.wait(5)
        row = connection.get(f"/api/v1/runs/{run}/team").json()["items"][0]
        assert connection.post(f"/api/v1/runs/{run}/team/{row['id']}/cancel", headers=ORIGIN).status_code == 202
    finally:
        release.set()
    for _ in range(100):
        state = connection.get(f"/api/v1/runs/{run}").json()
        if state["status"] not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.03)
    assert state["status"] == "SUCCEEDED", state
    row = connection.get(f"/api/v1/runs/{run}/team").json()["items"][0]
    assert row["status"] == "CANCELLED" and row["reported_calls"] == 1
    assert row["input_tokens"] == 2 and row["output_tokens"] == 2


def test_durable_delegation_identity_limit_and_lost_lease(client):
    connection, app = client
    child = agent(connection, "缓存成员")
    root = agent(
        connection, "缓存团队", team=[{"name": "helper", "version_id": publish(connection, child), "goal": "分析"}]
    )
    app.state.settings.ask_worker_enabled = True
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "明确委派"}, headers=ORIGIN
    ).json()["run_id"]

    async def exercise():
        token = uuid4()
        rows = await app.state.db.fetch(
            "UPDATE task_runs SET status='RUNNING',lease_token=%s,lease_until=now()+interval '60 seconds' "
            "WHERE id=%s RETURNING agent_snapshot",
            (token, run),
        )
        snapshot = rows[0]["agent_snapshot"]

        async def guard():
            pass

        def team():
            return TeamRun(
                app.state.db,
                app.state.models,
                app.state.capabilities,
                None,
                UUID(run),
                owner,
                "ask",
                token,
                snapshot,
                guard,
            )

        original = team()
        member = original.members["helper"]
        arguments = {"description": "分析", "subagent_type": "helper"}
        row = await original.claim(member, "stable-id", arguments)
        await original.finish(row["id"], "SUCCEEDED", "已有证据")
        restored = team()
        cached = await restored.claim(member, "stable-id", arguments)
        assert cached["repeated"] and cached["result"] == "已有证据"
        with pytest.raises(APIError) as reused:
            await restored.claim(member, "stable-id", {**arguments, "description": "改变目标"})
        assert reused.value.code == "TEAM_CALL_ID_REUSED"
        for index in range(7):
            await restored.claim(member, str(index), arguments)
        with pytest.raises(APIError) as limit:
            await restored.claim(member, "ninth", arguments)
        assert limit.value.code == "TEAM_DELEGATION_LIMIT"
        await app.state.db.execute("UPDATE task_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (run,))
        await restored.finish(row["id"], "FAILED", "失效后台不能覆盖")
        assert (await app.state.db.fetch("SELECT result FROM team_calls WHERE id=%s", (row["id"],)))[0][
            "result"
        ] == "已有证据"
        with pytest.raises(APIError) as lost:
            await restored.claim(member, "after-lease-loss", arguments)
        assert lost.value.code == "TEAM_LEASE_LOST"

    connection.portal.call(exercise)


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local sandbox acceptance")
def test_work_team_reads_real_task_file_and_shares_parent_budget(client):
    from opensandbox.config import ConnectionConfig

    from app.durable_sandboxes import DurableSandboxes
    from app.work_artifacts import WorkArtifacts
    from app.work_engine import WorkEngine

    connection, app = client
    child_model = model(app, connection)
    child = agent(connection, "工作区审阅者", child_model)
    root = agent(
        connection,
        "工作团队",
        team=[{"name": "reviewer", "version_id": publish(connection, child), "goal": "只读核对任务输入"}],
    )
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post(
        "/api/v1/work-tasks", json={"title": "团队读取", "goal": "核对输入文件"}, headers=ORIGIN
    ).json()["id"]
    assert (
        connection.post(
            f"/api/v1/work-tasks/{task}/files",
            params={"path": "/input.txt", "revision": 1},
            content=b"team-work-input-evidence",
            headers=ORIGIN,
        ).status_code
        == 201
    )
    started = connection.post(
        f"/api/v1/work-tasks/{task}/runs", json={"revision": 2, "agent_id": root, "model_id": "test"}, headers=ORIGIN
    )
    assert started.status_code == 202, started.text
    run = started.json()["run_id"]
    observed = []

    class Reader(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            observed.append(str(messages))
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def factory(configuration):
        if configuration.id == "test":
            return ToolModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "review-work",
                                "name": "task",
                                "args": {"subagent_type": "reviewer", "description": "读取 input.txt"},
                            }
                        ],
                        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    ),
                    AIMessage(
                        content="已收到文件审阅证据",
                        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    ),
                ]
            )
        return Reader(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"id": "read-input", "name": "read_file", "args": {"file_path": "/input.txt"}}],
                    usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                ),
                AIMessage(
                    content="已读取输入证据", usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
                ),
            ]
        )

    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    sandbox = DurableSandboxes(
        app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)
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
        assert state["status"] == "COMPLETED", state
        assert state["model_calls"] == 4 and state["used_tokens"] == 10
        assert any("team-work-input-evidence" in text for text in observed)
        row = connection.get(f"/api/v1/work-runs/{run}/team").json()["items"][0]
        assert row["status"] == "SUCCEEDED" and row["model_id"] == child_model and row["calls"] == 2
    finally:
        connection.portal.call(sandbox.close)
        connection.portal.call(
            app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s AND state='RELEASED'", (run,)
        )
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run,))


def test_member_timeout_is_unknown_and_stops_parent_without_replay(client):
    connection, app = client
    child_model = model(app, connection)
    child = agent(connection, "未知请求成员", child_model)
    root = agent(
        connection, "未知结果团队", team=[{"name": "helper", "version_id": publish(connection, child), "goal": "分析"}]
    )
    calls = []

    class Lead(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append("lead")
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    class Broken(ToolModel):
        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append("child")
            raise TimeoutError("local deterministic double")

    def factory(configuration):
        if configuration.id == "test":
            return Lead(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "uncertain",
                                "name": "task",
                                "args": {"subagent_type": "helper", "description": "只请求一次"},
                            }
                        ],
                        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    )
                ]
            )
        return Broken(responses=[])

    app.state.runtime.model_factory = factory
    conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "一次调查"}, headers=ORIGIN
    ).json()["run_id"]
    connection.get(f"/api/v1/runs/{run}/events")
    state = connection.get(f"/api/v1/runs/{run}").json()
    assert state["status"] == "FAILED" and state["error_code"] == "TEAM_RESULT_UNKNOWN"
    assert calls == ["lead", "child"]
    row = connection.get(f"/api/v1/runs/{run}/team").json()["items"][0]
    assert row["status"] == "UNKNOWN" and row["reported_calls"] == 0


def test_team_resource_subset_and_other_account_cannot_read_or_invoke(client):
    from app.manage import add_user

    connection, app = client
    knowledge = connection.post("/api/v1/capabilities/knowledge", json={"name": "团队私有知识"}, headers=ORIGIN).json()[
        "id"
    ]
    child = agent(connection, "有知识的成员", knowledge_ids=[knowledge])
    version = publish(connection, child)
    spec = {"name": "helper", "version_id": version, "goal": "检查"}
    denied = connection.post(
        "/api/v1/agents", json={"name": "不能扩权", "model_id": "test", "team": [spec]}, headers=ORIGIN
    )
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "TEAM_RESOURCE_SCOPE"
    root = agent(connection, "授权团队", knowledge_ids=[knowledge], team=[spec])
    conversation = connection.post("/api/v1/conversations", json={"agent_id": root}, headers=ORIGIN).json()["id"]
    app.state.settings.ask_worker_enabled = True
    run = connection.post(
        f"/api/v1/conversations/{conversation}/runs", json={"model_id": "test", "message": "等待"}, headers=ORIGIN
    ).json()["run_id"]
    other = "other-team-" + uuid4().hex[:12]
    connection.portal.call(add_user, app.state.settings.database_url, other, "team-test-password")
    connection.cookies.clear()
    assert (
        connection.post(
            "/api/v1/auth/login", json={"username": other, "password": "team-test-password"}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert connection.get("/api/v1/agents/team-options").json()["items"] == []
    assert connection.get(f"/api/v1/runs/{run}/team").status_code == 404
    assert connection.post(f"/api/v1/runs/{run}/team/{uuid4()}/cancel", headers=ORIGIN).status_code == 404
    denied = connection.post(
        "/api/v1/agents", json={"name": "不能借用", "model_id": "test", "team": [spec]}, headers=ORIGIN
    )
    assert denied.status_code == 403
