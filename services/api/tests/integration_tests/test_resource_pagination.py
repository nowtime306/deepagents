"""Older owned repository, batch and occurrence records remain reachable."""

from uuid import uuid4

from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from test_automations import batch, schedule
from test_repositories import grant


def test_repository_pages_options_literal_search_and_owner_scope(client):
    connection, app = client
    grant(connection, app)
    owner = connection.get("/api/v1/auth/me").json()["id"]
    for index in range(102):
        response = connection.post("/api/v1/repositories", headers=ORIGIN,
            json={"name": f"资源{index:03d}" if index else "字面%_资源", "slug": f"local/repo-{index}"})
        assert response.status_code == 201
    connection.portal.call(app.state.db.execute,
        "UPDATE repositories SET updated_at='2026-01-01T00:00:00Z' WHERE owner_id=%s", (owner,))
    first = connection.get("/api/v1/repositories").json()
    assert len(first["items"]) == 100 and first["next_cursor"]
    second = connection.get("/api/v1/repositories", params={"before": first["next_cursor"]}).json()
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    assert len({row["id"] for row in first["items"] + second["items"]}) == 102
    options = connection.get("/api/v1/repositories/options").json()
    assert len(connection.get("/api/v1/repositories/options",
        params={"before": options["next_cursor"]}).json()["items"]) == 2
    assert len(connection.get("/api/v1/repositories", params={"q": "%_"}).json()["items"]) == 1
    assert connection.get("/api/v1/repositories/options", params={"before": first["next_cursor"]}).status_code == 422
    assert connection.get("/api/v1/repositories", params={"before": "bad"}).status_code == 422
    username = "resource-other-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.post("/api/v1/auth/login", headers=ORIGIN,
                    json={"username": username, "password": "integration-password"})
    grant(connection, app)
    assert connection.get("/api/v1/repositories").json()["items"] == []
    assert connection.get("/api/v1/repositories", params={"before": first["next_cursor"]}).status_code == 422


def test_batch_and_occurrence_history_pages_and_cursor_resource_scope(client):
    connection, app = client
    owner = connection.get("/api/v1/auth/me").json()["id"]
    identifier = batch(connection)
    connection.portal.call(app.state.db.execute,
        "INSERT INTO agent_batches(id,owner_id,name,snapshot,mode,status,model_call_limit,token_limit,item_budget) "
        "SELECT gen_random_uuid(),owner_id,'批次'||n,snapshot,mode,'COMPLETED',"
        "model_call_limit,token_limit,item_budget "
        "FROM agent_batches CROSS JOIN generate_series(1,101) n WHERE id=%s", (identifier,))
    first = connection.get("/api/v1/batches").json()
    second = connection.get("/api/v1/batches", params={"before": first["next_cursor"]}).json()
    assert len(first["items"]) == 100 and len(second["items"]) == 2
    assert len({row["id"] for row in first["items"] + second["items"]}) == 102
    assert len(connection.get("/api/v1/batches", params={"q": "批次101"}).json()["items"]) == 1
    automation = schedule(connection)
    connection.portal.call(app.state.db.execute,
        "INSERT INTO automation_occurrences(id,automation_id,owner_id,scheduled_for,status) "
        "SELECT gen_random_uuid(),%s,%s,'2026-01-01'::timestamptz+n*interval '1 second','SKIPPED_BUSY' "
        "FROM generate_series(1,102) n", (automation, owner))
    path = f"/api/v1/automations/{automation}/occurrences"
    history = connection.get(path).json()
    earlier = connection.get(path, params={"before": history["next_cursor"]}).json()
    assert len(history["items"]) == 100 and len(earlier["items"]) == 2
    assert len({row["id"] for row in history["items"] + earlier["items"]}) == 102
    assert connection.get(path, params={"before": first["next_cursor"]}).status_code == 422
    other = schedule(connection)
    assert connection.get(f"/api/v1/automations/{other}/occurrences",
        params={"before": history["next_cursor"]}).status_code == 422
