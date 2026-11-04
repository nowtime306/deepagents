"""Real UsageRecorder callbacks, WorkflowRuntime and controlled model; only SQL/auth are substitutes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import evaluation_target as profile
import pytest
from app.config import ModelConfig
from app.errors import APIError
from app.resource_runtime import ResourceRun, active_resources
from app.workflow_definition import Definition
from app.workflow_runtime import WorkflowRuntime

from evaluation_runner import fixed_versioned_workflow as facts


class Cursor:
    def __init__(self, rows=()):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)


class Database:
    def __init__(self, config):
        self.config, self.ledger, self.policies, self.snapshots, self.events = config, {}, {}, {}, []
        self.pool = self

    @asynccontextmanager
    async def connection(self):
        yield self

    async def fetch(self, sql, args):
        if sql.startswith("SELECT revision,model,kind"):
            assert args == (self.config.id,)
            return [self.config.model_dump(include={"revision", "model", "kind", "provider", "protocol", "base_url", "supports_images"})]
        raise AssertionError("Unexpected callback fetch: " + sql)

    async def execute(self, sql, args=()):
        if sql.startswith("INSERT INTO workflow_node_events"):
            self.events.append(args[3].obj)
            return Cursor()
        if sql.startswith("SELECT pg_advisory"):
            return Cursor()
        if sql.startswith("SELECT active FROM platform_maintenance"):
            return Cursor([{"active": False}])
        if sql.startswith("SELECT 1 FROM model_usage"):
            return Cursor([{"found": True}] if args[0] in self.ledger else [])
        if sql.startswith("SELECT r.snapshot FROM workflow_runs"):
            return Cursor([{"snapshot": {"definition": self.policies[args[0]]}}])
        if sql.startswith("SELECT count(*) AS calls"):
            rows = [item for item in self.ledger.values() if item["run_id"] == args[0]]
            return Cursor([{"calls": len(rows), "spent": sum(item["input_tokens"] + item["output_tokens"] if item["status"] == "KNOWN"
                else item["estimated_input_tokens"] + item["output_token_reserve"] for item in rows)}])
        if sql.startswith("INSERT INTO model_usage"):
            self.ledger[args[0]] = {"call_id": args[0], "run_id": args[1], "owner_id": args[2], "mode": args[3], "model_id": args[4],
                "model_revision": args[5], "estimated_input_tokens": args[6], "output_token_reserve": args[7], "status": "PENDING",
                "input_tokens": None, "output_tokens": None, "price_version_id": None}
            return Cursor()
        if sql.startswith("UPDATE run_resource_snapshots"):
            return Cursor([{"snapshot_id": self.snapshots[args[2]], "phase": 1}])
        if sql.startswith("UPDATE model_usage SET resource_snapshot_id"):
            self.ledger[args[-1]].update(resource_snapshot_id=args[0], resource_phase=args[1])
            return Cursor()
        if sql.startswith("UPDATE model_usage SET effective_parameters"):
            self.ledger[args[-1]]["effective_parameters"] = args[0].obj
            return Cursor()
        if sql.startswith("UPDATE model_usage SET estimate_method"):
            self.ledger[args[-1]]["estimate_method"] = args[0]
            return Cursor()
        if sql.startswith("UPDATE model_usage SET status='KNOWN'"):
            self.ledger[args[-1]].update(status="KNOWN", input_tokens=args[0], output_tokens=args[1])
            return Cursor([{"call_id": args[-1]}])
        if sql.startswith("UPDATE model_usage SET status='UNKNOWN'"):
            if args[-1] in self.ledger:
                self.ledger[args[-1]]["status"] = "UNKNOWN"
                return Cursor([{"call_id": args[-1]}])
            return Cursor()
        if sql.startswith("SELECT owner_id,price_version_id,status"):
            row = self.ledger.get(args[0])
            return Cursor([row] if row else [])
        if sql.startswith("SELECT owner_id FROM model_usage"):
            row = self.ledger.get(args[0])
            return Cursor([row] if row else [])
        if sql.startswith(("SELECT * FROM model_price_versions", "SELECT * FROM money_budgets",
                           "SELECT * FROM (SELECT DISTINCT ON(item)", "SELECT h.*,p.unit_price")):
            return Cursor()
        raise AssertionError("Unexpected callback SQL: " + sql)


async def noop(*args, **kwargs):
    return None


async def value(item):
    return item


def setup(monkeypatch):
    owner, model = uuid4(), str(uuid4())
    config = ModelConfig(id=model, revision=1, name="F01 actual callback", model="eval::AET-F01::v1",
                         base_url="http://127.0.0.1:8199/controlled-model", max_output_tokens=4096)
    database, state = Database(config), profile.ProfileState()
    state.register_model(model, "AET-F01", "lead", owner)
    calls = []

    async def get(identifier):
        assert identifier == model
        return database.config

    def factory(configuration):
        calls.append((configuration.revision, configuration.temperature))
        return profile.model_factory(configuration, state)

    runtime = WorkflowRuntime(database, SimpleNamespace(get=get), None, factory)
    monkeypatch.setattr("app.permissions.require_account", noop)
    monkeypatch.setattr("app.usage.authorize", noop)
    monkeypatch.setattr(runtime, "pins", lambda *args, **kwargs: value({}))
    monkeypatch.setattr(runtime, "routed", lambda config, *args, **kwargs: value(config))
    return database, state, owner, model, runtime, calls


async def invoke(database, owner, model, runtime, definition, root=None):
    root = root or uuid4()
    database.policies[root] = definition
    database.snapshots[root] = uuid4()
    scope = ResourceRun(database, owner, "workflow", root)
    scope.values[("model", model)] = database.config.model_dump(exclude={"api_key"})
    token = active_resources.set(scope)
    try:
        result = await runtime.run(Definition.model_validate(definition), owner, model, "", "MiXeD", root, {})
    finally:
        active_resources.reset(token)
    return root, result


async def test_original_1000_policy_stops_real_callback_before_any_controlled_generation(monkeypatch):
    database, state, owner, model, runtime, _ = setup(monkeypatch)
    graph = {**facts.graph("upper"), "token_limit": 1000}
    with pytest.raises(Exception) as caught:
        await invoke(database, owner, model, runtime, graph)
    error = caught.value
    while error.__cause__:
        error = error.__cause__
    assert isinstance(error, APIError) and error.code == "WORKFLOW_BUDGET_EXCEEDED"
    assert database.ledger == {} and state.case_summary(owner, "AET-F01")["calls"] == 0


async def test_four_real_callbacks_reserve_safely_then_record_actual_sixty_and_maintained_parameters(monkeypatch):
    database, state, owner, model, runtime, calls = setup(monkeypatch)
    for index, operation in enumerate(("upper", "lower", "upper", "lower")):
        if index == 2:
            database.config = database.config.model_copy(update={"revision": 2, "temperature": 0.4})
        root, output = await invoke(database, owner, model, runtime, facts.graph(operation))
        assert output == ("MIXED" if operation == "upper" else "mixed")
        row = next(item for item in database.ledger.values() if item["run_id"] == root)
        assert row["status"] == "KNOWN" and row["input_tokens"] == 10 and row["output_tokens"] == 5
        assert 4096 < row["estimated_input_tokens"] + row["output_token_reserve"] <= 8192
        assert row["resource_snapshot_id"] == database.snapshots[root]
        assert row["model_revision"] == (1 if index < 2 else 2)
        assert row["effective_parameters"].get("temperature") == (None if index < 2 else 0.4)
    assert calls == [(1, None), (1, None), (2, 0.4), (2, 0.4)]
    assert sum(row["input_tokens"] + row["output_tokens"] for row in database.ledger.values()) == 60
    assert state.case_summary(owner, "AET-F01")["calls"] == 4
    assert state.case_summary(owner, "AET-F01")["tokens"] == 60


async def test_real_native_callback_stops_second_call_in_same_root_before_provider(monkeypatch):
    database, state, owner, model, runtime, _ = setup(monkeypatch)
    root, _ = await invoke(database, owner, model, runtime, facts.graph("upper"))
    with pytest.raises(Exception) as caught:
        await invoke(database, owner, model, runtime, facts.graph("upper"), root=root)
    error = caught.value
    while error.__cause__:
        error = error.__cause__
    assert isinstance(error, APIError) and error.code == "WORKFLOW_BUDGET_EXCEEDED"
    assert len(database.ledger) == 1 and state.case_summary(owner, "AET-F01")["calls"] == 1


def test_larger_native_reservation_does_not_raise_actual_controlled_case_budget():
    assert profile.LIMITS["AET-F01"] == (1, 8192)
    assert profile.CASE_LIMITS["AET-F01"] == {"max_model_calls": 4, "max_tokens": 60, "max_roots": 4}
    assert facts.LIMITS["AET-F01"] == {"max_model_calls": 4, "max_tokens": 60, "max_roots": 4}
    assert facts.graph("upper")["token_limit"] == facts.graph("lower")["token_limit"] == 8192
    assert facts.graph("child")["token_limit"] == 1000
