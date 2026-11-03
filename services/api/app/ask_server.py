"""Independent production Ask host sharing the existing maintenance exclusion lock."""

import asyncio
import logging
import sys

from psycopg import AsyncConnection

from app.ask_worker import AskWorker
from app.config import Settings
from app.models import CredentialVault, ModelRegistry
from app.service_fence import ServiceFence, run_fenced
from app.storage import WORK_MAINTENANCE_LOCK, Database


async def main() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_url)
    try:
        await database.pool.open(wait=True)
        async with await AsyncConnection.connect(settings.database_url, autocommit=True) as connection:
            lock = await connection.execute("SELECT pg_try_advisory_lock_shared(%s)", (WORK_MAINTENANCE_LOCK,))
            if not (await lock.fetchone())[0]:
                raise RuntimeError("平台正在备份维护，暂不启动对话后台")
            models = ModelRegistry(database, CredentialVault(settings.data_dir / "model.key", create=False))
            fence = ServiceFence(database, connection, "ask", [(WORK_MAINTENANCE_LOCK, True)],
                                 stop_file=settings.data_dir / "services/ask.stop")
            await fence.start()
            database.service_fence = fence
            try:
                await run_fenced(fence, AskWorker(database, models, settings).serve(asyncio.Event()))
            finally:
                await fence.close()
    finally:
        await database.pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    ) as runner:
        runner.run(main())
