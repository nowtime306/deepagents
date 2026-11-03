"""A dependency swallowing cancellation must not leave the Work reaper alive."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.work_server import reconcile


@pytest.mark.parametrize("stage", ["inventory", "recovery"])
@pytest.mark.parametrize("result", ["return", "error"])
def test_reaper_honors_pending_cancellation_after_dependency_swallows_it(stage, result):
    async def exercise():
        entered = asyncio.Event()

        async def swallowing_dependency():
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                if result == "error":
                    raise RuntimeError("Simulated interrupted database session") from None

        service = SimpleNamespace(reconcile=AsyncMock())
        recovery = SimpleNamespace(tick=AsyncMock())
        if stage == "inventory":
            service.reconcile.side_effect = swallowing_dependency
        else:
            recovery.tick.side_effect = swallowing_dependency
        task = asyncio.create_task(reconcile(service, recovery))
        await entered.wait()
        task.cancel()
        # wait_for would issue a second cancel and hide the original regression.
        done, _ = await asyncio.wait({task}, timeout=0.2)
        try:
            assert task in done, "One cancellation must stop the reaper without another inventory/sleep cycle"
            assert task.cancelled()
            assert service.reconcile.await_count == 1
            assert recovery.tick.await_count == (0 if stage == "inventory" else 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())
