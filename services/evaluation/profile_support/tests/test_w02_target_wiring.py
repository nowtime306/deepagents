"""Private copies only: provider admission and real worker lifecycle with controlled empty queue."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.resource_runtime import ResourceRun, active_resources
from langchain_core.messages import HumanMessage
from test_work_consumer import controller


def module(name, filename, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / filename)
    loaded = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, loaded)
    spec.loader.exec_module(loaded)
    return loaded


def reserve(state, owner, root):
    token = active_resources.set(ResourceRun(None, owner, "work", root))
    try:
        return state.reserve("AET-W02", "lead", [HumanMessage(content="fixed")])
    finally:
        active_resources.reset(token)


def test_private_case_limit_counts_all_roots_and_survives_reconstruction(tmp_path, monkeypatch):
    target = module("private_w02_target", "evaluation_target.py", monkeypatch)
    state, owner = target.ProfileState(tmp_path / "case.json"), uuid4()
    for root in (uuid4(), uuid4()):
        for _ in range(3):
            state.complete(reserve(state, owner, root))
    for current in (state, target.ProfileState(state.path)):
        with pytest.raises(RuntimeError, match="case budget"):
            reserve(current, owner, uuid4())
        summary = current.case_summary(owner, "AET-W02")
        assert (summary["calls"], summary["tokens"], len(summary["roots"])) == (6, 90, 2)


def test_private_pending_reservation_blocks_other_root_and_poisons_cold_reconstruction(tmp_path, monkeypatch):
    target = module("private_w02_target", "evaluation_target.py", monkeypatch)
    state, owner = target.ProfileState(tmp_path / "case.json"), uuid4()
    reserve(state, owner, uuid4())
    for current in (state, target.ProfileState(state.path)):
        with pytest.raises(RuntimeError, match="case budget"):
            reserve(current, owner, uuid4())
        assert current.case_summary(owner, "AET-W02")["calls"] == 1


def test_private_target_preserves_f06_hard_zero_model_effect(tmp_path, monkeypatch):
    from types import SimpleNamespace

    target = module("private_w02_target", "evaluation_target.py", monkeypatch)
    state, owner, root = target.ProfileState(tmp_path / "case.json"), uuid4(), uuid4()
    state.register_model("f06", "AET-F06", "lead", owner)
    token = active_resources.set(ResourceRun(None, owner, "workflow", root))
    try:
        with pytest.raises(RuntimeError, match="budget"):
            target.model_factory(SimpleNamespace(id="f06", model="eval::AET-F06::v1"), state).invoke([HumanMessage(content="unexpected")])
    finally:
        active_resources.reset(token)
    assert not state.calls and state.case_summary(owner, "AET-F06")["calls"] == 0


def test_private_same_fixed_dispatch_callback_survives_actual_consumer_generation_rebuild(tmp_path, monkeypatch):
    loaded = module("private_w02_consumer", "evaluation_work_consumer.py", monkeypatch)
    previous, proof, root, _ = controller(tmp_path)
    calls = []

    async def dispatch(run):
        calls.append(run["task_id"])
        return {"executor": "fixed-verifier" if run["task_id"] == "registered" else "original-engine"}

    async def empty_tick(worker):
        return False

    monkeypatch.setattr(loaded.ObservedWorker, "tick", empty_tick)
    service = loaded.ConsumerController(previous.engine, previous.profiles, previous.sandboxes, previous.path, execute=dispatch)

    async def scenario():
        service.start()
        try:
            before = service.worker
            await service.registered()
            assert await before.execute({"task_id": "registered"}) == {"executor": "fixed-verifier"}
            result = await service.rebuild(proof, root)
            assert service.worker is not before and service.worker.execute is before.execute is dispatch
            assert result["before_worker_ids"] != result["after_worker_ids"]
            assert result["checkpoint_id_before"] == result["checkpoint_id_after"]
            assert await service.worker.execute({"task_id": "normal"}) == {"executor": "original-engine"}
            assert calls == ["registered", "normal"]
        finally:
            await service.close()

    asyncio.run(scenario())
