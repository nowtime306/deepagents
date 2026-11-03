import asyncio
from uuid import uuid4

import pytest
from app.durable_sandboxes import DurableSandboxes
from evaluation_sandbox_cap import BoundedSandboxes
from opensandbox.config import ConnectionConfig


def configuration():
    return ConnectionConfig(domain="127.0.0.1:1", api_key="unit-controlled-only", use_server_proxy=True)


def test_third_creation_is_blocked_before_parent_reservation_and_restart_does_not_reset(tmp_path, monkeypatch):
    parent_calls = []

    async def reserve(self, run_id, token, kind):
        parent_calls.append(run_id)
        return {"id": str(uuid4())}

    monkeypatch.setattr(DurableSandboxes, "reserve", reserve)

    async def exercise():
        path = tmp_path / "sandbox.json"
        service = BoundedSandboxes(None, configuration(), path)
        await service.reserve(uuid4(), uuid4(), "coding")
        await service.reserve(uuid4(), uuid4(), "coding")
        rebuilt = BoundedSandboxes(None, configuration(), path)
        with pytest.raises(RuntimeError, match="budget"):
            await rebuilt.reserve(uuid4(), uuid4(), "coding")
        assert rebuilt.capability()["attempts"] == 2

    asyncio.run(exercise())
    assert len(parent_calls) == 2


def test_only_known_local_no_admission_refunds(tmp_path, monkeypatch):
    responses = iter([None, RuntimeError("ambiguous reservation"), {"id": "reserved"}])

    async def reserve(self, *args):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(DurableSandboxes, "reserve", reserve)

    async def exercise():
        service = BoundedSandboxes(None, configuration(), tmp_path / "sandbox.json")
        assert await service.reserve(uuid4(), uuid4(), "coding") is None
        assert service.attempts == 0
        with pytest.raises(RuntimeError):
            await service.reserve(uuid4(), uuid4(), "coding")
        assert service.attempts == 1
        assert await service.reserve(uuid4(), uuid4(), "coding") == {"id": "reserved"}
        with pytest.raises(RuntimeError, match="budget"):
            await service.reserve(uuid4(), uuid4(), "coding")

    asyncio.run(exercise())


def test_browser_is_rejected_before_any_parent_create(tmp_path, monkeypatch):
    async def forbidden(*args):
        pytest.fail("Browser must not reach the generic manager")

    monkeypatch.setattr(DurableSandboxes, "create", forbidden)

    async def exercise():
        service = BoundedSandboxes(None, configuration(), tmp_path / "sandbox.json")
        with pytest.raises(ValueError):
            await service.create(uuid4(), uuid4(), "browser")
        assert service.attempts == 0

    asyncio.run(exercise())
