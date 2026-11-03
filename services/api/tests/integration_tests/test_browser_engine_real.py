"""The complete Deep Agents approval loop preserves the actual browser between groups."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from opensandbox.config import ConnectionConfig
from test_api import ORIGIN
from test_api import client as client
from test_browser_daemon_real import fixture_script

from app.durable_sandboxes import DurableSandboxes
from app.work_artifacts import WorkArtifacts
from app.work_engine import WorkEngine


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local browser sandbox")
def test_deep_agents_retains_page_across_actual_approval_interrupts(client, monkeypatch):
    connection, app = client
    monkeypatch.setattr("app.work_network.public_addresses", lambda _: ["93.184.215.14"])
    monkeypatch.setattr(
        "app.browser_transport.DAEMON", fixture_script('<title>Engine fixture</title><input id="value">')
    )
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post(
        "/api/v1/work-tasks", headers=ORIGIN, json={"title": "真实浏览器审批循环", "goal": "跨审批保留输入"}
    ).json()["id"]
    response = connection.post(
        f"/api/v1/work-tasks/{task}/runs",
        headers=ORIGIN,
        json={"model_id": "test", "revision": 1, "environment": "browser", "network_domains": ["example.com"]},
    )
    assert response.status_code == 202, response.text
    run = response.json()["run_id"]

    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "browser_task",
                        "id": "first-page",
                        "args": {
                            "steps": [
                                {"op": "open", "parameters": {"url": "https://example.com/owner-fixture"}},
                                {"op": "fill", "parameters": {"selector": "#value", "text": "persistent-input"}},
                            ]
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "browser_task",
                        "id": "same-page",
                        "args": {
                            "steps": [
                                {"op": "screenshot", "parameters": {"path": "/same-page.png"}},
                            ]
                        },
                    }
                ],
            ),
            AIMessage(content="已跨审批保存同一页面截图"),
        ]
    )
    for message in model.responses:
        message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    service = DurableSandboxes(
        app.state.db, ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)
    )
    storage = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")
    engine = WorkEngine(
        app.state.db,
        app.state.models,
        service,
        storage,
        app.state.runtime.saver,
        lambda _: model,
        capabilities_factory=lambda _: app.state.capabilities,
    )

    def approve():
        pending = [
            item
            for item in connection.get(f"/api/v1/work-runs/{run}/approvals").json()["items"]
            if not item["decision"]
        ]
        assert len(pending) == 1
        item = pending[0]
        assert (
            connection.post(
                f"/api/v1/work-approvals/{item['id']}/decisions",
                headers=ORIGIN,
                json={"decision": "approve", "digest": item["digest"]},
            ).status_code
            == 200
        )

    try:
        assert connection.portal.call(engine.events.tick)
        assert connection.get(f"/api/v1/work-runs/{run}").json()["status"] == "WAITING_APPROVAL"
        approve()
        assert connection.portal.call(engine.events.tick)
        assert connection.get(f"/api/v1/work-runs/{run}").json()["status"] == "WAITING_APPROVAL"
        session = connection.portal.call(
            app.state.db.fetch, "SELECT * FROM work_sandbox_sessions WHERE run_id=%s AND state='READY'", (run,)
        )
        assert len(session) == 1 and session[0]["browser_persistent"] and session[0]["retention_required"]
        original = session[0]["id"]
        approve()
        assert connection.portal.call(engine.events.tick)
        assert connection.get(f"/api/v1/work-runs/{run}").json()["status"] == "COMPLETED"
        sessions = connection.portal.call(
            app.state.db.fetch, "SELECT * FROM work_sandbox_sessions WHERE run_id=%s", (run,)
        )
        assert sum(item["id"] == original for item in sessions) == 1
        assert all(item["state"] == "RELEASED" for item in sessions)
        artifacts = connection.get(f"/api/v1/work-runs/{run}/artifacts").json()["items"]
        screenshot = next(item for item in artifacts if item["path"] == "/same-page.png")
        assert connection.get(f"/api/v1/work-artifacts/{screenshot['id']}/download").content.startswith(b"\x89PNG")
    finally:
        sessions = connection.portal.call(
            app.state.db.fetch, "SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (run,)
        )
        for item in sessions:
            connection.portal.call(service.release, item["id"])
        connection.portal.call(service.close)
        files = connection.portal.call(app.state.db.fetch, "SELECT id FROM work_artifacts WHERE run_id=%s", (run,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s", (run,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run,))
        for item in files:
            storage.path(item["id"]).unlink(missing_ok=True)
        connection.delete(f"/api/v1/work-tasks/{task}", headers=ORIGIN)
