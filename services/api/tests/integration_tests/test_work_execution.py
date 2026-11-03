"""Work submission, approvals and real sandbox resume acceptance."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from opensandbox.config import ConnectionConfig
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client

from app.durable_sandboxes import DurableSandboxes
from app.work_artifacts import WorkArtifacts
from app.work_engine import WorkEngine
from app.work_queue import WorkQueue


def task(connection):
    return connection.post(
        "/api/v1/work-tasks", json={"title": "执行验收", "goal": "写入脚本并验证"}, headers=ORIGIN
    ).json()["id"]


def test_execution_gate_snapshot_and_cancel(client):
    connection, app = client
    identifier = task(connection)
    body = {"model_id": "test", "revision": 1}
    assert connection.post(f"/api/v1/work-tasks/{identifier}/runs", json=body, headers=ORIGIN).status_code == 503
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    result = connection.post(f"/api/v1/work-tasks/{identifier}/runs", json=body, headers=ORIGIN)
    assert result.status_code == 202
    run_id = result.json()["run_id"]
    assert connection.post(f"/api/v1/work-tasks/{identifier}/runs", json=body, headers=ORIGIN).status_code == 409
    assert connection.get(f"/api/v1/work-runs/{run_id}").json()["goal"] == "写入脚本并验证"
    assert connection.get(f"/api/v1/work-runs/{run_id}/events?after=-1").status_code == 422
    assert connection.post(f"/api/v1/work-runs/{run_id}/cancel", headers=ORIGIN).status_code == 202
    assert connection.get(f"/api/v1/work-runs/{run_id}").json()["status"] == "CANCELLED"
    # Every route follows the same account ownership boundary.
    username = "other-" + identifier
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login", json={"username": username, "password": "integration-password"}, headers=ORIGIN
    )
    for suffix in ("", "/events", "/approvals", "/artifacts", "/usage", "/diagnostics"):
        assert connection.get(f"/api/v1/work-runs/{run_id}{suffix}").status_code == 404
    assert connection.post(f"/api/v1/work-runs/{run_id}/cancel", headers=ORIGIN).status_code == 404
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run_id,))


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires local sandbox acceptance")
def test_real_worker_approval_resume_and_artifact_download(client):
    connection, app = client
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    skill_name = "work-skill-" + uuid4().hex[:8]
    import io
    import zipfile

    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "SKILL.md", f"---\nname: {skill_name}\ndescription: Work verification\n---\n\nskill-marker-verified"
        )
        archive.writestr("scripts/answer.py", "print(42)")
    skill = connection.post(
        "/api/v1/capabilities/skills",
        files={"file": ("skill.zip", package.getvalue())},
        headers=ORIGIN,
    ).json()["id"]
    agent = connection.post(
        "/api/v1/agents",
        json={"name": "执行技能-" + uuid4().hex[:8], "model_id": "test", "skill_ids": [skill]},
        headers=ORIGIN,
    ).json()["id"]
    identifier = task(connection)
    upload = connection.post(
        f"/api/v1/work-tasks/{identifier}/files",
        params={"path": "/input.txt", "revision": 1},
        content=b"source-input-marker",
        headers=ORIGIN,
    )
    assert upload.status_code == 201
    result = connection.post(
        f"/api/v1/work-tasks/{identifier}/runs",
        json={"model_id": "test", "revision": 2, "agent_id": agent},
        headers=ORIGIN,
    )
    assert result.status_code == 202
    run_id = result.json()["run_id"]
    key = (Path(__file__).parents[3] / "sandbox/.local/manager.key").read_text().strip()
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key, use_server_proxy=True)

    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "id": "planning",
                        "args": {"todos": [{"content": "读取输入并生成可运行成果", "status": "in_progress"}]},
                    }
                ],
            ),
            AIMessage(
                content="", tool_calls=[{"name": "read_file", "id": "input-read", "args": {"file_path": "/input.txt"}}]
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "id": "read-skill", "args": {"file_path": f"/skills/{skill_name}/SKILL.md"}}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "copy_skill_resource",
                        "id": "copy-skill",
                        "args": {"file_path": f"/skills/{skill_name}/scripts/answer.py", "target_path": "/answer.py"},
                    }
                ],
            ),
            AIMessage(
                content="", tool_calls=[{"name": "execute", "id": "exec", "args": {"command": "python3 answer.py"}}]
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "id": "verify-task",
                        "args": {
                            "subagent_type": "verifier",
                            "description": "读取answer.py并独立运行，检查输出是否为42",
                        },
                    }
                ],
            ),
            AIMessage(
                content="", tool_calls=[{"name": "read_file", "id": "verify-read", "args": {"file_path": "/answer.py"}}]
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "execute", "id": "verify-execute", "args": {"command": "python3 answer.py"}}],
            ),
            AIMessage(content="独立执行已通过，输出为42"),
            AIMessage(content="已执行，输出为42"),
        ]
    )
    for message in model.responses:
        message.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    storage = WorkArtifacts(app.state.db, app.state.settings.data_dir / "work-artifacts")
    service = DurableSandboxes(app.state.db, config)
    engine = WorkEngine(
        app.state.db,
        app.state.models,
        service,
        storage,
        app.state.runtime.saver,
        lambda _: model,
        capabilities_factory=lambda _: app.state.capabilities,
    )
    queue = WorkQueue(app.state.db)

    def tick():
        import time

        for _ in range(10):
            assert connection.portal.call(engine.events.tick)
            state = connection.get(f"/api/v1/work-runs/{run_id}").json()
            if state["status"] != "PENDING" or state["error_code"] != "WORK_CAPACITY_WAIT":
                return True
            time.sleep(5)
            connection.portal.call(app.state.db.execute,
                "UPDATE work_queue_runs SET available_after=now() WHERE id=%s AND status='PENDING'", (run_id,))
        pytest.fail("Actual host did not admit the isolated Coding fixture within 50 seconds")

    try:
        connection.portal.call(
            app.state.db.execute, "UPDATE work_queue_runs SET pause_requested=true WHERE id=%s", (run_id,)
        )
        assert tick()
        assert connection.get(f"/api/v1/work-runs/{run_id}").json()["status"] == "PAUSED"
        assert connection.post(f"/api/v1/work-runs/{run_id}/resume", headers=ORIGIN).json()["status"] == "PENDING"
        assert connection.get(f"/api/v1/work-runs/{run_id}/approvals").json()["items"] == []
        assert tick()
        assert connection.get(f"/api/v1/work-runs/{run_id}").json()["status"] == "WAITING_APPROVAL"
        plans = connection.get(f"/api/v1/work-runs/{run_id}/events").json()["items"]
        assert any(
            event["event"] == "plan.updated" and event["data"]["todos"][0]["status"] == "in_progress" for event in plans
        )
        approval = connection.get(f"/api/v1/work-runs/{run_id}/approvals").json()["items"][0]
        assert approval["payload"]["action_requests"][0]["name"] == "execute"
        url = f"/api/v1/work-approvals/{approval['id']}/decisions"
        assert connection.post(url, json={"digest": "0" * 64, "decision": "approve"}, headers=ORIGIN).status_code == 409
        decision = {
            "digest": approval["digest"],
            "decision": "edit",
            "edited_actions": [
                {
                    "name": "execute",
                    "args": {
                        "command": "python3 answer.py && printf edited-command-confirmed "
                        "&& echo changed-once >> input.txt",
                        "timeout": 10,
                    },
                }
            ],
        }
        assert connection.post(url, json=decision, headers=ORIGIN).status_code == 200
        assert connection.post(url, json=decision, headers=ORIGIN).status_code == 200
        assert tick()
        interim = connection.get(f"/api/v1/work-runs/{run_id}").json()
        assert interim["status"] == "WAITING_APPROVAL"
        pending = [
            a for a in connection.get(f"/api/v1/work-runs/{run_id}/approvals").json()["items"] if not a["decision"]
        ]
        assert len(pending) == 1
        second = pending[0]
        assert (
            connection.post(
                f"/api/v1/work-approvals/{second['id']}/decisions",
                json={"digest": second["digest"], "decision": "approve"},
                headers=ORIGIN,
            ).status_code
            == 200
        )
        assert tick()
        run = connection.get(f"/api/v1/work-runs/{run_id}").json()
        assert run["used_tokens"] >= 75
        usage = connection.get(f"/api/v1/work-runs/{run_id}/usage").json()["total"]
        assert usage["reported_calls"] == run["model_calls"]
        assert usage["unknown_calls"] == 0
        assert usage["input_tokens"] + usage["output_tokens"] == run["used_tokens"]
        assert run["result"]["verification"]["successful_commands"] == 2
        operations = connection.get(f"/api/v1/work-runs/{run_id}/operations").json()["items"]
        assert len(operations) == 2 and all(item["status"] == "SUCCEEDED" for item in operations)
        progress = connection.get(f"/api/v1/work-runs/{run_id}/events").json()["items"]
        assert any("edited-command-confirmed" in item["data"].get("output", "") for item in progress)
        assert any(
            item["event"] == "tool.completed" and "skill-marker-verified" in item["data"].get("output", "")
            for item in progress
        )
        assert run["status"] == "COMPLETED" and "42" in run["result"]["text"]
        artifacts = connection.get(f"/api/v1/work-runs/{run_id}/artifacts").json()["items"]
        input_artifact = next(item for item in artifacts if item["path"] == "/input.txt")
        final_input = connection.get(f"/api/v1/work-artifacts/{input_artifact['id']}/download").content
        assert b"source-input-marker" in final_input and final_input.count(b"changed-once") == 1
        artifact = next(item for item in artifacts if item["path"] == "/answer.py")
        output = connection.get(f"/api/v1/work-artifacts/{artifact['id']}/download")
        assert output.status_code == 200 and output.content == b"print(42)"
        assert "attachment;" in output.headers["content-disposition"]
        events = connection.get(f"/api/v1/work-runs/{run_id}/events").json()["items"]
        assert events[-1]["event"] == "run.completed"
        assert await_no_sessions(connection, app, run_id)
        # No worker can claim a completed run again.
        assert connection.portal.call(queue.claim) is None
    finally:
        connection.portal.call(service.close)
        records = connection.portal.call(app.state.db.fetch, "SELECT id FROM work_artifacts WHERE run_id=%s", (run_id,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_sandbox_sessions WHERE run_id=%s", (run_id,))
        connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (run_id,))
        for record in records:
            storage.path(record["id"]).unlink(missing_ok=True)
        connection.delete(f"/api/v1/work-tasks/{identifier}", headers=ORIGIN)


def await_no_sessions(connection, app, run_id):
    rows = connection.portal.call(
        app.state.db.fetch, "SELECT 1 FROM work_sandbox_sessions WHERE run_id=%s AND state<>'RELEASED'", (run_id,)
    )
    return not rows
