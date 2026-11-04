"""Actual compiler/model-node engineering with controlled state and no DB/HTTP."""

from types import SimpleNamespace
from uuid import uuid4

import evaluation_target as profile
import pytest
from app.config import ModelConfig
from app.resource_runtime import ResourceRun, active_resources
from app.workflow_compiler import WorkflowNodeFailed
from app.workflow_definition import Definition, validate
from app.workflow_runtime import WorkflowRuntime
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage

from evaluation_runner import fixed_versioned_workflow as facts


class Database:
    def __init__(self):
        self.events = []

    async def execute(self, sql, parameters):
        assert sql.startswith("INSERT INTO workflow_node_events")
        self.events.append({"run_id": parameters[0], "node_id": parameters[1], "event": parameters[2], "data": parameters[3].obj})


class Recorder(BaseCallbackHandler):
    def __init__(self):
        self.usage = []

    def on_llm_end(self, response, **kwargs):
        self.usage.append(response.generations[0][0].message.usage_metadata)


async def authority(*args, **kwargs):
    return None


def context(state, owner, run):
    return active_resources.set(ResourceRun(None, owner, "workflow", run))


@pytest.mark.parametrize("kind", ["upper", "lower", "child"])
def test_definitions_are_accepted_by_real_workflow_validator(kind):
    validate(Definition.model_validate(facts.graph(kind)))


async def test_actual_model_node_uses_two_instructions_and_live_maintained_config_across_four_roots(monkeypatch):
    owner, model = uuid4(), str(uuid4())
    state = profile.ProfileState()
    state.register_model(model, "AET-F01", "lead", owner)
    calls, recorder, database = [], Recorder(), Database()
    config = ModelConfig(id=model, revision=1, name="F01", model="eval::AET-F01::v1", base_url="http://127.0.0.1:8199/controlled-model")

    async def get_model(identifier):
        assert identifier == model
        return config

    def factory(value):
        calls.append((value.revision, value.temperature))
        return profile.model_factory(value, state)

    runtime = WorkflowRuntime(database, SimpleNamespace(get=get_model), None, factory)
    monkeypatch.setattr("app.permissions.require_account", authority)
    monkeypatch.setattr(runtime, "pins", lambda *args, **kwargs: async_value({}))
    monkeypatch.setattr(runtime, "routed", lambda value, *args, **kwargs: async_value(value))
    monkeypatch.setattr(runtime, "recorder", lambda *args: recorder)
    roots = []
    for index, operation in enumerate(("upper", "lower", "upper", "lower")):
        if index == 2:
            config = config.model_copy(update={"revision": 2, "temperature": 0.4})
        run = uuid4()
        roots.append(run)
        token = context(state, owner, run)
        try:
            output = await runtime.run(Definition.model_validate(facts.graph(operation)), owner, model, "", facts.INPUT, run, {})
        finally:
            active_resources.reset(token)
        assert output == ("MIXED" if operation == "upper" else "mixed")
    assert calls == [(1, None), (1, None), (2, 0.4), (2, 0.4)]
    assert len(database.events) == 24 and {row["run_id"] for row in database.events} == set(roots)
    assert recorder.usage == [{"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}] * 4
    summary = state.case_summary(owner, "AET-F01")
    assert summary["complete"] is True and summary["calls"] == 4 and summary["tokens"] == 60
    token = context(state, owner, uuid4())
    try:
        with pytest.raises(RuntimeError, match="budget"):
            profile.model_factory(config, state).invoke([SystemMessage(content="AE-F01-upper-v1"), HumanMessage(content=facts.INPUT)])
    finally:
        active_resources.reset(token)
    assert state.case_summary(owner, "AET-F01")["calls"] == 4


async def async_value(value):
    return value


async def test_actual_nested_failure_preserves_parent_child_prefixes_and_shares_one_root(monkeypatch):
    owner, run, version = uuid4(), uuid4(), str(uuid4())
    database = Database()
    runtime = WorkflowRuntime(database, None, None, lambda _: pytest.fail("F03 must not invoke a model"))
    monkeypatch.setattr("app.permissions.require_account", authority)
    monkeypatch.setattr(runtime, "pins", lambda *args, **kwargs: async_value({}))
    monkeypatch.setattr(runtime, "published", lambda *args: async_value({"definition": facts.graph("child"), "model_id": "fixed", "instructions": ""}))
    parent = Definition.model_validate(facts.graph("parent", version))
    token = context(None, owner, run)
    try:
        with pytest.raises(WorkflowNodeFailed) as caught:
            await runtime.run(parent, owner, "fixed", "", "{}", run, {"workflows": {version: {"pins": {}}}})
    finally:
        active_resources.reset(token)
    assert caught.value.node_id == "action"
    assert {row["run_id"] for row in database.events} == {run}
    observed = [(row["node_id"], row["data"]["kind"], row["event"],
                 row["data"].get("output" if row["event"] == "node.completed" else "input"), row["data"].get("error_code"))
                for row in database.events]
    assert observed == facts.event_spec("AET-F03", 0, published=True)


async def test_actual_parent_initialization_succeeds_with_fixed_child_version(monkeypatch):
    owner, run, version = uuid4(), uuid4(), str(uuid4())
    database = Database()
    runtime = WorkflowRuntime(database, None, None, lambda _: pytest.fail("No F03 provider effect"))
    monkeypatch.setattr("app.permissions.require_account", authority)
    monkeypatch.setattr(runtime, "pins", lambda *args, **kwargs: async_value({}))
    monkeypatch.setattr(runtime, "published", lambda *args: async_value({"definition": facts.graph("child"), "model_id": "fixed", "instructions": ""}))
    token = context(None, owner, run)
    try:
        output = await runtime.run(Definition.model_validate(facts.graph("parent", version)), owner, "fixed", "", facts.GOOD, run,
                                   {"workflows": {version: {"pins": {}}}})
    finally:
        active_resources.reset(token)
    assert output == "ok"
    observed = [(row["node_id"], row["data"]["kind"], row["event"],
                 row["data"].get("output" if row["event"] == "node.completed" else "input"), row["data"].get("error_code"))
                for row in database.events]
    assert observed == facts.event_spec("AET-F03", 1)


def test_f03_root_budget_explicitly_prevents_any_model_effect():
    state, owner, run = profile.ProfileState(), uuid4(), uuid4()
    token = context(state, owner, run)
    try:
        with pytest.raises(RuntimeError, match="budget"):
            state.reserve("AET-F03", "lead", [HumanMessage(content="{}")])
    finally:
        active_resources.reset(token)
    assert state.case_summary(owner, "AET-F03")["calls"] == 0


@pytest.mark.parametrize("messages", [[HumanMessage(content="MiXeD")],
    [SystemMessage(content="AE-F01-upper-v1"), HumanMessage(content="other")],
    [SystemMessage(content="other"), HumanMessage(content="MiXeD")]])
def test_unregistered_f01_input_never_claims_known_success(messages):
    state, owner, run, model_id = profile.ProfileState(), uuid4(), uuid4(), str(uuid4())
    state.register_model(model_id, "AET-F01", "lead", owner)
    config = ModelConfig(id=model_id, name="F01", model="eval::AET-F01::v1", base_url="http://127.0.0.1:8199/controlled-model")
    token = context(state, owner, run)
    try:
        with pytest.raises(ValueError, match="Unregistered F01"):
            profile.model_factory(config, state).invoke(messages)
    finally:
        active_resources.reset(token)
    assert state.case_summary(owner, "AET-F01")["complete"] is False
