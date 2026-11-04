"""Actual compiler/WorkWorkflow control engineering; all I/O remains substituted."""

from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import evaluation_flow_code as code_profile
import evaluation_target as target_profile
import pytest
import test_f05_target_deadline as activation_fence
from app import workflow_compiler as compiler
from app import workflow_definition as schema
from app import workflow_runtime as runtime
from app.workflow_work import WorkWorkflow
from evaluation_runner import fixed_code_contract as contract
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

boot = activation_fence.boot


def test_rebased_provider_guard_rejects_before_controlled_reservation(monkeypatch):
    from langchain_core.messages import HumanMessage

    state = target_profile.ProfileState()
    calls = []

    def deny(case_id):
        calls.append(case_id)
        raise RuntimeError("parent effect denied")

    state.parent_effect_guard = deny
    monkeypatch.setattr(
        target_profile, "current_scope", lambda: SimpleNamespace(owner=uuid4(), mode="workflow", run=uuid4())
    )
    with pytest.raises(RuntimeError, match="parent effect denied"):
        state.reserve("AET-F07", "lead", [HumanMessage(content="timeout")])
    assert calls == ["AET-F07"]
    assert state.roots == {} and not state.calls and not state.observations


async def test_rebased_workflow_guard_denies_before_node_event_or_execution(monkeypatch):
    from app.resource_runtime import ResourceRun, active_resources

    calls = []

    async def deny(boundary):
        calls.append(boundary)
        raise RuntimeError("parent workflow denied")

    database = Database()
    database.evaluation_effect_guard = deny
    service = runtime.WorkflowRuntime(database, None, None, lambda _: pytest.fail("No provider"))
    monkeypatch.setattr("app.permissions.require_account", noop)
    monkeypatch.setattr(service, "pins", noop)
    monkeypatch.setattr(service, "execute", lambda *args, **kwargs: pytest.fail("No node execution"))
    owner, root = uuid4(), uuid4()
    token = active_resources.set(ResourceRun(database, owner, "workflow", root))
    try:
        with pytest.raises((RuntimeError, compiler.WorkflowNodeFailed)):
            await service.run(http_graph(), owner, "fixed", "", "input", root, {})
    finally:
        active_resources.reset(token)
    assert calls == ["tool"] and database.events == []


async def test_rebased_code_create_inherits_parent_guard_before_remote_create(tmp_path, monkeypatch, boot):
    boot.activate()
    import app.durable_sandboxes as durable

    owner, run, token, namespace, session = (uuid4() for _ in range(5))
    actions = []
    owner = boot.target.owner_id
    profiles = SimpleNamespace(
        lock=Lock(), models={"model": {"case_id": contract.CASE, "role": "lead", "owner_id": str(owner)}}
    )

    class DB:
        async def fetch(self, sql, args=()):
            if sql.startswith("SELECT network_domains"):
                return [{"network_domains": []}]
            if sql.startswith("SELECT id FROM work_sandbox_namespace"):
                return [{"id": namespace}]
            if sql.startswith("SELECT count(*)"):
                return [{"count": 1 if "work_queue_runs" in sql else 0}]
            return [{"owner_id": owner, "model_id": "model", "snapshot": {"definition": contract.definition()}}]

        async def execute(self, sql, args):
            assert "state='RELEASED'" in sql and args == (session,)
            actions.append("known-unused-release")

        async def evaluation_effect_guard(self, boundary):
            actions.append(boundary)
            raise RuntimeError("parent sandbox denied")

    async def reserve(*args):
        actions.append("local-reserve")
        return {"id": session}

    monkeypatch.setattr(durable, "policy", noop)
    monkeypatch.setattr(durable.DurableSandboxes, "reserve", reserve)
    monkeypatch.setattr(durable.Sandbox, "create", lambda *args, **kwargs: pytest.fail("No remote create"))
    service = code_profile.CodeSandboxes(
        DB(),
        SimpleNamespace(api_key="synthetic-engineering-only", use_server_proxy=True),
        tmp_path / "bound.json",
        profiles,
        enabled=True,
    )
    assert service.activate(
        boot.guard,
        {
            "schema_version": 1,
            "enforcement": "controlled-model-factory-v1",
            "scope": "resource-run-root",
            "profiles": {"AET-F05": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}},
        },
        reviewed={
            "sandbox_bounds": {"AET-F05": 10},
            "f05_deadline": {
                "definition_version": 2,
                **{key: boot.context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id")},
            },
        },
    )
    assert type(service).create is code_profile.BoundedSandboxes.create
    with pytest.raises(RuntimeError, match="parent sandbox denied"):
        await service.create(run, token, "coding")
    assert actions == ["local-reserve", "sandbox", "known-unused-release"]
    assert service.attempts == 1  # A denied parent effect cannot silently refund the persisted creation budget.


def http_graph(declared=True):
    from evaluation_runner.fixed_fixtures import transform

    return schema.Definition.model_validate(transform())


class Database:
    def __init__(self):
        self.events = []

    async def fetch(self, sql, args):
        if sql.startswith("SELECT count(DISTINCT"):
            return [{"count": len({row["node_id"] for row in self.events if row["event"] == "node.started"})}]
        if "FROM workflow_node_events" in sql:
            return (
                [1] if any(row["node_id"] == args[1] and row["event"] == "node.started" for row in self.events) else []
            )
        raise AssertionError("Unexpected DB substitute query")

    async def execute(self, sql, args):
        assert sql.startswith("INSERT INTO workflow_node_events")
        self.events.append(args[3].obj)


class Journal:
    def __init__(self):
        self.rows = []

    async def start(self, name, args):
        self.rows.append((name, args))
        return len(self.rows)

    async def finish(self, *args):
        pass


class Backend:
    def __init__(self):
        self.files, self.calls = {}, 0

    async def aupload_files(self, files):
        self.files.update(files)
        return [SimpleNamespace(error=None) for _ in files]

    async def aexecute_workflow(self, command, *, seconds, arguments):
        assert command == "python3 /workspace/workflow-code.py < /workspace/workflow-code.input.txt"
        assert seconds == 5 and arguments == contract.arguments("code")
        assert self.files == {"/workflow-code.py": contract.CODE.encode(), "/workflow-code.input.txt": b"seed"}
        self.calls += 1
        # The real code is never executed on this host; this is a controlled backend result.
        return SimpleNamespace(exit_code=0, output=contract.OUTPUT, truncated=False)


async def noop(*args, **kwargs):
    pass


@pytest.mark.parametrize("reject", [False, True])
async def test_real_workflow_three_interrupts_fixed_arguments_and_reject_zero_code(monkeypatch, reject):
    database, backend = Database(), Backend()
    run = {"id": uuid4(), "owner_id": uuid4(), "lease_token": uuid4()}
    engine = SimpleNamespace(
        database=database,
        models=None,
        capabilities_factory=lambda _: None,
        model_factory=lambda _: pytest.fail("No model"),
        events=SimpleNamespace(emit=noop),
        saver=InMemorySaver(),
    )
    flow = WorkWorkflow(
        engine,
        run,
        {
            "workflow_run_id": uuid4(),
            "version_id": None,
            "snapshot": {"definition": contract.definition(), "model_id": "fixed", "instructions": ""},
        },
        backend,
        noop,
    )
    monkeypatch.setattr(flow, "authority", noop)
    flow.pause = SimpleNamespace(check=noop)
    flow.journal = Journal()
    config = {"configurable": {"thread_id": "work:" + str(run["id"])}}
    graph = await flow.graph()
    result = await graph.ainvoke({"input": "seed", "values": {}, "output": "", "last_output": ""}, config)
    nodes = ("before",) if reject else ("before", "code", "after")
    for _index, node in enumerate(nodes):
        interrupt = result["__interrupt__"][0].value
        action = interrupt["action_requests"][0]
        assert action["name"] == ("workflow_code" if node == "code" else "workflow_approval")
        assert action["args"] == contract.arguments(node)
        assert backend.calls == (1 if node == "after" else 0)
        if reject:
            with pytest.raises(compiler.WorkflowNodeFailed):
                await graph.ainvoke(Command(resume={"decisions": [{"type": "reject"}]}), config)
        else:
            result = await graph.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert backend.calls == (0 if reject else 1)
    assert len(flow.journal.rows) == (
        0 if reject else 2
    )  # Backend's own workflow_code journal is separately substituted.
    if not reject:
        assert result["output"] == "seed-code"
    assert len(database.events) == (5 if reject else 13)


@pytest.mark.parametrize("fixed,maximum", [(True, 10), (False, 2)])
async def test_actual_profile_reservation_count_is_case_specific_and_survives_reload(
    tmp_path, monkeypatch, fixed, maximum, boot
):
    boot.activate()
    owner, run, token = uuid4(), uuid4(), uuid4()
    owner = boot.target.owner_id
    profiles = SimpleNamespace(
        lock=Lock(),
        models={"model": {"case_id": "AET-F05" if fixed else "AET-W01", "role": "lead", "owner_id": str(owner)}},
    )

    class DB:
        async def fetch(self, sql, args=()):
            if sql.startswith("SELECT count(*)"):
                return [{"count": 1 if "work_queue_runs" in sql else 0}]
            return [{"owner_id": owner, "model_id": "model", "snapshot": {"definition": contract.definition()}}]

    async def reserve(*args):
        return {"id": uuid4()}

    monkeypatch.setattr(code_profile.DurableSandboxes, "reserve", reserve)
    path = tmp_path / "bound.json"
    connection = SimpleNamespace(api_key="synthetic-engineering-only", use_server_proxy=True)
    service = code_profile.CodeSandboxes(DB(), connection, path, profiles, enabled=True)
    assert service.activate(
        boot.guard,
        {
            "schema_version": 1,
            "enforcement": "controlled-model-factory-v1",
            "scope": "resource-run-root",
            "profiles": {"AET-F05": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}},
        },
        reviewed={
            "sandbox_bounds": {"AET-F05": 10},
            "f05_deadline": {
                "definition_version": 2,
                **{key: boot.context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id")},
            },
        },
    )
    for _ in range(maximum):
        assert await service.reserve(run, token, "coding")
    assert service.attempts == maximum and service.capability()["max_created"] == 2
    service = code_profile.CodeSandboxes(DB(), connection, path, profiles, enabled=True)
    assert service.activate(
        boot.guard,
        {
            "schema_version": 1,
            "enforcement": "controlled-model-factory-v1",
            "scope": "resource-run-root",
            "profiles": {"AET-F05": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}},
        },
        reviewed={
            "sandbox_bounds": {"AET-F05": 10},
            "f05_deadline": {
                "definition_version": 2,
                **{key: boot.context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id")},
            },
        },
    )
    with pytest.raises(RuntimeError, match="budget"):
        await service.reserve(run, token, "coding")
    assert service.attempts == maximum


async def test_f05_concurrent_admission_returns_without_consuming_or_overriding_global_capacity(
    tmp_path, monkeypatch, boot
):
    boot.activate()
    owner = uuid4()
    owner = boot.target.owner_id
    profiles = SimpleNamespace(
        lock=Lock(), models={"model": {"case_id": "AET-F05", "role": "lead", "owner_id": str(owner)}}
    )

    class DB:
        async def fetch(self, sql, args=()):
            return (
                [{"count": 1}]
                if sql.startswith("SELECT count(*)")
                else [{"owner_id": owner, "model_id": "model", "snapshot": {"definition": contract.definition()}}]
            )

    service = code_profile.CodeSandboxes(
        DB(),
        SimpleNamespace(api_key="synthetic-engineering-only", use_server_proxy=True),
        tmp_path / "bound.json",
        profiles,
        enabled=True,
    )
    assert service.activate(
        boot.guard,
        {
            "schema_version": 1,
            "enforcement": "controlled-model-factory-v1",
            "scope": "resource-run-root",
            "profiles": {"AET-F05": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}},
        },
        reviewed={
            "sandbox_bounds": {"AET-F05": 10},
            "f05_deadline": {
                "definition_version": 2,
                **{key: boot.context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id")},
            },
        },
    )
    assert await service.reserve(uuid4(), uuid4(), "coding") is None
    assert service.attempts == 0


async def test_unreviewed_f05_ten_creation_path_is_blocked_by_default(tmp_path):
    owner = uuid4()
    profiles = SimpleNamespace(
        lock=Lock(), models={"model": {"case_id": "AET-F05", "role": "lead", "owner_id": str(owner)}}
    )

    class DB:
        async def fetch(self, sql, args):
            return [{"owner_id": owner, "model_id": "model", "snapshot": {"definition": contract.definition()}}]

    service = code_profile.CodeSandboxes(
        DB(),
        SimpleNamespace(api_key="synthetic-engineering-only", use_server_proxy=True),
        tmp_path / "bound.json",
        profiles,
    )
    assert service.capability()["fixed_cases"]["AET-F05"]["enabled"] is False
    with pytest.raises(RuntimeError, match="blocked"):
        await service.reserve(uuid4(), uuid4(), "coding")
    assert service.attempts == 0
