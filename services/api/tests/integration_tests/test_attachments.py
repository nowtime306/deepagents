"""Document attachments reach the graph through immutable owner-checked snapshots."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_attachment_snapshot_download_and_conversation_cleanup(client):
    connection, app = client
    identifier = connection.post("/api/v1/conversations", json={"title": "附件验收"}, headers=ORIGIN).json()["id"]
    data = "这是本次附件的独有验证内容 marker-attachment-42。".encode()
    uploaded = connection.post(
        f"/api/v1/conversations/{identifier}/attachments?name=notes.txt", content=data, headers=ORIGIN
    )
    assert uploaded.status_code == 201
    attachment = uploaded.json()["id"]
    assert connection.get(f"/api/v1/attachments/{attachment}/download").content == data
    assert (
        connection.get(f"/api/v1/conversations/{identifier}/messages").json()["pending_attachments"][0]["id"]
        == attachment
    )
    result = connection.post(
        f"/api/v1/conversations/{identifier}/runs",
        json={"message": "读取附件", "model_id": "test", "attachment_ids": [attachment]},
        headers=ORIGIN,
    )
    assert result.status_code == 202
    connection.get("/api/v1/runs/" + result.json()["run_id"] + "/events")
    checkpoint = connection.portal.call(app.state.runtime.saver.aget_tuple, {"configurable": {"thread_id": identifier}})
    messages = checkpoint.checkpoint["channel_values"]["messages"]
    assert any(message.type == "human" and "marker-attachment-42" in message.content for message in messages)
    history = connection.get(f"/api/v1/conversations/{identifier}/messages").json()
    assert not history["pending_attachments"]
    assert history["items"][0]["content"]["attachments"][0]["id"] == attachment
    assert connection.delete(f"/api/v1/attachments/{attachment}", headers=ORIGIN).status_code == 409
    stored = app.state.settings.data_dir / "attachments" / attachment
    assert stored.exists()
    assert connection.delete(f"/api/v1/conversations/{identifier}", headers=ORIGIN).status_code == 200
    assert not stored.exists()
    assert connection.get(f"/api/v1/attachments/{attachment}/download").status_code == 404


def test_unsupported_oversize_and_cross_conversation_attachments(client):
    connection, app = client
    first = connection.post("/api/v1/conversations", json={"title": "附件私有"}, headers=ORIGIN).json()["id"]
    second = connection.post("/api/v1/conversations", json={"title": "其他对话"}, headers=ORIGIN).json()["id"]
    base = f"/api/v1/conversations/{first}/attachments"
    assert connection.post(base + "?name=photo.png", content=b"png", headers=ORIGIN).status_code == 422
    assert (
        connection.post(base + "?name=large.txt", content=b"x" * (4 * 1024 * 1024 + 1), headers=ORIGIN).status_code
        == 413
    )
    assert connection.post(base + "?name=bad.txt", content=b"\xff", headers=ORIGIN).status_code == 422
    attachment = connection.post(base + "?name=private.txt", content=b"private test content", headers=ORIGIN).json()[
        "id"
    ]
    assert (
        connection.post(
            f"/api/v1/conversations/{second}/runs",
            json={"message": "读取", "model_id": "test", "attachment_ids": [attachment]},
            headers=ORIGIN,
        ).status_code
        == 422
    )
    other = "attachment-other-" + str(uuid4())
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert connection.get(f"/api/v1/attachments/{attachment}/download").status_code == 404
    assert connection.get(base).status_code == 404
    assert connection.post(base + "?name=attack.txt", content=b"text", headers=ORIGIN).status_code == 404
