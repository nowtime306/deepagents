"""Real database admission and consumer with deterministic model execution."""

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from test_api import ORIGIN
from test_api import client as client


def setup_version(connection):
    identifier = connection.post(
        "/api/v1/workflows", json={"name": "对外-" + str(uuid4()), "model_id": "test"}, headers=ORIGIN
    ).json()["id"]
    assert (
        connection.post(f"/api/v1/workflows/{identifier}/test-runs", json={"text": "测试"}, headers=ORIGIN).status_code
        == 200
    )
    connection.post(f"/api/v1/workflows/{identifier}/publish", json={"revision": 1}, headers=ORIGIN)
    return identifier, connection.get(f"/api/v1/workflows/{identifier}/versions").json()["items"][0]["id"]


def wait_run(connection, identifier, headers):
    for _ in range(100):
        row = connection.get(f"/api/public/v1/workflow-runs/{identifier}", headers=headers).json()
        if row.get("status") not in ("PENDING", "RUNNING"):
            return row
        time.sleep(0.03)
    raise AssertionError("Workflow consumer did not finish")


def test_scoped_keys_idempotency_revocation_and_audit(client):
    connection, app = client
    workflow, version = setup_version(connection)
    result = connection.post(
        "/api/v1/api-keys", json={"name": "系统A", "version_id": version, "rpm": 1}, headers=ORIGIN
    )
    assert result.status_code == 201
    key = result.json()
    assert key["key"] not in connection.get("/api/v1/api-keys").text
    headers = {"Authorization": "Bearer " + key["key"], "Idempotency-Key": "same-request"}
    url = "/api/public/v1/workflow-runs"
    # Browser cookies alone never authorize the public endpoint; no Origin required with Bearer.
    assert connection.post(url, json={"text": "hello"}).status_code == 401
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: connection.post(url, json={"text": "hello"}, headers=headers), range(4)))
    assert all(row.status_code == 202 for row in results)
    identifiers = {row.json()["id"] for row in results}
    assert len(identifiers) == 1
    run_id = identifiers.pop()
    assert connection.post(url, json={"text": "changed"}, headers=headers).status_code == 409
    assert connection.post(url, json={"text": "new"}, headers={**headers, "Idempotency-Key": "new"}).status_code == 429
    assert wait_run(connection, run_id, headers)["status"] == "SUCCEEDED"
    other = connection.post("/api/v1/api-keys", json={"name": "系统B", "version_id": version}, headers=ORIGIN).json()
    assert connection.get(f"{url}/{run_id}", headers={"Authorization": "Bearer " + other["key"]}).status_code == 404
    assert (
        connection.post(
            f"/api/v1/workflows/{workflow}/versions/1/runs", json={"text": "private"}, headers=headers
        ).status_code
        == 403
    )
    assert connection.delete(f"/api/v1/api-keys/{key['id']}", headers=ORIGIN).status_code == 200
    assert connection.get(f"{url}/{run_id}", headers=headers).status_code == 401
    assert connection.post(url, json={"text": "hello"}, headers=headers).status_code == 401
    audit = connection.get("/api/v1/api-call-logs").json()["items"]
    assert len(audit) == 1 and audit[0]["status"] == "SUCCEEDED" and audit[0]["duration_ms"] is not None
    connection.portal.call(
        app.state.db.execute,
        "UPDATE api_credentials SET expires_at=now()-interval '1 second' WHERE id=%s",
        (other["id"],),
    )
    assert (
        connection.post(
            url,
            json={"text": "hello"},
            headers={"Authorization": "Bearer " + other["key"], "Idempotency-Key": "expired"},
        ).status_code
        == 401
    )


def test_queued_authorization_is_rechecked_and_concurrency_is_bounded(client):
    connection, app = client
    workflow, version = setup_version(connection)
    key = connection.post("/api/v1/api-keys", json={"name": "排队", "version_id": version}, headers=ORIGIN).json()
    headers = {"Authorization": "Bearer " + key["key"], "Idempotency-Key": "queued"}

    # Delay execution to exercise persisted queue and admission instead of timing a fast model.
    async def delay(_):
        import asyncio

        await asyncio.sleep(1)

    original = app.state.workflow_queue.guard
    first_guard = True

    async def guard(run):
        nonlocal first_guard
        if first_guard:
            first_guard = False
            await delay(run)
        await original(run)

    app.state.workflow_queue.guard = guard
    result = connection.post("/api/public/v1/workflow-runs", json={"text": "queued"}, headers=headers)
    assert result.status_code == 202
    run_id = result.json()["id"]
    assert (
        connection.post(
            "/api/public/v1/workflow-runs", json={"text": "again"}, headers={**headers, "Idempotency-Key": "second"}
        ).status_code
        == 429
    )
    connection.put(f"/api/v1/workflows/{workflow}/versions/1/state", json={"active": False}, headers=ORIGIN)
    # Downlining blocks new admissions; accepted runs retain their immutable version.
    assert wait_run(connection, run_id, headers)["status"] == "SUCCEEDED"
    assert (
        connection.post(
            "/api/public/v1/workflow-runs", json={"text": "off"}, headers={**headers, "Idempotency-Key": "off"}
        ).status_code
        == 409
    )


def test_revoking_a_key_stops_the_next_node_of_an_accepted_run(client):
    import asyncio

    connection, app = client
    _, version = setup_version(connection)
    key = connection.post(
        "/api/v1/api-keys", json={"name": "撤销在途授权", "version_id": version}, headers=ORIGIN
    ).json()
    original = app.state.workflow_queue.guard

    async def guard(run):
        await asyncio.sleep(0.5)
        await original(run)

    app.state.workflow_queue.guard = guard
    response = connection.post(
        "/api/public/v1/workflow-runs",
        json={"text": "revoke"},
        headers={"Authorization": "Bearer " + key["key"], "Idempotency-Key": "revoke"},
    )
    assert response.status_code == 202
    identifier = response.json()["id"]
    assert connection.delete("/api/v1/api-keys/" + key["id"], headers=ORIGIN).status_code == 200
    for _ in range(100):
        row = connection.portal.call(app.state.db.fetch, "SELECT status FROM workflow_runs WHERE id=%s", (identifier,))[
            0
        ]
        if row["status"] == "FAILED":
            break
        time.sleep(0.03)
    assert row["status"] == "FAILED"
