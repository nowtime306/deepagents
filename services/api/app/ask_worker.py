"""Independent consumer with per-conversation connection-bound checkpoint ownership."""

import asyncio
import logging
from uuid import UUID, uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.ask_queue import AskQueue, conversation_lock
from app.ask_resources import AskKnowledge
from app.capabilities import CapabilityService
from app.errors import APIError, fail
from app.graphs import resolve_model
from app.mcp_service import MCPService
from app.runtime import Runtime

logger = logging.getLogger(__name__)


class AskWorker:
    def __init__(
        self, database, models, settings, model_factory=resolve_model, *, mcp_client_factory=None
    ) -> None:
        from app.execution_policy import bind

        bind(database, settings)
        self.database, self.models, self.settings = database, models, settings
        self.model_factory = model_factory
        self.mcp_client_factory = mcp_client_factory
        self.queue, self.id = AskQueue(database), uuid4()

    def create_mcp(self):
        service = MCPService(self.database, self.models.vault)
        if self.mcp_client_factory is not None:
            service.http_client_factory = self.mcp_client_factory
        return service

    async def heartbeat(self) -> None:
        await self.database.execute(
            "INSERT INTO ask_worker_instances(id) VALUES(%s) ON CONFLICT(id) DO UPDATE SET heartbeat_at=now()",
            (self.id,),
        )

    async def monitor(self, run: dict, task: asyncio.Task) -> None:
        elapsed = 0
        while not task.done():
            await asyncio.sleep(0.25)
            rows = await self.database.fetch(
                "SELECT cancel_requested FROM task_runs WHERE id=%s AND status='RUNNING' AND lease_token=%s "
                "AND lease_until>now()",
                (run["id"], run["lease_token"]),
            )
            if not rows or rows[0]["cancel_requested"]:
                task.cancel()
                return
            elapsed += 1
            if elapsed % 40 == 0:
                await self.heartbeat()
                if not await self.queue.renew(run["id"], run["lease_token"]):
                    task.cancel()
                    return

    async def execute(self, run: dict, connection) -> None:
        from app.resource_runtime import resource_run

        owner = (await self.database.fetch(
            "SELECT owner_id FROM conversations WHERE id=%s", (run["conversation_id"],)
        ))[0]["owner_id"]
        async with resource_run(self.database, owner, "ask", run["id"]):
            runtime = Runtime(
                self.database,
                AsyncPostgresSaver(connection),
                self.settings,
                self.model_factory,
                lease_token=run["lease_token"],
                lease_connection=connection,
                model_registry=self.models,
            )
            runtime.capabilities = CapabilityService(
                self.database,
                AskKnowledge(self.database, run, self.settings.web_origin, self.settings.internal_api_url),
                self.create_mcp(),
                model_registry=self.models,
            )
            try:
                from app.resource_route_start import at_start

                refreshed = await at_start(self.models, owner, run["id"], "ask", run["agent_snapshot"],
                                           images=any(item.get("media_type", "").startswith("image/")
                                                      for item in run.get("attachment_snapshot") or []),
                                           tools=bool(run["agent_snapshot"].get("mcp_ids")
                                                      or run["agent_snapshot"].get("skill_ids")
                                                      or run["agent_snapshot"].get("knowledge_ids")
                                                      or run["agent_snapshot"].get("team")
                                                      or run["agent_snapshot"].get("memory_enabled")))
                model = refreshed or await self.models.get(run["model_id"])
                if run["agent_snapshot"].get("route_selection_id"):
                    model = model.model_copy(
                        update={"route_selection_id": UUID(run["agent_snapshot"]["route_selection_id"])}
                    )
                if run["agent_snapshot"].get("internal_purpose") == "memory":
                    model = model.model_copy(update={"max_output_tokens": min(model.max_output_tokens or 4096, 4096)})
                if run["agent_snapshot"].get("internal_purpose") == "ocr":
                    model = model.model_copy(update={"max_output_tokens": min(model.max_output_tokens or 8192, 8192)})
                messages = await self.database.fetch(
                    "SELECT content->>'text' AS text FROM conversation_messages WHERE run_id=%s AND role='user' "
                    "ORDER BY created_at,id LIMIT 1",
                    (run["id"],),
                )
                if not messages:
                    fail(409, "ASK_INPUT_MISSING", "对话输入缺失，未请求模型")

                def guarded_model(configuration):
                    resolved = self.model_factory(configuration)
                    if hasattr(resolved, "max_retries"):
                        resolved.max_retries = 0
                    return resolved

                runtime.model_factory = guarded_model
                task = asyncio.create_task(runtime.run(run["id"], run["conversation_id"], messages[0]["text"], model))
                monitor = asyncio.create_task(self.monitor(run, task))
                try:
                    await task
                finally:
                    monitor.cancel()
                    await asyncio.gather(monitor, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                try:
                    await self.database.event(
                        run["id"],
                        "run.failed",
                        {"message": "对话后台执行失败，请检查配置或重新提问"},
                        status="FAILED",
                        error=error.code if isinstance(error, APIError) else "ASK_WORKER_FAILED",
                        lease_token=run["lease_token"],
                    )
                except APIError as failure:
                    if failure.code != "ASK_LEASE_LOST":
                        raise

    async def tick(self) -> bool:
        await self.heartbeat()
        await self.queue.reconcile()
        for candidate in await self.queue.candidates():
            connection = await AsyncConnection.connect(
                self.settings.database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
            )
            try:
                lock = await connection.execute(
                    "SELECT pg_try_advisory_lock(710020,%s) AS allowed",
                    (conversation_lock(candidate["conversation_id"]),),
                )
                if not (await lock.fetchone())["allowed"]:
                    continue
                run = await self.queue.claim(candidate["id"], self.id)
                if not run:
                    continue
                owner = await self.database.fetch(
                    "SELECT owner_id FROM conversations WHERE id=%s", (run["conversation_id"],)
                )
                run["owner_id"] = owner[0]["owner_id"]
                await self.execute(run, connection)
                return True
            finally:
                await connection.close()
        return False

    async def serve(self, stop: asyncio.Event) -> None:
        async def consume():
            while not stop.is_set():
                try:
                    executed = await self.tick()
                except Exception as error:
                    logger.error("Ask worker polling failed (%s)", type(error).__name__)
                    executed = False
                if not executed:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.5)
                    except TimeoutError:
                        pass

        try:
            # Each claim owns a dedicated conversation/checkpoint connection.
            await asyncio.gather(consume(), consume())
        finally:
            await self.database.execute("DELETE FROM ask_worker_instances WHERE id=%s", (self.id,))
