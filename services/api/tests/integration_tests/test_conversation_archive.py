"""Archive and keyset pagination preserve private conversation histories."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client


def test_archive_keeps_history_and_requires_restore_before_new_runs(client):
    connection, app = client
    identifier = connection.post("/api/v1/conversations", json={"title": "归档验证"}, headers=ORIGIN).json()["id"]
    assert (
        connection.post(
            f"/api/v1/conversations/{identifier}/archive", json={"archived": True}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert identifier not in [item["id"] for item in connection.get("/api/v1/conversations").json()["items"]]
    assert identifier in [item["id"] for item in connection.get("/api/v1/conversations?archived=true").json()["items"]]
    assert connection.get(f"/api/v1/conversations/{identifier}/messages").json()["archived"] is True
    assert (
        connection.post(
            f"/api/v1/conversations/{identifier}/runs", json={"message": "你好", "model_id": "test"}, headers=ORIGIN
        ).status_code
        == 409
    )
    assert (
        connection.post(
            f"/api/v1/conversations/{identifier}/archive", json={"archived": False}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert connection.get(f"/api/v1/conversations/{identifier}/messages").json()["archived"] is False
    other = "archive-other-" + str(uuid4())
    run_async(add_user(DATABASE, other, "integration-password"))
    connection.cookies.clear()
    connection.post("/api/v1/auth/login", json={"username": other, "password": "integration-password"}, headers=ORIGIN)
    assert (
        connection.post(
            f"/api/v1/conversations/{identifier}/archive", json={"archived": True}, headers=ORIGIN
        ).status_code
        == 404
    )


def test_conversation_and_message_pages_have_no_gaps_or_duplicates(client):
    connection, app = client
    identifier = connection.post("/api/v1/conversations", json={"title": "分页验证"}, headers=ORIGIN).json()["id"]
    owner = connection.portal.call(app.state.db.fetch, "SELECT owner_id FROM conversations WHERE id=%s", (identifier,))[
        0
    ]["owner_id"]
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO conversations(id,owner_id,title) "
        "SELECT gen_random_uuid(),%s,'分页对话'||n FROM generate_series(1,60) n",
        (owner,),
    )
    first = connection.get("/api/v1/conversations").json()
    second = connection.get("/api/v1/conversations?before=" + first["next_cursor"]).json()
    assert len(first["items"]) == 50 and len(second["items"]) == 11
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    run_id = uuid4()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO task_runs(id,conversation_id,model_id,status) VALUES(%s,%s,'test','SUCCEEDED')",
        (run_id, identifier),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content,created_at) "
        "SELECT gen_random_uuid(),%s,%s,'assistant',jsonb_build_object('text',n::text),now()+n*interval '1 second' "
        "FROM generate_series(1,205) n",
        (identifier, run_id),
    )
    pages = []
    before = ""
    while True:
        page = connection.get(
            f"/api/v1/conversations/{identifier}/messages" + ("?before=" + before if before else "")
        ).json()
        pages.insert(0, page["items"])
        before = page["next_before"]
        if not before:
            break
    messages = [item for page in pages for item in page]
    assert [int(message["content"]["text"]) for message in messages] == list(range(1, 206))
    assert len({message["id"] for message in messages}) == 205
