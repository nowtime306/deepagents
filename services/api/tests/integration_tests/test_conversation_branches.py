"""Branches preserve source records, private attachments and independent checkpoints."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_api import DATABASE, ORIGIN, TestModel, add_user, run_async
from test_api import client as client


def ask(connection, conversation, message, attachments=None):
    response = connection.post(
        f"/api/v1/conversations/{conversation}/runs",
        headers=ORIGIN,
        json={
            "model_id": "test",
            "message": message,
            "attachment_ids": attachments or [],
        },
    )
    assert response.status_code == 202, response.text
    events = connection.get(f"/api/v1/runs/{response.json()['run_id']}/events")
    assert "run.completed" in events.text, events.text[-500:]
    return response.json()["run_id"]


def history(connection, identifier):
    return connection.get(f"/api/v1/conversations/{identifier}/messages").json()["items"]


def source(connection):
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN, json={"title": "分支来源"}).json()["id"]
    attachment = connection.post(
        f"/api/v1/conversations/{conversation}/attachments?name=note.txt",
        headers=ORIGIN,
        content="历史附件：约定暗号是松树".encode(),
    ).json()["id"]
    ask(connection, conversation, "请记住暗号", [attachment])
    return conversation, attachment, history(connection, conversation)


def test_fork_copies_attachments_survives_source_delete_and_seeds_only_once(client):
    connection, app = client
    original, file, messages = source(connection)
    before = history(connection, original)
    body = {"message_id": messages[-1]["id"], "request_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda _: connection.post(f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json=body),
                range(2),
            )
        )
    assert all(reply.status_code == 201 for reply in replies), [reply.text for reply in replies]
    fork = replies[0].json()["id"]
    assert replies[1].json()["id"] == fork
    copied = history(connection, fork)
    assert len(copied) == 2 and all(item["content"]["context_import"] for item in copied)
    assert history(connection, original) == before
    copied_file = copied[0]["content"]["attachments"][0]["id"]
    assert copied_file != file
    assert connection.get(f"/api/v1/conversations/{fork}/usage").json()["total"]["calls"] == 0
    assert connection.delete(f"/api/v1/conversations/{original}", headers=ORIGIN).status_code == 200
    assert connection.get(f"/api/v1/attachments/{copied_file}/download").content == "历史附件：约定暗号是松树".encode()
    prompts = []

    class InspectModel(TestModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                yield chunk

        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            prompts.append(messages)
            return "暗号是松树"

    app.state.runtime.model_factory = lambda _: InspectModel(responses=["unused"])
    ask(connection, fork, "暗号是什么？")
    ask(connection, fork, "再说一次")
    assert len(prompts) == 2
    for prompt in prompts:
        assert sum("历史附件：约定暗号是松树" in str(item.content) for item in prompt) == 1
        assert not any(getattr(item, "tool_calls", None) for item in prompt)
    branch = connection.portal.call(
        app.state.db.fetch,
        "SELECT source_id,source_message_id FROM conversation_branches WHERE conversation_id=%s",
        (fork,),
    )[0]
    assert branch["source_id"] is None and branch["source_message_id"] is None
    assert len(history(connection, fork)) == 6


def test_edit_imports_only_prior_history_retains_pending_attachment_and_conflicts(client):
    connection, _ = client
    original, file, messages = source(connection)
    body = {"message_id": messages[0]["id"], "request_id": str(uuid4()), "mode": "edit", "edit_text": "修改后的问题"}
    result = connection.post(f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json=body)
    assert result.status_code == 201, result.text
    fork = result.json()["id"]
    assert result.json()["draft"] == "修改后的问题" and history(connection, fork) == []
    pending = connection.get(f"/api/v1/conversations/{fork}/messages").json()["pending_attachments"]
    assert connection.get(f"/api/v1/conversations/{fork}/messages").json()["saved_draft"] == "修改后的问题"
    assert len(pending) == 1 and pending[0]["id"] != file
    assert (
        connection.post(
            f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json={**body, "edit_text": "不同问题"}
        ).status_code
        == 409
    )
    invalid = {**body, "request_id": str(uuid4()), "message_id": messages[-1]["id"]}
    assert (
        connection.post(f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json=invalid).status_code == 422
    )
    assert len(history(connection, original)) == 2
    ask(connection, fork, "修改后的问题", [pending[0]["id"]])
    assert connection.get(f"/api/v1/conversations/{fork}/messages").json()["saved_draft"] == ""
    assert (
        connection.post(f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json=body).json()["draft"] == ""
    )


def test_corrupted_attachment_rolls_back_branch_and_foreign_history_is_denied(client):
    connection, app = client
    original, file, messages = source(connection)
    body = {"message_id": messages[-1]["id"], "request_id": str(uuid4())}
    target = app.state.settings.data_dir / "attachments" / file
    raw = target.read_bytes()
    target.write_bytes(b"corrupted")
    try:
        response = connection.post(f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json=body)
        assert response.status_code == 503
        rows = connection.portal.call(
            app.state.db.fetch, "SELECT 1 FROM conversation_branches WHERE request_id=%s", (body["request_id"],)
        )
        assert not rows
    finally:
        target.write_bytes(raw)
    username = "分支越权-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    connection.post(
        "/api/v1/auth/login", headers=ORIGIN, json={"username": username, "password": "integration-password"}
    )
    assert connection.post(f"/api/v1/conversations/{original}/branches", headers=ORIGIN, json=body).status_code == 404
