"""Run with the API venv; these tests never import DeepEval or start a target."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.resource_runtime import ResourceRun, active_resources
from evaluation_target import LIMITS, ProfileState, model_factory
from langchain_core.messages import HumanMessage, ToolMessage


def configuration(case="AET-A02", name="AE lead"):
    role = "reviewer" if name == "AE AET-W05 reviewer" else "lead"
    return SimpleNamespace(id=case + ":" + role, model=f"eval::{case}::v1", name=name)


def registered_factory(config, state):
    if config.model.split('::')[1] in {'AET-W05', 'AET-W06'}:
        state.team_audit = SimpleNamespace(healthy=lambda: None)  # Protocol-only unit fixture, not a tool evidence observer.
    state.register_model(config.id, config.model.split("::")[1],
                         "reviewer" if config.name == "AE AET-W05 reviewer" else "lead", active_resources.get().owner)
    return model_factory(config, state)


def scope():
    return ResourceRun(None, uuid4(), "ask", uuid4())


def test_factory_rejects_missing_scope_and_unknown_supplier():
    state = ProfileState()
    with pytest.raises(RuntimeError):
        model_factory(configuration(), state)
    with pytest.raises(ValueError):
        model_factory(SimpleNamespace(model="default-paid-model", name="unsafe"), state)
    assert not state.calls


def test_actual_factory_shares_cap_across_reconstructed_models():
    state, root = ProfileState(), scope()
    token = active_resources.set(root)
    try:
        first = registered_factory(configuration(), state).invoke([HumanMessage(content="只用JSON回答。记住虚拟项目名星桥。")])
        assert first.usage_metadata["total_tokens"] == 15
        with pytest.raises(RuntimeError, match="budget"):
            registered_factory(configuration(), state).invoke([HumanMessage(content="second")])
        assert sum(state.calls.values()) == LIMITS["AET-A02"][0] == 1
        assert state.roots[(str(root.owner), root.mode, str(root.run))]["tokens"] == 15
    finally:
        active_resources.reset(token)


def test_shared_root_cap_is_atomic_across_threads():
    state, root = ProfileState(), scope()

    def generate(_):
        token = active_resources.set(root)
        try:
            registered_factory(configuration(), state).invoke([HumanMessage(content="context")])
            return True
        except RuntimeError:
            return False
        finally:
            active_resources.reset(token)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(generate, range(16))) == 1
    assert sum(state.calls.values()) == 1


def test_unknown_member_generation_blocks_lead_on_same_root():
    state, root = ProfileState(), scope()
    token = active_resources.set(root)
    try:
        member = registered_factory(configuration("AET-W05", "AE AET-W05 reviewer"), state)
        first = member.invoke([HumanMessage(content="review")])
        assert first.tool_calls[0]["name"] == "read_file"
        with pytest.raises(TimeoutError):
            member.invoke([HumanMessage(content="review"), ToolMessage(content="team-evidence", tool_call_id="ae-member-read")])
        with pytest.raises(RuntimeError, match="budget"):
            registered_factory(configuration("AET-W05"), state).invoke([HumanMessage(content="lead")])
        assert sum(state.calls.values()) == 2
    finally:
        active_resources.reset(token)


def test_shared_parent_member_query_budget_blocks_before_next_provider_effect():
    state, root = ProfileState(), scope()
    token = active_resources.set(root)
    try:
        first = registered_factory(configuration("AET-W06"), state).invoke([HumanMessage(content="read input")])
        assert first.usage_metadata["total_tokens"] == 15
        for role, amount in (("reviewer",15),("reviewer",15),("embedding",7),("reviewer",15),("lead",15933)):
            key = state.reserve("AET-W06",role,[],cost=amount)
            state.complete(key)
        with pytest.raises(RuntimeError, match="budget"):
            registered_factory(configuration("AET-W06"), state).invoke([HumanMessage(content="next")])
        assert sum(state.calls.values()) == 6
    finally:
        active_resources.reset(token)


def test_other_root_has_its_own_budget_and_context_assertions():
    state = ProfileState()
    owner = uuid4()
    for _ in range(2):
        root = scope()
        root.owner = owner
        token = active_resources.set(root)
        try:
            result = registered_factory(configuration(), state).invoke([
                HumanMessage(content="只用JSON回答。记住虚拟项目名星桥。"),
                HumanMessage(content="将人数从2改成3，输出project和people两个字段。"),
            ])
            assert '"people": 3' in result.content
            observed = state.summary(root.owner, root.mode, root.run)
            assert observed[0]["human_turns"] == 2
            assert observed[0]["has_project_context"] is True
            assert "星桥" not in str(observed)  # Only booleans/digest, no private messages.
        finally:
            active_resources.reset(token)
    assert len(state.roots) == 2


def test_restart_keeps_consumed_cap_and_inflight_unknown(tmp_path):
    path, root = tmp_path / "state.json", scope()
    state = ProfileState(path)
    token = active_resources.set(root)
    try:
        registered_factory(configuration(), state).invoke([HumanMessage(content="first")])
        restored = ProfileState(path)
        with pytest.raises(RuntimeError, match="budget"):
            registered_factory(configuration(), restored).invoke([HumanMessage(content="second")])
    finally:
        active_resources.reset(token)
    other = scope()
    token = active_resources.set(other)
    try:
        state.reserve("AET-A01", "lead", [HumanMessage(content="in flight")])
        restored = ProfileState(path)
        with pytest.raises(RuntimeError, match="budget"):
            registered_factory(configuration("AET-A01"), restored).invoke([HumanMessage(content="do not retry")])
    finally:
        active_resources.reset(token)


def test_renaming_sealed_reviewer_does_not_turn_it_into_lead(tmp_path):
    state, root = ProfileState(tmp_path / "state.json"), scope()
    token = active_resources.set(root)
    try:
        config = configuration("AET-W05", "AE AET-W05 reviewer")
        state.register_model(config.id, "AET-W05", "reviewer", root.owner)
        config.name = "ordinary renamed label"
        restored = ProfileState(state.path)
        assert model_factory(config, restored).role == "reviewer"
        restored.team_audit = SimpleNamespace(healthy=lambda: None)  # Unit protocol context only.
        first = model_factory(config, restored).invoke([HumanMessage(content="review")])
        assert first.tool_calls[0]["name"] == "read_file"
        with pytest.raises(TimeoutError):
            model_factory(config, restored).invoke([HumanMessage(content="review"), ToolMessage(content="team-evidence", tool_call_id="ae-member-read")])
        with pytest.raises(ValueError):
            restored.register_model(config.id, "AET-W05", "lead", root.owner)
        unregistered = SimpleNamespace(id="other-id", model=config.model, name="AE AET-W05 reviewer")
        with pytest.raises(ValueError):
            model_factory(unregistered, restored)
    finally:
        active_resources.reset(token)


def test_sealed_model_binding_cannot_be_used_by_another_owner():
    state, root = ProfileState(), scope()
    config = configuration()
    state.register_model(config.id, "AET-A02", "lead", uuid4())
    token = active_resources.set(root)
    try:
        with pytest.raises(ValueError, match="owner-scoped"):
            model_factory(config, state)
        assert not state.calls
    finally:
        active_resources.reset(token)
