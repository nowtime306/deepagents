"""Opt-in local Coding acceptance: a confirmed stage resumes the real Work checkpoint once."""

import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from opensandbox.config import ConnectionConfig
from pydantic import Field
from test_api import ORIGIN
from test_api import client as client

from app.durable_sandboxes import DurableSandboxes
from app.models import ModelInput
from app.resource_runtime import current_scope, release_credentials
from app.work_artifacts import WorkArtifacts
from app.work_engine import WorkEngine


class CountingToolModel(FakeMessagesListChatModel):
    """Exercise real callbacks and tool binding without contacting any model supplier."""

    requests: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.requests.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class ObservedWorkEngine(WorkEngine):
    """Observe the real compiled graph without changing its execution or checkpoint handling."""

    async def agent_graph(self, run, model, backend, browser, guard):
        graph = await super().agent_graph(run, model, backend, browser, guard)
        self.observed_graph = graph
        return graph


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires explicit local Coding acceptance")
def test_real_coding_consumer_resumes_confirmed_configuration_from_original_checkpoint_once(client):
    connection, app = client  # The shared fixture additionally requires TEST_DATABASE_URL ending in _test.
    assert not connection.portal.call(
        app.state.db.fetch,
        "SELECT id FROM work_queue_runs WHERE status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')",
    ), "Use an isolated test database without other active Work runs"
    app.state.settings.work_enabled = True
    worker = uuid4()
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (worker,))
    owner = UUID(connection.get("/api/v1/auth/me").json()["id"])
    before_url, confirmed_url = "http://before-recovery.invalid/v1", "http://confirmed-recovery.invalid/v1"
    definition = ModelInput(name="recovery-" + uuid4().hex, model="controlled-fake", base_url=before_url,
                            supports_tools=True)
    model_id = connection.portal.call(app.state.models.save, definition)
    goal = "original-goal-" + uuid4().hex
    task = connection.post("/api/v1/work-tasks", headers=ORIGIN,
                           json={"title": "真实配置恢复检查", "goal": goal}).json()["id"]
    accepted = connection.post(f"/api/v1/work-tasks/{task}/runs", headers=ORIGIN,
                               json={"revision": 1, "model_id": model_id, "environment": "coding"})
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run_id"]
    marker = "controlled-model-completed-once"
    model = CountingToolModel(responses=[AIMessage(
        content=marker, usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )])
    configured = []

    def factory(config):
        configured.append((current_scope().phase, config.revision, config.base_url, config.temperature))
        return model

    key_path = Path(__file__).parents[3] / "sandbox/.local/manager.key"
    assert key_path.is_file(), "RUN_LOCAL_SANDBOX=1 requires the existing local sandbox manager key"
    management = ConnectionConfig(domain="127.0.0.1:18180", api_key=key_path.read_text().strip(), use_server_proxy=True)
    storage = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")
    services = []

    def consumer():
        service = DurableSandboxes(app.state.db, management)
        services.append(service)
        return ObservedWorkEngine(app.state.db, app.state.models, service, storage, app.state.runtime.saver, factory,
                                  capabilities_factory=lambda _: app.state.capabilities)

    def state():
        return connection.portal.call(app.state.db.fetch, "SELECT * FROM work_queue_runs WHERE id=%s", (run_id,))[0]

    def checkpoint():
        return connection.portal.call(app.state.runtime.saver.aget_tuple,
                                      {"configurable": {"thread_id": "work:" + run_id}})

    def graph_state(engine):
        return connection.portal.call(engine.observed_graph.aget_state,
                                      {"configurable": {"thread_id": "work:" + run_id}})

    def tick(engine):
        # Real host capacity may be transiently unavailable; never replace the sandbox or force run success.
        for attempt in range(10):
            assert connection.portal.call(engine.events.tick), "The isolated queued run was not claimed"
            row = state()
            if row["status"] != "PENDING" or row["error_code"] != "WORK_CAPACITY_WAIT":
                return row
            if attempt != 9:
                time.sleep(5)
                connection.portal.call(app.state.db.execute,
                    "UPDATE work_queue_runs SET available_after=now() WHERE id=%s AND status='PENDING'", (run_id,))
        pytest.fail("Actual local Coding capacity was not available within the bounded acceptance window")

    try:
        snapshot = state()["agent_snapshot"]
        assert not snapshot.get("id") and not snapshot.get("route_selection_id")
        assert all(not snapshot.get(key) for key in (
            "skill_ids", "mcp_ids", "knowledge_ids", "team", "team_pins", "workflow_version_ids",
            "memory_enabled", "web_enabled",
        ))
        first = consumer()
        connection.portal.call(app.state.db.execute,
                               "UPDATE work_queue_runs SET pause_requested=true WHERE id=%s", (run_id,))
        paused = tick(first)
        assert paused["status"] == "PAUSED" and not paused["error_code"]
        assert paused["resume_decision"] and all(value is True for value in paused["resume_decision"].values())
        original = checkpoint()
        assert original is not None
        humans = [message for message in graph_state(first).values["messages"]
                  if isinstance(message, HumanMessage)]
        assert len(humans) == 1 and goal in humans[0].content
        assert model.requests == []
        assert all(item[:3] == (1, 1, before_url) for item in configured)
        assert not connection.get(f"/api/v1/work-runs/{run_id}/approvals").json()["items"]
        assert not connection.get(f"/api/v1/work-runs/{run_id}/operations").json()["items"]

        # Reconstruct the consumer and clear only this run's process cache to simulate a cold worker.
        connection.portal.call(services[0].close)
        connection.portal.call(release_credentials, owner, "work", run_id)
        changed = definition.model_copy(update={"revision": 1, "base_url": confirmed_url, "temperature": 0.4})
        connection.portal.call(app.state.models.save, changed, model_id)
        resumed = connection.post(f"/api/v1/work-runs/{run_id}/resume", headers=ORIGIN)
        assert resumed.status_code == 202 and resumed.json()["status"] == "PENDING", resumed.text
        second = consumer()
        before_recovery = list(configured)
        waiting = tick(second)
        assert waiting["status"] == "PAUSED" and waiting["error_code"] == "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED"
        assert configured == before_recovery and model.requests == []
        assert checkpoint().config == original.config  # The rejected target never reached graph execution.

        endpoint = f"/api/v1/resource-runs/work/{run_id}/configuration-recovery"
        preview = connection.get(endpoint)
        assert preview.status_code == 200 and preview.json()["can_confirm"], preview.text
        confirmed = connection.post(endpoint, headers=ORIGIN, json={
            "expected_phase": preview.json()["phase"], "confirmation_digest": preview.json()["confirmation_digest"],
        })
        assert confirmed.status_code == 202 and confirmed.json()["phase"] == 2, confirmed.text
        finished = tick(second)
        assert finished["status"] == "COMPLETED" and marker in finished["result"]["text"], finished
        assert configured[len(before_recovery):]
        assert all(item == (2, 2, confirmed_url, 0.4) for item in configured[len(before_recovery):])
        final_humans = [message for message in graph_state(second).values["messages"]
                        if isinstance(message, HumanMessage)]
        assert len(final_humans) == 1 and final_humans[0].id == humans[0].id
        assert final_humans[0].content == humans[0].content
        assert len(model.requests) == 1
        assert len([message for message in model.requests[0] if isinstance(message, HumanMessage)]) == 1
        usage = connection.portal.call(app.state.db.fetch,
            "SELECT u.resource_phase,u.model_revision,u.status,s.phase,s.used_at FROM model_usage u "
            "JOIN run_resource_snapshots s ON s.snapshot_id=u.resource_snapshot_id WHERE u.run_id=%s", (run_id,))
        assert len(usage) == 1 and usage[0]["resource_phase"] == usage[0]["phase"] == 2
        assert usage[0]["model_revision"] == 2 and usage[0]["status"] == "KNOWN" and usage[0]["used_at"]
        evidence = connection.portal.call(app.state.db.fetch,
            "SELECT phase,resource_kind,used_at FROM run_resource_snapshots WHERE run_id=%s ORDER BY phase", (run_id,))
        assert len(evidence) == 2 and all(row["resource_kind"] == "model" for row in evidence)
        assert evidence[0]["phase"] == 1 and evidence[0]["used_at"] is None
        assert not connection.get(f"/api/v1/work-runs/{run_id}/approvals").json()["items"]
        assert not connection.get(f"/api/v1/work-runs/{run_id}/operations").json()["items"]
        assert not connection.portal.call(second.events.tick)
        assert len(model.requests) == 1 and not state()["resource_resume_pending"]
    finally:
        connection.portal.call(release_credentials, owner, "work", run_id)
        sessions = connection.portal.call(app.state.db.fetch,
            "SELECT id FROM work_sandbox_sessions WHERE run_id=%s AND state<>'RELEASED'", (run_id,))
        try:
            for session in sessions:
                assert connection.portal.call(services[-1].release, session["id"]), "Keep unresolved sandbox evidence"
        finally:
            for service in services:
                connection.portal.call(service.close)
        artifacts = connection.portal.call(
            app.state.db.fetch, "SELECT id FROM work_artifacts WHERE run_id=%s", (run_id,)
        )
        connection.portal.call(app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s", (run_id,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run_id,))
        for artifact in artifacts:
            storage.path(artifact["id"]).unlink(missing_ok=True)
        connection.delete(f"/api/v1/work-tasks/{task}", headers=ORIGIN)
        connection.portal.call(app.state.db.execute, "UPDATE model_configs SET enabled=false WHERE id=%s", (model_id,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_worker_instances WHERE id=%s", (worker,))
