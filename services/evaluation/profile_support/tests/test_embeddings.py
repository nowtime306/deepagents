import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.resource_runtime import ResourceRun, active_resources
from evaluation_embeddings import install_embeddings
from evaluation_target import ProfileState, model_factory
from langchain_core.messages import HumanMessage


class Meter:
    def __init__(self, *, fail=False):
        self.reported = []
        self.errors = 0
        self.fail = fail
        self.started = 0

    async def start(self, texts):
        self.started += 1
        return "real-meter-interface-receipt"

    async def finish(self, receipt, usage):
        if self.fail:
            raise TimeoutError("Controlled metering failure")
        self.reported.append(usage)

    async def error(self, receipt, error):
        self.errors += 1


def setup(tmp_path):
    state = ProfileState(tmp_path / "state.json")
    owner = uuid4()
    app = SimpleNamespace(state=SimpleNamespace(knowledge=SimpleNamespace(embedder=None, reranker=None)))
    state.register_model("chat", "AET-A03", "lead", owner)
    state.register_model("vector", "AET-A03", "embedding", owner)
    restore = install_embeddings(app, state)
    config = SimpleNamespace(id="vector", kind="embedding", protocol="openai", model="eval::AET-A03::v1")
    root = ResourceRun(None, owner, "ask", uuid4())
    return state, app, restore, config, root


def test_controlled_embedding_and_chat_share_one_durable_root_budget(tmp_path):
    state, app, restore, config, root = setup(tmp_path)
    token = active_resources.set(root)
    try:
        chat = model_factory(SimpleNamespace(id="chat", model=config.model, name="lead"), state)
        chat.invoke([HumanMessage(content="星桥产品保修多久")])
        meter = Meter()
        vectors = asyncio.run(app.state.knowledge.embedder(config, ["星桥产品保修三年，须提供发票。"], meter=meter))
        assert vectors == [[1.0, 1.0, 1.0, 0.0]]
        assert meter.reported == [{"prompt_tokens": 7, "total_tokens": 7}]
        observed = state.root_summary(root.owner, root.mode, root.run)
        assert observed["provider_calls"] == 2 and observed["tokens"] == 22 and observed["complete"] is True
    finally:
        active_resources.reset(token)
        restore()


def test_budget_rejection_happens_before_native_usage_intent(tmp_path):
    state, app, restore, config, root = setup(tmp_path)
    token = active_resources.set(root)
    try:
        chat = model_factory(SimpleNamespace(id="chat", model=config.model, name="lead"), state)
        for _ in range(6):
            chat.invoke([HumanMessage(content="consume the registered root cap")])
        meter = Meter()
        with pytest.raises(RuntimeError, match="budget"):
            asyncio.run(app.state.knowledge.embedder(config, ["星桥"], meter=meter))
        assert meter.started == 0
        assert meter.errors == 0
        assert meter.reported == []
    finally:
        active_resources.reset(token)
        restore()


def test_embedding_unknown_metering_poisons_followup_chat_and_survives_rebuild(tmp_path):
    state, app, restore, config, root = setup(tmp_path)
    token = active_resources.set(root)
    try:
        meter = Meter(fail=True)
        with pytest.raises(TimeoutError):
            asyncio.run(app.state.knowledge.embedder(config, ["星桥"], meter=meter))
        assert meter.errors == 1
        rebuilt = ProfileState(state.path)
        with pytest.raises(RuntimeError, match="budget"):
            model_factory(SimpleNamespace(id="chat", model=config.model, name="lead"), rebuilt).invoke([HumanMessage(content="next")])
        assert rebuilt.root_summary(root.owner, root.mode, root.run)["complete"] is False
    finally:
        active_resources.reset(token)
        restore()


def test_embedding_without_scope_or_native_meter_has_no_provider_effect(tmp_path):
    state, app, restore, config, root = setup(tmp_path)
    try:
        with pytest.raises(ValueError):
            asyncio.run(app.state.knowledge.embedder(config, ["text"], meter=Meter()))
        token = active_resources.set(root)
        try:
            with pytest.raises(ValueError):
                asyncio.run(app.state.knowledge.embedder(config, ["text"]))
        finally:
            active_resources.reset(token)
        assert not state.calls
    finally:
        restore()


def test_meter_start_failure_preserves_unknown_reservation_without_fabricating_error_row(tmp_path):
    class StartFailure(Meter):
        async def start(self, texts):
            self.started += 1
            raise TimeoutError("Native meter admission failed")

    state, app, restore, config, root = setup(tmp_path)
    token = active_resources.set(root)
    try:
        meter = StartFailure()
        with pytest.raises(TimeoutError):
            asyncio.run(app.state.knowledge.embedder(config, ["星桥"], meter=meter))
        assert meter.started == 1 and meter.errors == 0 and meter.reported == []
        rebuilt = ProfileState(state.path)
        assert rebuilt.roots[(str(root.owner), root.mode, str(root.run))]["tokens"] == 7
        assert rebuilt.root_summary(root.owner, root.mode, root.run)["complete"] is False
        with pytest.raises(RuntimeError, match="budget"):
            model_factory(SimpleNamespace(id="chat", model=config.model, name="lead"), rebuilt).invoke([HumanMessage(content="no retry")])
    finally:
        active_resources.reset(token)
        restore()
