"""Opt-in real extension + PostgreSQL Work approval acceptance; file sandbox/model are doubles."""

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from deepagents.backends.state import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.types import Command
from psycopg.types.json import Jsonb
from test_api import ORIGIN
from test_api import client as client

from app.browser_skill_runtime import BrowserSkillRuntime
from app.browser_skill_trace import import_bundle
from app.operation_journal import OperationJournal
from app.work_engine import WorkEngine
from app.work_queue import WorkQueue

ROOT = os.getenv("BROWSER_SKILL_REAL_FIXTURE_ROOT", "")


@pytest.mark.skipif(not ROOT, reason="Requires separate empty-profile real extension fixture")
def test_real_work_approval_reject_then_approve_actual_extension(client):
    connection, app = client
    root = Path(ROOT).resolve()
    assert root.name.startswith("browser-skill-work-e2e-")
    manifest = json.loads((root / "fixture.json").read_text(encoding="utf-8"))
    assert manifest["url"] == "https://example.com/owned-bsk-fixture"
    draft = import_bundle(root / "bundle")
    assert [s["op"] for s in draft["steps"]] == ["navigate", "fill", "select", "click"]
    assert "RecordedFixture8672" not in json.dumps(draft)
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    identifier = uuid4()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO browser_skill_recordings(id,owner_id,connection_id,title,status,draft) "
        "VALUES(%s,%s,'local','实际扩展路径','review',%s)",
        (identifier, owner, Jsonb(draft)),
    )
    published = connection.post(
        f"/api/v1/browser-skills/recordings/{identifier}/publish", json={"revision": 1}, headers=ORIGIN
    )
    assert published.status_code == 200, published.text
    skill_id = published.json()["skill_id"]
    skill = connection.get(f"/api/v1/capabilities/skills/{skill_id}").json()
    agent = connection.post(
        "/api/v1/agents",
        headers=ORIGIN,
        json={"name": "real-fixture-" + uuid4().hex[:8], "model_id": "test", "skill_ids": [skill_id]},
    ).json()["id"]
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))

    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    class RealRuntime(BrowserSkillRuntime):
        def __init__(self):
            super().__init__(app.state.db, app.state.settings.data_dir)
            self.root, self.instance = root, manifest["instance"]
            self.executable = manifest["executable"]
            self.calls, self.dom = [], ""

        async def command(self, *args, **kwargs):
            self.calls.append(args[0])
            if args[:2] == ("session", "stop"):
                observed = json.loads(await super().command("observe", "--session", args[2], "--json"))
                self.dom = json.dumps(observed, ensure_ascii=False)
            return await super().command(*args, **kwargs)

    runtime = RealRuntime()
    evidence = []
    for decision in ("reject", "approve"):
        task = connection.post(
            "/api/v1/work-tasks",
            headers=ORIGIN,
            json={"title": "真实本机路径审批", "goal": "只操作独立fixture虚构数据"},
        ).json()["id"]
        submitted = connection.post(
            f"/api/v1/work-tasks/{task}/runs",
            headers=ORIGIN,
            json={
                "model_id": "test",
                "revision": 1,
                "environment": "browser",
                "agent_id": agent,
                "allow_local_browser": True,
                "network_domains": ["example.com"],
            },
        )
        assert submitted.status_code == 202, submitted.text
        queue = WorkQueue(app.state.db)
        run = connection.portal.call(queue.claim)
        assert str(run["id"]) == submitted.json()["run_id"]
        inputs = {
            step["parameter"]: "WorkApproved291" if step["op"] == "fill" else "blue"
            for step in draft["steps"]
            if "parameter" in step
        }
        args = {
            "skill_id": skill_id,
            "version": 1,
            "parameters": inputs,
            "execution_boundary": "local_authenticated_browser",
            "skill_name": skill["name"],
            "step_count": 4,
        }
        model = ToolModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "run_browser_skill", "id": "host-path", "args": args}]),
                AIMessage(content="fixture完成"),
            ]
        )
        engine = WorkEngine(
            app.state.db,
            app.state.models,
            None,
            None,
            app.state.runtime.saver,
            lambda _, fixed_model=model: fixed_model,
            capabilities_factory=lambda _: app.state.capabilities,
            browser_skills=runtime,
        )
        config = {"configurable": {"thread_id": "work:" + str(run["id"])}}
        before = len(runtime.calls)

        async def initial(run=run, engine=engine, model=model, config=config):
            backend = StateBackend()
            backend.guard = lambda: app.state.db.fetch("SELECT 1")
            backend.journal = OperationJournal(app.state.db, run)
            graph = await engine.agent_graph(run, model, backend, None, backend.guard)
            result = await graph.ainvoke({"messages": [{"role": "user", "content": "执行fixture路径"}]}, config)
            interrupts = result.get("__interrupt__", [])
            assert interrupts
            await engine.pause(run, interrupts)

        connection.portal.call(initial)
        assert len(runtime.calls) == before
        approval = connection.get(f"/api/v1/work-runs/{run['id']}/approvals").json()["items"][-1]
        action = approval["payload"]["action_requests"][0]
        assert action["name"] == "run_browser_skill" and action["args"] == args
        assert "完整录制路径" in action["description"] and "无网络硬隔离" in action["description"]
        response = connection.post(
            f"/api/v1/work-approvals/{approval['id']}/decisions",
            headers=ORIGIN,
            json={"digest": approval["digest"], "decision": decision},
        )
        assert response.status_code == 200, response.text
        resumed = connection.portal.call(queue.claim)
        assert resumed["id"] == run["id"]

        async def resume(resumed=resumed, engine=engine, model=model, config=config, decision=decision, args=args):
            backend = StateBackend()
            backend.guard = lambda: app.state.db.fetch("SELECT 1")
            backend.journal = OperationJournal(app.state.db, resumed)
            await engine.consume_approvals(resumed)
            graph = await engine.agent_graph(resumed, model, backend, None, backend.guard)
            result = await graph.ainvoke(Command(resume=resumed["resume_decision"]), config)
            assert not result.get("__interrupt__")
            if decision == "approve":
                with pytest.raises(PermissionError):
                    await backend.journal.start("run_browser_skill", args)

        connection.portal.call(resume)
        operations = connection.portal.call(
            app.state.db.fetch,
            "SELECT status FROM operation_executions WHERE run_id=%s AND name='run_browser_skill'",
            (run["id"],),
        )
        if decision == "reject":
            assert len(runtime.calls) == before and operations == []
        else:
            assert "WorkApproved291:blue" in runtime.dom
            assert operations == [{"status": "SUCCEEDED"}]
            assert runtime.calls[before:].count("fill") == 1 and runtime.calls[before:].count("click") == 1
        evidence.append(
            {
                "decision": decision,
                "cli_calls": runtime.calls[before:],
                "operations": operations,
                "approval_boundary": action["args"]["execution_boundary"],
                "approved_steps": 4,
                "dom_result_verified": "WorkApproved291:blue" if decision == "approve" else None,
            }
        )
        assert connection.portal.call(lambda q=queue, r=resumed: q.finish(r["id"], r["lease_token"], success=True))
    (root / "work-approval-evidence.json").write_text(
        json.dumps(
            {
                "passed": True,
                "cases": evidence,
                "real": [
                    "official extension",
                    "CLI",
                    "PostgreSQL",
                    "WorkQueue",
                    "WorkEngine.agent_graph",
                    "approval API",
                    "OperationJournal",
                ],
                "doubles": [
                    "ToolModel",
                    "StateBackend instead of sandbox filesystem",
                    "HTTPS fixture fulfilled locally",
                ],
                "not_covered": ["WorkEngine sandbox lifecycle and billing callbacks", "production application UI"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
