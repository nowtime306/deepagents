import asyncio
import json
import subprocess
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app import durable_sandboxes as durable
from app import host_resources as host


@pytest.fixture
def probe(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(host, "_cache", (0.0, 0))
    monkeypatch.setattr(host.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(host, "host_available", lambda: 12 * host.GIB)

    def docker(args):
        clock.now += 0.25
        return str(10 * host.GIB) if args[0] == "info" else "1GiB / 10GiB"

    monkeypatch.setattr(host, "docker", docker)
    return clock


def test_sample_exact_budget_and_cache_keep_original_observation(probe):
    assert host.sample() == 8 * host.GIB
    first = host.budget_observation()
    assert first["host_available_bytes"] == 12 * host.GIB
    assert first["docker_total_bytes"] == 10 * host.GIB and first["docker_used_bytes"] == host.GIB
    assert first["margin_bytes"] == host.GIB and first["elapsed_ms"] == 500
    probe.now += 1
    assert host.sample() == 8 * host.GIB
    second = host.budget_observation()
    assert second["sampled_at"] == first["sampled_at"] and second["cached"] is True


@pytest.mark.parametrize(
    "error",
    [
        OSError("SECRET/path"),
        ValueError("SECRET raw output"),
        subprocess.CalledProcessError(1, ["SECRET"], stderr="password=SECRET"),
    ],
)
def test_probe_error_remains_zero_with_bounded_safe_type(probe, monkeypatch, error):
    def fail(args):
        raise error

    monkeypatch.setattr(host, "docker", fail)
    assert host.sample() == 0
    observation = host.budget_observation()
    assert observation["status"] == "ERROR" and observation["probe_stage"] == "docker_total"
    assert observation["host_available_bytes"] is None and observation["budget_bytes"] == 0
    assert "SECRET" not in json.dumps(observation)


async def test_async_resource_budget_returns_int_with_same_thread_sample(probe):
    result = await host.resource_budget()
    assert type(result) is int and result == 8 * host.GIB
    assert host.budget_observation()["budget_bytes"] == result


class DB:
    def __init__(self, reserved=0, mode="ok", event_failure=False):
        self.reserved, self.mode, self.event_failure = reserved, mode, event_failure
        self.owner, self.events = uuid4(), []
        self.pool = self

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, sql, params=()):
        if "COALESCE(sum" in sql:
            value = {"used": self.reserved}
        elif "knowledge_video_jobs" in sql:
            value = {"used": 0}
        elif "INSERT INTO work_sandbox_sessions" in sql:
            if self.mode == "unique":
                raise UniqueViolation("SECRET")
            if self.mode == "error":
                raise RuntimeError("ORIGINAL_ERROR")
            return SimpleNamespace(fetchall=lambda: immediate([] if self.mode == "lease" else [{"id": uuid4()}]))
        elif "next_event_id=next_event_id+1" in sql:
            if self.event_failure:
                raise OSError("SECRET diagnostic failure")
            value = {"next_event_id": 1, "owner_id": self.owner}
        elif "INSERT INTO work_run_events" in sql:
            self.events.append(params[-1].obj)
            value = {}
        else:
            value = {}
        return SimpleNamespace(fetchone=lambda: immediate(value))

    async def fetch(self, *args):
        return []


async def immediate(value):
    return value


@pytest.mark.parametrize(
    "mode,budget,reserved,reason",
    [
        ("ok", 1, 0, "BUDGET_INSUFFICIENT"),
        ("ok", 5 * host.GIB, host.GIB, "BUDGET_INSUFFICIENT"),
        ("ok", 5 * host.GIB, Decimal(host.GIB), "BUDGET_INSUFFICIENT"),
        ("lease", 10 * host.GIB, 0, "LEASE_PREDICATE_REJECTED"),
        ("unique", 10 * host.GIB, 0, "RESERVATION_CONFLICT"),
    ],
)
async def test_actual_none_branches_record_exact_run_owner_without_sdk(monkeypatch, mode, budget, reserved, reason):
    db = DB(reserved, mode)

    async def sample():
        return budget

    monkeypatch.setattr(durable, "resource_budget", sample)
    service = object.__new__(durable.DurableSandboxes)
    service.database = db
    run, token = uuid4(), uuid4()
    assert await service.reserve(run, token, "coding") is None
    assert len(db.events) == 1
    event = db.events[0]
    assert event["reason"] == reason and event["run_id"] == str(run) and event["owner_id"] == str(db.owner)
    assert event["needed_bytes"] == 5 * host.GIB and event["reserved_bytes"] == reserved
    assert event["budget_bytes"] == budget and str(token) not in json.dumps(event)
    assert event["sample"] is None  # A substituted scalar is not a historic probe receipt.


async def test_create_prelease_none_does_not_probe_or_call_sdk(monkeypatch):
    service = object.__new__(durable.DurableSandboxes)
    service.database = DB()
    monkeypatch.setattr(durable, "resource_budget", lambda: pytest.fail("no sample occurred"))
    monkeypatch.setattr(durable.Sandbox, "create", lambda *a, **k: pytest.fail("no SDK call"))
    assert await service.create(uuid4(), uuid4(), "coding") is None
    assert service.database.events[0]["reason"] == "CREATE_LEASE_UNAVAILABLE"
    assert service.database.events[0]["sample"] is None


async def test_diagnostic_failure_never_admits_and_no_secret_warning(monkeypatch, caplog):
    service = object.__new__(durable.DurableSandboxes)
    service.database = DB(event_failure=True)
    monkeypatch.setattr(durable, "resource_budget", lambda: immediate(0))
    assert await service.reserve(uuid4(), uuid4(), "coding") is None
    assert "capacity_diagnostic_failed" in caplog.text and "SECRET" not in caplog.text


async def test_original_database_error_is_not_swallowed_or_reclassified(monkeypatch):
    service = object.__new__(durable.DurableSandboxes)
    service.database = DB(mode="error")
    monkeypatch.setattr(durable, "resource_budget", lambda: immediate(10 * host.GIB))
    with pytest.raises(RuntimeError, match="ORIGINAL_ERROR"):
        await service.reserve(uuid4(), uuid4(), "coding")
    assert service.database.events == []


async def test_admitted_reservation_keeps_return_shape_and_has_no_refusal(monkeypatch):
    service = object.__new__(durable.DurableSandboxes)
    service.database = DB()
    monkeypatch.setattr(durable, "resource_budget", lambda: immediate(10 * host.GIB))
    result = await service.reserve(uuid4(), uuid4(), "coding")
    assert set(result) == {"id"} and not service.database.events


async def test_thread_samples_cannot_cross_contaminate_callers(monkeypatch):
    tag = ContextVar("synthetic_sample_tag")
    barrier = threading.Barrier(2)

    def sample():
        value = tag.get()
        host._observation.set({"budget_bytes": value})
        barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(host, "sample", sample)

    async def caller(value):
        tag.set(value)
        result = await host.resource_budget()
        return result, host.budget_observation()

    first, second = await asyncio.gather(caller(11), caller(22))
    assert first == (11, {"budget_bytes": 11}) and second == (22, {"budget_bytes": 22})


async def test_actual_probe_failure_attaches_safe_error_to_none(probe, monkeypatch):
    def failed(args):
        raise OSError("SECRET stderr/path")

    monkeypatch.setattr(host, "docker", failed)
    service = object.__new__(durable.DurableSandboxes)
    service.database = DB()
    assert await service.reserve(uuid4(), uuid4(), "coding") is None
    value = service.database.events[0]
    assert value["sample"]["status"] == "ERROR" and value["sample"]["error_type"] == "OSError"
    assert "SECRET" not in json.dumps(value)


async def test_sdk_ambiguous_create_is_not_retried_and_keeps_unknown(monkeypatch):
    class Creating(DB):
        updates = []

        async def fetch(self, sql, *args):
            return [{"network_domains": []}] if "network_domains" in sql else [{"id": uuid4()}]

        async def execute(self, sql, params=()):
            self.updates.append(sql)
            return await super().execute(sql, params)

    service = object.__new__(durable.DurableSandboxes)
    service.database, service.config = Creating(), SimpleNamespace()
    calls = []

    async def create(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("ORIGINAL_AMBIGUOUS_SDK")

    monkeypatch.setattr(durable, "resource_budget", lambda: immediate(10 * host.GIB))
    monkeypatch.setattr(durable, "policy", lambda *args: immediate(None))
    monkeypatch.setattr(durable.Sandbox, "create", create)
    with pytest.raises(RuntimeError, match="ORIGINAL_AMBIGUOUS_SDK"):
        await service.create(uuid4(), uuid4(), "coding")
    assert calls == [True] and any("state='UNKNOWN'" in sql for sql in service.database.updates)
    assert not service.database.events
