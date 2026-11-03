"""HTTP permissions, concurrent ownership, unknown effects and browser state release."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from opensandbox.config import ConnectionConfig
from test_api import DATABASE, ORIGIN, add_user, run_async
from test_api import client as client
from test_durable_sandboxes import cleanup, prepare

from app.browser_workbench import BrowserWorkbench


@pytest.fixture
def controlled(client):
    connection, app, run = prepare(client)
    session, requests, behavior = uuid4(), [], {"result": True, "release": True}
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_queue_runs SET environment='browser',network_domains='[\"example.com\"]',"
        "status='PAUSED',lease_token=NULL,lease_until=NULL WHERE id=%s",
        (run["id"],),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_sandbox_sessions(id,run_id,owner_id,kind,state,sandbox_id,"
        "browser_persistent,retention_required) "
        "VALUES(%s,%s,%s,'browser','READY',%s,true,true)",
        (session, run["id"], run["owner_id"], "fixture-" + str(session)),
    )
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_browser_transports(session_id,task_id,owner_id,token_ciphertext,status) "
        "VALUES(%s,%s,%s,%s,'READY')",
        (session, run["task_id"], run["owner_id"], app.state.models.vault.encrypt("a" * 64)),
    )
    service = BrowserWorkbench(
        app.state.db,
        app.state.models.vault,
        ConnectionConfig(domain="127.0.0.1:18180", api_key="fixture-only", use_server_proxy=True),
        SimpleNamespace(capture=AsyncMock(return_value=uuid4())),
    )

    @asynccontextmanager
    async def connect(current, *, control=None, cleanup=False):
        async def guard():
            if control:
                await service.guard_control(control, cleanup=cleanup)
            else:
                await service.guard_view(current, {"id": session})

        async def request(body=None, *, frame=False):
            await guard()
            if frame:
                return b"\xff\xd8\xfffixture"
            requests.append(body)
            await asyncio.sleep(0.05)
            return {
                "ok": behavior["result"],
                "result": {"title": "fixture", "url": "https://example.com?private=query"},
            }

        yield SimpleNamespace(transport=SimpleNamespace(key="a" * 64, request=request), save=guard, backend=None)

    async def release(identifier):
        assert identifier == session
        if behavior["release"]:
            await app.state.db.execute(
                "UPDATE work_sandbox_sessions SET state='RELEASED',retention_required=false WHERE id=%s", (session,)
            )
        return behavior["release"]

    service.connect, service.sandboxes.release = connect, release
    app.state.browser_workbench = service
    app.state.settings.work_enabled = True
    try:
        yield connection, app, run, service, requests, behavior
    finally:
        connection.portal.call(app.state.db.execute, "DELETE FROM work_browser_controls WHERE run_id=%s", (run["id"],))
        connection.portal.call(service.close)
        app.state.browser_workbench = None
        cleanup(connection, app, run)


def start(connection, run, request=None, **flags):
    return connection.post(
        f"/api/v1/work-runs/{run['id']}/browser/controls",
        headers=ORIGIN,
        json={"request_id": request or str(uuid4()), **flags},
    )


def test_concurrent_control_and_actions_are_fenced_and_private(controlled):
    connection, app, run, service, requests, _ = controlled
    nonce = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: start(connection, run, nonce), range(2)))
    assert all(response.status_code == 202 for response in responses)
    control = responses[0].json()["id"]
    assert responses[1].json()["id"] == control
    assert start(connection, run).status_code == 409
    assert connection.post(f"/api/v1/work-runs/{run['id']}/resume", headers=ORIGIN).status_code == 409
    summary = connection.get(f"/api/v1/work-runs/{run['id']}/browser")
    assert summary.status_code == 200 and "ciphertext" not in summary.text and "a" * 64 not in summary.text
    body = {"request_id": str(uuid4()), "kind": "type", "parameters": {"text": "private-manual-text"}}
    url = f"/api/v1/work-browser-controls/{control}/actions"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: connection.post(url, headers=ORIGIN, json=body), range(2)))
    assert all(result.status_code in {200, 409} for result in results) and len(requests) == 1
    repeated = connection.post(url, headers=ORIGIN, json=body)
    assert repeated.status_code == 200 and repeated.json()["url"] == "https://example.com"
    assert len(requests) == 1
    rows = connection.portal.call(
        app.state.db.fetch,
        "SELECT row_to_json(a)::text AS data FROM work_browser_actions a WHERE control_id=%s",
        (control,),
    )
    assert rows and "private-manual-text" not in rows[0]["data"]
    assert connection.post(url, headers=ORIGIN, json={**body, "parameters": {"text": "changed"}}).status_code == 409
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_browser_controls SET lease_until=now()-interval '1 second' WHERE id=%s",
        (control,),
    )
    assert connection.post(url, headers=ORIGIN, json={**body, "request_id": str(uuid4())}).status_code == 409
    assert len(requests) == 1
    assert connection.portal.call(service.tick)
    assert connection.portal.call(service.control, control, run["owner_id"])["status"] == "CLOSED"
    assert connection.post(f"/api/v1/work-runs/{run['id']}/resume", headers=ORIGIN).status_code == 202


def test_unknown_manual_action_requires_review_and_role_change_stops_control(controlled):
    connection, app, run, service, requests, behavior = controlled
    control = start(connection, run).json()["id"]
    behavior["result"] = False
    nonce = str(uuid4())
    url = f"/api/v1/work-browser-controls/{control}"
    body = {"request_id": nonce, "kind": "pointer", "parameters": {"x": 20, "y": 20}}
    assert connection.post(url + "/actions", headers=ORIGIN, json=body).status_code == 409
    assert (
        connection.post(url + "/actions", headers=ORIGIN, json={**body, "request_id": str(uuid4())}).status_code == 409
    )
    assert len(requests) == 1
    connection.post(url + "/close", headers=ORIGIN, json={})
    assert connection.portal.call(service.tick)
    assert connection.portal.call(service.control, control, run["owner_id"])["status"] == "UNKNOWN"
    assert (
        connection.post(url + f"/actions/{nonce}/review", headers=ORIGIN, json={"acknowledged": False}).status_code
        == 422
    )
    assert (
        connection.post(url + f"/actions/{nonce}/review", headers=ORIGIN, json={"acknowledged": True}).status_code
        == 200
    )
    connection.post(url + "/close", headers=ORIGIN, json={})
    assert connection.portal.call(service.tick)
    control = start(connection, run).json()["id"]
    connection.portal.call(app.state.db.execute, "UPDATE users SET role_id='viewer' WHERE id=%s", (run["owner_id"],))
    try:
        assert connection.get(f"/api/v1/work-runs/{run['id']}/browser/frame").status_code == 200
        assert connection.post(f"/api/v1/work-browser-controls/{control}/renew", headers=ORIGIN).status_code == 403
        assert connection.portal.call(service.tick)
        assert connection.portal.call(service.control, control, run["owner_id"])["status"] == "CLOSED"
    finally:
        connection.portal.call(
            app.state.db.execute, "UPDATE users SET role_id='member' WHERE id=%s", (run["owner_id"],)
        )


def test_login_clear_waits_for_confirmed_environment_release(controlled):
    connection, app, run, service, _, behavior = controlled
    cipher = app.state.models.vault.encrypt("private-state-fixture")
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_browser_states(task_id,owner_id,state_ciphertext,status) VALUES(%s,%s,%s,'READY')",
        (run["task_id"], run["owner_id"], cipher),
    )
    behavior["release"] = False
    control = start(connection, run, clear_login=True).json()["id"]
    assert connection.portal.call(service.tick)
    assert connection.portal.call(service.control, control, run["owner_id"])["status"] == "UNKNOWN"
    rows = connection.portal.call(
        app.state.db.fetch, "SELECT state_ciphertext FROM work_browser_states WHERE task_id=%s", (run["task_id"],)
    )
    assert rows[0]["state_ciphertext"] == cipher
    behavior["release"] = True
    connection.post(f"/api/v1/work-browser-controls/{control}/close", headers=ORIGIN, json={"clear_login": True})
    assert connection.portal.call(service.tick)
    rows = connection.portal.call(
        app.state.db.fetch,
        "SELECT state_ciphertext,revision,status FROM work_browser_states WHERE task_id=%s",
        (run["task_id"],),
    )
    assert rows[0]["state_ciphertext"] == "" and rows[0]["status"] == "EMPTY" and rows[0]["revision"] == 1


def test_interrupted_manual_request_requires_review_without_replay(controlled):
    connection, app, run, service, requests, _ = controlled
    control = start(connection, run).json()["id"]
    nonce = uuid4()
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_browser_actions(control_id,request_id,kind,digest,created_at) "
        "VALUES(%s,%s,'key','fixture',now()-interval '91 seconds')",
        (control, nonce),
    )
    connection.portal.call(service.tick)
    summary = connection.get(f"/api/v1/work-runs/{run['id']}/browser").json()
    assert summary["actions"][0]["status"] == "UNKNOWN" and not requests
    url = f"/api/v1/work-browser-controls/{control}"
    for parameters in [{"key": []}, {"key": {}}, {"key": 1}]:
        assert (
            connection.post(
                url + "/actions",
                headers=ORIGIN,
                json={"request_id": str(uuid4()), "kind": "key", "parameters": parameters},
            ).status_code
            == 422
        )
    connection.post(url + "/close", headers=ORIGIN, json={})
    connection.portal.call(service.tick)
    assert connection.portal.call(service.control, control, run["owner_id"])["status"] == "UNKNOWN"
    assert (
        connection.post(url + f"/actions/{nonce}/review", headers=ORIGIN, json={"acknowledged": True}).status_code
        == 200
    )
    connection.post(url + "/close", headers=ORIGIN, json={})
    connection.portal.call(service.tick)
    assert connection.portal.call(service.control, control, run["owner_id"])["status"] == "CLOSED"
    assert not requests


def test_browser_owner_and_running_lease_boundaries(controlled):
    connection, app, run, service, requests, _ = controlled
    control = start(connection, run).json()["id"]
    cookies = dict(connection.cookies.items())
    username = "browser-other-" + uuid4().hex
    run_async(add_user(DATABASE, username, "integration-password"))
    connection.cookies.clear()
    assert (
        connection.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={"username": username, "password": "integration-password"},
        ).status_code
        == 200
    )
    try:
        for suffix in ["/browser", "/browser/frame"]:
            assert connection.get(f"/api/v1/work-runs/{run['id']}" + suffix).status_code == 404
        assert start(connection, run).status_code == 404
        assert (
            connection.post(
                f"/api/v1/work-browser-controls/{control}/actions",
                headers=ORIGIN,
                json={"request_id": str(uuid4()), "kind": "key", "parameters": {"key": "Tab"}},
            ).status_code
            == 404
        )
    finally:
        connection.cookies.clear()
        connection.cookies.update(cookies)
    connection.post(f"/api/v1/work-browser-controls/{control}/close", headers=ORIGIN, json={})
    connection.portal.call(service.tick)
    connection.portal.call(
        app.state.db.execute,
        "UPDATE work_queue_runs SET status='RUNNING',lease_token=%s,lease_until=now()-interval '1 second' WHERE id=%s",
        (uuid4(), run["id"]),
    )
    assert connection.get(f"/api/v1/work-runs/{run['id']}/browser/frame").status_code == 409
    assert start(connection, run).status_code == 409
    assert not requests


def test_restored_browser_has_no_live_control_or_transport(controlled):
    from app.backup import quarantine_browsers

    connection, app, run, service, requests, _ = controlled
    control = start(connection, run).json()["id"]
    cipher = app.state.models.vault.encrypt("restored-private-fixture")
    connection.portal.call(
        app.state.db.execute,
        "INSERT INTO work_browser_states(task_id,owner_id,state_ciphertext,status) VALUES(%s,%s,%s,'READY')",
        (run["task_id"], run["owner_id"], cipher),
    )

    async def restore():
        async with app.state.db.pool.connection() as database:
            # Isolate this fixture exactly as a restored deployment's own database.
            await database.execute(
                "CREATE TEMP TABLE work_browser_controls AS SELECT * FROM public.work_browser_controls WHERE id=%s",
                (control,),
            )
            await database.execute(
                "CREATE TEMP TABLE work_browser_transports AS SELECT * FROM public.work_browser_transports "
                "WHERE task_id=%s",
                (run["task_id"],),
            )
            await database.execute(
                "CREATE TEMP TABLE work_browser_states AS SELECT * FROM public.work_browser_states WHERE task_id=%s",
                (run["task_id"],),
            )
            try:
                await quarantine_browsers(database)
                saved = await (await database.execute("SELECT * FROM work_browser_states")).fetchone()
                assert saved["state_ciphertext"] == cipher and saved["status"] == "REVIEW"
                token = await (await database.execute("SELECT * FROM work_browser_transports")).fetchone()
                assert token["token_ciphertext"] == "" and token["status"] == "CLOSED"
                state = await (await database.execute("SELECT * FROM work_browser_controls")).fetchone()
                assert state["status"] == "CLOSED" and state["error_code"] == "RESTORED_REVIEW"
            finally:
                await database.rollback()

    connection.portal.call(restore)
    assert not requests
