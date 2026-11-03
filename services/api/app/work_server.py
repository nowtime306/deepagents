"""Explicit independent Work worker entry point for local deployment."""

import asyncio
import logging
import sys
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opensandbox.config import ConnectionConfig
from psycopg import AsyncConnection

from app.capabilities import CapabilityService
from app.config import Settings
from app.durable_sandboxes import DurableSandboxes
from app.graphs import resolve_model
from app.mcp_service import MCPService
from app.models import CredentialVault, ModelRegistry
from app.service_fence import ServiceFence, run_fenced
from app.storage import WORK_MAINTENANCE_LOCK, Database
from app.work_artifacts import WorkArtifacts
from app.work_engine import WorkEngine
from app.work_recovery import WorkRecovery
from app.work_resources import WorkKnowledge

logger = logging.getLogger(__name__)


async def reconcile(service: DurableSandboxes, recovery: WorkRecovery) -> None:
    task = asyncio.current_task()
    while True:
        try:
            await service.reconcile()
            if task.cancelling():
                raise asyncio.CancelledError
            await recovery.tick()
        except Exception as error:
            if task.cancelling():
                raise asyncio.CancelledError from None
            logger.error("Sandbox reconciliation failed (%s)", type(error).__name__)
        if task.cancelling():
            raise asyncio.CancelledError
        await asyncio.sleep(30)


async def main() -> None:
    settings = Settings.from_env()
    if not settings.work_enabled:
        raise RuntimeError("任务执行尚未启用")
    database = Database(settings.database_url)
    key_path = Path(__file__).parents[2] / "sandbox" / ".local" / "manager.key"
    config = ConnectionConfig(domain="127.0.0.1:18180", api_key=key_path.read_text().strip(), use_server_proxy=True)
    sandboxes = DurableSandboxes(database, config)
    reaper = None
    try:
        await database.pool.open(wait=True)
        async with (
            await AsyncConnection.connect(settings.database_url, autocommit=True) as maintenance,
            AsyncPostgresSaver.from_conn_string(settings.database_url) as saver,
        ):
            lock = await maintenance.execute("SELECT pg_try_advisory_lock_shared(%s)", (WORK_MAINTENANCE_LOCK,))
            if not (await lock.fetchone())[0]:
                raise RuntimeError("平台正在备份维护，请稍后启动任务服务")
            models = ModelRegistry(database, CredentialVault(settings.data_dir / "model.key", create=False))
            mcp = MCPService(database, models.vault)
            from app.browser_skill_runtime import BrowserSkillRuntime

            engine = WorkEngine(
                database,
                models,
                sandboxes,
                WorkArtifacts(database, settings.data_dir / "work-artifacts"),
                saver,
                resolve_model,
                capabilities_factory=lambda run: CapabilityService(
                    database, WorkKnowledge(database, run, settings.web_origin, settings.internal_api_url), mcp,
                    model_registry=models
                ),
                internal_api_url=settings.internal_api_url,
                browser_skills=BrowserSkillRuntime(database, settings.data_dir),
            )
            await sandboxes.reconcile()
            recovery = WorkRecovery(database, sandboxes, engine.artifacts, vault=models.vault)
            reaper = asyncio.create_task(reconcile(sandboxes, recovery))
            fence = ServiceFence(database, maintenance, "work", [(WORK_MAINTENANCE_LOCK, True)],
                                 stop_file=settings.data_dir / "services/work.stop")
            await fence.start()
            database.service_fence = fence
            try:
                await run_fenced(fence, engine.events.serve(asyncio.Event()))
            finally:
                await fence.close()
    finally:
        if reaper:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
        await sandboxes.close()
        await database.pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    ) as runner:
        runner.run(main())
