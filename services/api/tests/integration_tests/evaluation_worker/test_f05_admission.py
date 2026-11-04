"""Controlled SQL-result semantics only; the actual JOIN contract still needs PG."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
import test_f05_target_deadline as fence
from test_f05_activation import code, limits, reviewed, service

boot = fence.boot


def admission(boot, monkeypatch, *, fixed=True, leased=True, snapshot=None, run_owner=None):
    value, _state = service(boot, monkeypatch)
    boot.activate()
    value.activate(boot.guard, limits(), reviewed=reviewed(boot))
    calls = []

    class Database:
        async def fetch(self, query, args=()):
            if query.startswith("SELECT count(*)"):
                return [{"count": 3 if "work_queue_runs" in query else 0}]
            # The old inner JOIN discards a valid leased root whose workflow link is absent.
            if not leased or ("FROM work_queue_runs r JOIN work_run_workflows" in query and snapshot is None):
                return []
            return [
                {
                    "owner_id": run_owner or boot.target.owner_id,
                    "model_id": "fixed" if fixed else "ordinary",
                    "snapshot": snapshot,
                }
            ]

    async def reserve(*args):
        calls.append("native-reserve")
        return {"id": uuid4()} if leased else None

    value.database = Database()
    monkeypatch.setattr(code.DurableSandboxes, "reserve", reserve)
    return SimpleNamespace(service=value, calls=calls)


@pytest.mark.parametrize("snapshot", [None, {}, {"definition": {"execution": "work"}}])
async def test_known_fixed_lease_cannot_fall_back_when_owned_graph_is_missing(boot, monkeypatch, snapshot):
    value = admission(boot, monkeypatch, snapshot=snapshot)
    with pytest.raises(ValueError, match="exact fixed graph"):
        await value.service.reserve(uuid4(), uuid4(), "coding")
    assert value.calls == [] and value.service.attempts == 0


async def test_fixed_owned_graph_preserves_the_original_ten_bound(boot, monkeypatch):
    value = admission(boot, monkeypatch, snapshot={"definition": code.definition()})
    for _ in range(10):
        assert await value.service.reserve(uuid4(), uuid4(), "coding")
    with pytest.raises(RuntimeError, match="budget"):
        await value.service.reserve(uuid4(), uuid4(), "coding")
    assert value.calls == ["native-reserve"] * 10


async def test_ordinary_run_keeps_original_two_even_without_workflow(boot, monkeypatch):
    value = admission(boot, monkeypatch, fixed=False)
    for _ in range(2):
        assert await value.service.reserve(uuid4(), uuid4(), "coding")
    with pytest.raises(RuntimeError, match="budget"):
        await value.service.reserve(uuid4(), uuid4(), "coding")
    assert value.calls == ["native-reserve"] * 2


async def test_invalid_or_expired_lease_still_reaches_native_none_and_refunds_unused_slot(boot, monkeypatch):
    value = admission(boot, monkeypatch, leased=False)
    assert await value.service.reserve(uuid4(), uuid4(), "coding") is None
    assert value.calls == ["native-reserve"] and value.service.attempts == 0


@pytest.mark.parametrize("fault", ["foreign_run_owner", "foreign_binding_owner", "wrong_role"])
async def test_known_f05_bad_authority_never_degrades_to_ordinary_two(boot, monkeypatch, fault):
    value = admission(
        boot,
        monkeypatch,
        snapshot={"definition": code.definition()},
        run_owner=str(uuid4()) if fault == "foreign_run_owner" else None,
    )
    if fault == "foreign_binding_owner":
        value.service.profiles.models["fixed"]["owner_id"] = str(uuid4())
    elif fault == "wrong_role":
        value.service.profiles.models["fixed"]["role"] = "embedding"
    with pytest.raises(ValueError, match="owner differs"):
        await value.service.reserve(uuid4(), uuid4(), "coding")
    assert value.calls == [] and value.service.attempts == 0


async def test_another_registered_case_still_keeps_ordinary_coding_two(boot, monkeypatch):
    value = admission(boot, monkeypatch, snapshot=None)
    value.service.profiles.models["fixed"]["case_id"] = "AET-A02"
    for _ in range(2):
        assert await value.service.reserve(uuid4(), uuid4(), "coding")
    with pytest.raises(RuntimeError, match="budget"):
        await value.service.reserve(uuid4(), uuid4(), "coding")
    assert value.calls == ["native-reserve"] * 2
