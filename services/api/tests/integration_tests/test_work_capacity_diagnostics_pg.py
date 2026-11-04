"""Isolated PG/events/session contracts with scalar capacity stubs and no SDK or consumer."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_agent_evaluation_platform as platform_contract
from psycopg.types.json import Jsonb

from app import durable_sandboxes as durable
from app.security import COOKIE
from app.work_run_api import router

h = platform_contract.h  # Reuse the reviewed real session/isolation/success-only owner cleanup fixture.


migration = platform_contract.migration  # Preserve the original explicit isolation gate and real migrations.


@pytest.fixture
def capacity(h, request, monkeypatch):
    if h.sql("SELECT 1 FROM work_queue_runs UNION ALL SELECT 1 FROM work_sandbox_sessions LIMIT 1"):
        pytest.skip("An empty independent Work metadata namespace is required; existing rows are retained")
    assert h.sql("SELECT count(*) AS n FROM model_usage")[0]["n"] == 0
    assert h.sql("SELECT count(*) AS n FROM operation_executions")[0]["n"] == 0
    h.app.include_router(router)
    h.execute("UPDATE platform_roles SET permissions=permissions||%s WHERE id=%s", (Jsonb(["work.read"]), h.role))
    service = object.__new__(durable.DurableSandboxes)
    service.database = h.db
    run_ids, tasks = [], []
    before = request.session.testsfailed

    async def forbidden(*args, **kwargs):
        pytest.fail("No real SDK/manager call or queue consumer is permitted")

    monkeypatch.setattr(durable.Sandbox, "create", forbidden)
    monkeypatch.setattr(durable.Sandbox, "connect", forbidden)
    monkeypatch.setattr(durable.SandboxManager, "create", forbidden)

    def run():
        identifier, task, token = uuid4(), uuid4(), uuid4()
        h.execute(
            "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,%s,%s)",
            (task, h.owner, "synthetic capacity metadata", "no execution"),
        )
        tasks.append(task)
        h.execute(
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,status,lease_token,lease_until) "
            "VALUES(%s,%s,%s,%s,%s,1,'RUNNING',%s,now()+interval '1 hour')",
            (identifier, task, h.owner, "synthetic capacity metadata", "no execution", token),
        )
        run_ids.append(identifier)
        return identifier, token

    value = SimpleNamespace(
        h=h, service=service, run=run, call=lambda function, *args: h.http.portal.call(function, *args)
    )
    yield value
    if request.session.testsfailed != before:
        return  # Preserve this new failing fixture; do not touch any historical DB or rows.
    assert h.sql("SELECT count(*) AS n FROM model_usage")[0]["n"] == 0
    assert h.sql("SELECT count(*) AS n FROM operation_executions")[0]["n"] == 0
    h.execute("DELETE FROM work_sandbox_sessions WHERE run_id=ANY(%s) AND owner_id=%s", (run_ids, h.owner))
    h.execute("DELETE FROM work_queue_runs WHERE id=ANY(%s) AND owner_id=%s", (run_ids, h.owner))
    h.execute("DELETE FROM work_tasks WHERE id=ANY(%s) AND owner_id=%s", (tasks, h.owner))
    assert not h.sql("SELECT id FROM work_queue_runs WHERE id=ANY(%s)", (run_ids,))


def budget(monkeypatch, amount):
    async def scalar():
        return amount

    monkeypatch.setattr(durable, "resource_budget", scalar)


def events(c, run):
    return c.h.sql("SELECT event_id,event,data FROM work_run_events WHERE run_id=%s ORDER BY event_id", (run,))


def test_concurrent_native_event_ids_are_contiguous_and_owner_is_db_derived(capacity):
    c = capacity
    run, token = c.run()

    async def write():
        await asyncio.gather(
            *(c.service.capacity_refused(run, "coding", "BUDGET_INSUFFICIENT", needed=5, budget=0) for _ in range(20))
        )

    c.call(write)
    rows = events(c, run)
    assert [r["event_id"] for r in rows] == list(range(1, 21))
    assert c.h.sql("SELECT next_event_id FROM work_queue_runs WHERE id=%s", (run,))[0]["next_event_id"] == 20
    assert all(r["event"] == "sandbox.capacity_refused" and r["data"]["owner_id"] == str(c.h.owner) for r in rows)
    assert str(token) not in json.dumps(rows)


def test_actual_http_owner_reads_and_foreign_admin_cannot_read(capacity, monkeypatch):
    c = capacity
    run, token = c.run()
    budget(monkeypatch, 0)
    assert c.call(c.service.reserve, run, token, "coding") is None
    response = c.h.http.get(f"/api/v1/work-runs/{run}/events")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1 and data["next_after"] == 1
    fields = data["items"][0]["data"]
    assert set(fields) == {
        "schema_version",
        "reason",
        "kind",
        "run_id",
        "observed_at",
        "needed_bytes",
        "reserved_bytes",
        "budget_bytes",
        "sample",
        "owner_id",
    }
    assert fields["sample"] is None and fields["reason"] == "BUDGET_INSUFFICIENT"
    assert str(token) not in response.text and "stderr" not in response.text and "password" not in response.text
    c.h.http.cookies.set(COOKIE, c.h.tokens[1])
    assert c.h.sql("SELECT is_admin FROM users WHERE id=%s", (c.h.other,))[0]["is_admin"] is True
    assert c.h.http.get(f"/api/v1/work-runs/{run}/events").status_code == 404


def test_native_duplicate_event_constraint_rolls_back_allocation_and_never_admits(capacity, monkeypatch, caplog):
    c = capacity
    run, token = c.run()
    c.h.execute(
        "INSERT INTO work_run_events(run_id,event_id,event,data) VALUES(%s,1,'synthetic.existing',%s)",
        (run, Jsonb({"fixture": "preexisting duplicate"})),
    )
    budget(monkeypatch, 0)
    assert c.call(c.service.reserve, run, token, "coding") is None
    assert c.h.sql("SELECT next_event_id FROM work_queue_runs WHERE id=%s", (run,))[0]["next_event_id"] == 0
    rows = events(c, run)
    assert len(rows) == 1 and rows[0]["event"] == "synthetic.existing"
    assert "capacity_diagnostic_failed" in caplog.text
    assert not c.h.sql("SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (run,))


def test_actual_zero_scalar_budget_records_refusal_without_fake_probe(capacity, monkeypatch):
    c = capacity
    run, token = c.run()
    budget(monkeypatch, 0)
    assert c.call(c.service.reserve, run, token, "coding") is None
    value = events(c, run)[0]["data"]
    assert value["reason"] == "BUDGET_INSUFFICIENT" and value["budget_bytes"] == 0
    assert value["needed_bytes"] == 5 * 1024**3 and value["reserved_bytes"] == 0 and value["sample"] is None


@pytest.mark.parametrize("fault", ["wrong_token", "expired"])
def test_native_lease_predicate_rejects_with_high_scalar_budget(capacity, monkeypatch, fault):
    c = capacity
    run, token = c.run()
    if fault == "wrong_token":
        token = uuid4()
    else:
        c.h.execute("UPDATE work_queue_runs SET lease_until=now()-interval '1 second' WHERE id=%s", (run,))
    budget(monkeypatch, 20 * 1024**3)
    assert c.call(c.service.reserve, run, token, "coding") is None
    assert events(c, run)[0]["data"]["reason"] == "LEASE_PREDICATE_REJECTED"
    assert not c.h.sql("SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (run,))


def test_real_partial_session_unique_index_records_conflict(capacity, monkeypatch):
    c = capacity
    run, token = c.run()
    session = uuid4()
    c.h.execute(
        "INSERT INTO work_sandbox_sessions(id,run_id,owner_id,kind,state) VALUES(%s,%s,%s,'coding','CREATING')",
        (session, run, c.h.owner),
    )
    budget(monkeypatch, 20 * 1024**3)
    assert c.call(c.service.reserve, run, token, "coding") is None
    assert events(c, run)[0]["data"]["reason"] == "RESERVATION_CONFLICT"
    assert c.h.sql("SELECT id,state,sandbox_id FROM work_sandbox_sessions WHERE run_id=%s", (run,)) == [
        {"id": session, "state": "CREATING", "sandbox_id": None}
    ]


def test_create_missing_live_lease_does_not_sample_reserve_or_call_sdk(capacity, monkeypatch):
    c = capacity
    run, token = c.run()

    async def forbidden(*args, **kwargs):
        pytest.fail("No probe or reserve should occur before a valid live create lease")

    monkeypatch.setattr(durable, "resource_budget", forbidden)
    monkeypatch.setattr(c.service, "reserve", forbidden)
    assert c.call(c.service.create, run, uuid4(), "coding") is None
    value = events(c, run)[0]["data"]
    assert value["reason"] == "CREATE_LEASE_UNAVAILABLE" and value["sample"] is None
    assert value["budget_bytes"] is None and value["reserved_bytes"] is None
    assert not c.h.sql("SELECT id FROM work_sandbox_sessions WHERE run_id=%s", (run,))
