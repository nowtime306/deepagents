"""Admission and lifecycle behavior for the internal sandbox adapter."""

import asyncio
from datetime import timedelta

import pytest
from opensandbox.config import ConnectionConfig

from app.sandbox_service import SandboxService


class FakeSandbox:
    def __init__(self):
        self.destroyed = False
        self.fail_destroy = False

    async def destroy(self):
        if self.fail_destroy:
            raise RuntimeError("destroy failed")
        self.destroyed = True


@pytest.mark.asyncio
async def test_limits_queue_and_safe_create_parameters(monkeypatch):
    calls = []

    async def create(image, **kwargs):
        sandbox = FakeSandbox()
        calls.append((image, kwargs, sandbox))
        return sandbox

    monkeypatch.setattr("app.sandbox_service.Sandbox.create", create)
    service = SandboxService(ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True))
    coding = await service.create("coding")
    browser = await service.create("browser")
    waiting = asyncio.create_task(service.create("coding"))
    await asyncio.sleep(0)
    assert not waiting.done() and len(calls) == 2
    assert [item[0] for item in calls] == ["owner-agent/coding:dev", "owner-agent/browser:dev"]
    assert calls[0][1]["resource"] == {"cpu": "4", "memory": "5Gi"}
    assert calls[1][1]["resource"] == {"cpu": "2", "memory": "3Gi"}
    assert calls[0][1]["timeout"] == timedelta(minutes=60)
    assert calls[0][1]["network_policy"].default_action == "deny"
    assert "volumes" not in calls[0][1]
    await coding.destroy()
    replacement = await asyncio.wait_for(waiting, 1)
    await replacement.destroy()
    await browser.destroy()
    assert all(item[2].destroyed for item in calls)


@pytest.mark.asyncio
async def test_failed_destroy_keeps_reservation(monkeypatch):
    async def create(image, **kwargs):
        return FakeSandbox()

    monkeypatch.setattr("app.sandbox_service.Sandbox.create", create)
    service = SandboxService(ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True))
    lease = await service.create("coding")
    lease.sandbox.fail_destroy = True
    with pytest.raises(RuntimeError):
        await lease.destroy()
    waiting = asyncio.create_task(service.create("coding"))
    await asyncio.sleep(0)
    assert not waiting.done()
    lease.sandbox.fail_destroy = False
    await lease.destroy()
    next_lease = await asyncio.wait_for(waiting, 1)
    await next_lease.destroy()


@pytest.mark.asyncio
async def test_cancel_creation_cleans_up_before_releasing(monkeypatch):
    started, complete = asyncio.Event(), asyncio.Event()
    sandbox = FakeSandbox()

    async def create(image, **kwargs):
        started.set()
        await complete.wait()
        return sandbox

    monkeypatch.setattr("app.sandbox_service.Sandbox.create", create)
    service = SandboxService(ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True))
    task = asyncio.create_task(service.create("coding"))
    await started.wait()
    task.cancel()
    complete.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sandbox.destroyed and not service._reserved


@pytest.mark.asyncio
async def test_ambiguous_create_failure_keeps_slot(monkeypatch):
    async def create(image, **kwargs):
        raise RuntimeError("response lost")

    monkeypatch.setattr("app.sandbox_service.Sandbox.create", create)
    service = SandboxService(ConnectionConfig(domain="127.0.0.1:18180", api_key="test", use_server_proxy=True))
    with pytest.raises(RuntimeError):
        await service.create("coding")
    waiting = asyncio.create_task(service.create("coding"))
    await asyncio.sleep(0)
    assert not waiting.done()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
