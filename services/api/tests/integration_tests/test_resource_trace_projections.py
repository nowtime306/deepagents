"""HTTP projection contracts from controlled persisted evidence; no inference or browser execution."""

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from test_api import DATABASE, ORIGIN, add_user
from test_api import client as client

from app.resource_runtime import freeze_resource, resource_run


@pytest.fixture(params=["agent", "memory", "knowledge"])
def projection(client, request):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    kind = request.param
    identifier = owner if kind == "memory" else str(uuid4())
    private = "TRACE_PRIVATE_" + uuid4().hex
    ciphertext = app.state.models.vault.encrypt(private)
    connection.portal.call(app.state.db.execute,
        "UPDATE model_configs SET api_key_ciphertext=%s WHERE id='test'", (ciphertext,))
    if kind == "agent":
        connection.portal.call(app.state.db.execute,
            "INSERT INTO agents(id,owner_id,name,instructions,model_id) VALUES(%s,%s,%s,%s,'test')",
            (identifier, owner, uuid4().hex, private))
        safe = {"id": identifier, "revision": 1, "published_version_id": None, "published_version": None}
    elif kind == "memory":
        connection.portal.call(app.state.db.execute,
            "INSERT INTO memory_settings(owner_id,embedding_model_id,enabled) VALUES(%s,'test',true)", (owner,))
        safe = {"embedding_model_id": "test", "enabled": True}
    else:
        connection.portal.call(app.state.db.execute,
            "INSERT INTO knowledge_bases(id,owner_id,name,description,keyword_min_coverage,chunk_size,"
            "chunk_overlap,processing_revision) VALUES(%s,%s,%s,%s,.25,800,120,1)",
            (identifier, owner, uuid4().hex, private))
        safe = {"embedding_model_id": None, "rerank_model_id": None, "keyword_min_coverage": .25,
                "chunk_size": 800, "chunk_overlap": 120, "processing_revision": 1,
                "index_compatibility": [{"vector_fingerprint": "recorded-vector", "vector_dimensions": 2}]}
        connection.portal.call(app.state.db.execute,
            "INSERT INTO documents(id,knowledge_id,name,size,status,vector_status,"
            "vector_fingerprint,vector_dimensions) "
            "VALUES(%s,%s,%s,1,'READY','READY','recorded-vector',2)", (uuid4(), identifier, private))
    conversation = connection.post("/api/v1/conversations", headers=ORIGIN, json={}).json()["id"]
    run = uuid4()
    connection.portal.call(app.state.db.execute,
        "INSERT INTO task_runs(id,conversation_id,model_id,status,agent_snapshot) "
        "VALUES(%s,%s,'test','FAILED',%s)", (run, conversation, Jsonb({"instructions": private})))

    async def record():
        # Controlled prior resolved evidence, not a claim that the model/retriever executed.
        async with resource_run(app.state.db, UUID(owner), "ask", run):
            await freeze_resource(kind, identifier, 1, {"instructions": private},
                                  vault=app.state.models.vault, snapshot=safe)

    connection.portal.call(record)
    return connection, app, owner, kind, identifier, run, safe, private, ciphertext


def evidence(projection):
    connection, _, _, kind, identifier, run, _, private, ciphertext = projection
    response = connection.get(f"/api/v1/resource-runs/ask/{run}/configurations")
    assert response.status_code == 200, response.text
    assert all(value not in response.text for value in
               (private, ciphertext, "execution_config", "api_key", "ciphertext", "instructions", "password_hash"))
    items = response.json()["items"]
    assert len(items) == 1 and items[0]["resource_kind"] == kind and items[0]["resource_id"] == identifier
    return response.json(), items[0]


def test_current_projection_normalizes_json_and_preserves_recorded_missing_fields(projection):
    connection, app, _, kind, identifier, run, safe, _, _ = projection
    _, original = evidence(projection)
    assert original["snapshot"] == safe and original["differences"] == [] and original["available"]
    assert original["state"] == "resolved" and original["used_at"] is None and original["calls"] == []
    if kind == "agent":
        # PostgreSQL returns UUID, while JSONB's recorded ID is a string: no spurious identity difference.
        assert original["current"]["id"] == identifier and "id" not in original["differences"]
        connection.portal.call(app.state.db.execute,
            "UPDATE agents SET revision=revision+1,enabled=false WHERE id=%s", (identifier,))
        expected = {"revision"}
    elif kind == "memory":
        connection.portal.call(app.state.db.execute,
            "UPDATE memory_settings SET embedding_model_id=NULL,enabled=false,revision=revision+1 WHERE owner_id=%s",
            (identifier,))
        expected = {"embedding_model_id", "enabled"}
    else:
        connection.portal.call(app.state.db.execute,
            "UPDATE knowledge_bases SET keyword_min_coverage=.75,chunk_size=900 WHERE id=%s", (identifier,))
        connection.portal.call(app.state.db.execute,
            "UPDATE documents SET vector_fingerprint='current-vector',vector_dimensions=3 WHERE knowledge_id=%s",
            (identifier,))
        expected = {"keyword_min_coverage", "chunk_size", "index_compatibility"}
    _, changed = evidence(projection)
    assert changed["snapshot"] == original["snapshot"] and changed["snapshot_id"] == original["snapshot_id"]
    assert changed["revision"] == original["revision"] == "1" and set(changed["differences"]) == expected
    assert changed["available"] is (kind == "knowledge")
    if kind == "agent":
        assert changed["current"]["revision"] == 2 and changed["current"]["enabled"] is False
        assert changed["current"]["id"] == identifier and "id" not in changed["differences"]
    elif kind == "memory":
        assert changed["current"] == {"embedding_model_id": None, "enabled": False}
    else:
        assert changed["current"]["keyword_min_coverage"] == .75 and changed["current"]["chunk_size"] == 900
        assert changed["current"]["index_compatibility"] == [
            {"vector_fingerprint": "current-vector", "vector_dimensions": 3}]
    # Legacy evidence without this field must not be filled from today's live value.
    omitted = "revision" if kind == "agent" else "embedding_model_id"
    recorded = deepcopy(safe)
    recorded.pop(omitted)
    connection.portal.call(app.state.db.execute,
        "UPDATE run_resource_snapshots SET snapshot=%s WHERE run_id=%s", (Jsonb(recorded), run))
    _, legacy = evidence(projection)
    assert omitted not in legacy["snapshot"] and omitted not in legacy["current"]
    assert omitted not in legacy["differences"]
    table, column = {"agent": ("agents", "id"), "memory": ("memory_settings", "owner_id"),
                     "knowledge": ("knowledge_bases", "id")}[kind]
    connection.portal.call(app.state.db.execute,
        sql.SQL("DELETE FROM {} WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(column)), (identifier,))
    _, missing = evidence(projection)
    assert missing["current"] is None and not missing["available"] and missing["differences"] == []
    assert missing["snapshot"] == recorded and missing["snapshot_id"] == original["snapshot_id"]


def test_trace_readonly_permission_and_cross_owner_admin_are_live(projection):
    connection, app, owner, _, _, run, _, _, _ = projection
    role = "trace-reader-" + uuid4().hex
    connection.portal.call(app.state.db.execute,
        "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)", (role, role, Jsonb(["chat.read"])))
    connection.portal.call(app.state.db.execute,
        "UPDATE users SET is_admin=false,role_id=%s WHERE id=%s", (role, owner))
    body, _ = evidence(projection)
    assert not body["can_recover"] and not body["can_view_global_history"]
    connection.portal.call(app.state.db.execute,
        "UPDATE platform_roles SET permissions='[]'::jsonb WHERE id=%s", (role,))
    endpoint = f"/api/v1/resource-runs/ask/{run}/configurations"
    assert connection.get(endpoint).status_code == 403
    other = "trace-admin-" + uuid4().hex
    connection.portal.call(add_user, DATABASE, other, "integration-password")
    connection.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE username=%s", (other,))
    connection.cookies.clear()
    assert connection.post("/api/v1/auth/login", headers=ORIGIN,
                           json={"username": other, "password": "integration-password"}).status_code == 200
    rejected = connection.get(endpoint)
    assert rejected.status_code == 404 and "snapshot" not in rejected.text


def test_live_projection_cannot_expose_resource_now_owned_by_another_user(projection):
    connection, app, _, kind, identifier, run, safe, _, _ = projection
    other = "trace-private-" + uuid4().hex
    connection.portal.call(add_user, DATABASE, other, "integration-password")
    foreign_owner = connection.portal.call(app.state.db.fetch,
        "SELECT id FROM users WHERE username=%s", (other,))[0]["id"]
    table, column = {"agent": ("agents", "id"), "memory": ("memory_settings", "owner_id"),
                     "knowledge": ("knowledge_bases", "id")}[kind]
    connection.portal.call(app.state.db.execute,
        sql.SQL("UPDATE {} SET owner_id=%s WHERE {}=%s").format(sql.Identifier(table), sql.Identifier(column)),
        (foreign_owner, identifier))
    if kind == "memory":
        # A stale/corrupt reference to a different owner's settings must also be rejected by the current projection.
        identifier = str(foreign_owner)
        connection.portal.call(app.state.db.execute,
            "UPDATE run_resource_snapshots SET resource_id=%s WHERE run_id=%s", (identifier, run))
        projection = (*projection[:4], identifier, *projection[5:])
    _, item = evidence(projection)
    assert item["current"] is None and not item["available"] and item["differences"] == []
    assert item["snapshot"] == safe
