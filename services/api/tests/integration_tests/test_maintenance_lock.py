"""A running worker blocks backups without blocking ordinary model configuration writes."""

import asyncio
from uuid import uuid4

from test_api import client as client

from app.models import ModelInput
from app.storage import WORK_MAINTENANCE_LOCK


def test_worker_maintenance_lock_does_not_block_model_saves(client):
    connection, app = client

    async def exercise():
        async with app.state.db.pool.connection() as worker:
            await worker.execute("SELECT pg_advisory_lock_shared(%s)", (WORK_MAINTENANCE_LOCK,))
            identifier = None
            try:
                identifier = await asyncio.wait_for(
                    app.state.models.save(
                        ModelInput(name="锁回归-" + uuid4().hex, model="fixture", base_url="http://127.0.0.1:8793/v1")
                    ),
                    3,
                )
                async with app.state.db.pool.connection() as backup:
                    permitted = await (
                        await backup.execute("SELECT pg_try_advisory_lock(%s) AS allowed", (WORK_MAINTENANCE_LOCK,))
                    ).fetchone()
                    assert permitted["allowed"] is False
            finally:
                await worker.execute("SELECT pg_advisory_unlock_shared(%s)", (WORK_MAINTENANCE_LOCK,))
                if identifier:
                    await app.state.db.execute("DELETE FROM model_configs WHERE id=%s", (identifier,))

    connection.portal.call(exercise)
