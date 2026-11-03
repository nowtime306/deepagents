"""A lost dedicated lock connection permanently fences this service instance."""

import asyncio
import json
import logging
import math
import os
import platform
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import Literal
from uuid import uuid4

import anyio
from psycopg.types.json import Jsonb

from app.errors import fail

logger = logging.getLogger(__name__)
ServiceKind = Literal["api", "ask", "work", "ops"]


class ServiceFence:
    """Verify already-acquired session locks; never silently reconnect or reacquire them."""

    def __init__(self, database, connection, kind: ServiceKind, locks: list[tuple[int, bool]], *,
                 stop_file: Path | None = None) -> None:
        self.database, self.connection, self.kind, self.locks = database, connection, kind, locks
        self.stop_file = stop_file
        self.id, self.reason = uuid4(), None
        self.lost = asyncio.Event()
        self.mutex = asyncio.Lock()
        self.host_token = os.getenv("OWNER_AGENT_SERVICE_TOKEN")
        self.host_slot = os.getenv("OWNER_AGENT_SERVICE_SLOT", "0")

    def fence(self, reason: str) -> None:
        """A service can leave the fenced state only by creating a new instance."""
        if not self.lost.is_set():
            self.reason = reason
            self.lost.set()

    async def check(self) -> None:
        """Reject model, claim and HTTP boundaries even if the pool has recovered."""
        if self.lost.is_set():
            fail(503, "SERVICE_RESTARTING", "服务正在安全恢复，请稍后重试；旧操作不会自动重放")
        try:
            async with self.mutex:
                # ASGI disconnect uses repeated cancellation: let psycopg finish before unlocking.
                with anyio.CancelScope(shield=True):
                    async with asyncio.timeout(3):
                        if self.lost.is_set():
                            raise RuntimeError("Service is fenced")
                        for identifier, shared in self.locks:
                            row = await (await self.connection.execute(
                                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                                "AND locktype='advisory' "
                                "AND classid=0 AND objsubid=1 AND objid::bigint=%s AND mode=%s)",
                                (identifier, "ShareLock" if shared else "ExclusiveLock"),
                            )).fetchone()
                            if not row or not row[0]:
                                raise RuntimeError("Session lock lost")
        except Exception as error:
            self.fence(type(error).__name__)
            fail(503, "SERVICE_RESTARTING", "数据库会话已失效，服务正在恢复；旧操作不会自动重放")
        await anyio.lowlevel.checkpoint_if_cancelled()

    async def heartbeat(self) -> None:
        """Publish liveness on the same dedicated connection that owns the locks."""
        await self.check()
        try:
            async with self.mutex:
                with anyio.CancelScope(shield=True):
                    async with asyncio.timeout(3):
                        await self.connection.execute("INSERT INTO service_instances(id,kind,process_id,backend_id) "
                            "VALUES(%s,%s,%s,pg_backend_pid()) ON CONFLICT(id) DO UPDATE SET heartbeat_at=now()",
                            (self.id, self.kind, os.getpid()))
                        await self.connection.execute(
                            "INSERT INTO service_instance_metadata(instance_id,node,fault_domain,release,"
                            "required_locks) "
                            "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(instance_id) DO NOTHING",
                            (self.id, platform.node(), os.getenv("PLATFORM_FAULT_DOMAIN", platform.node())[:255],
                             os.getenv("PLATFORM_RELEASE", "0.1.0-dev")[:80], Jsonb(self.locks)))
        except Exception as error:
            self.fence(type(error).__name__)
            fail(503, "SERVICE_RESTARTING", "服务心跳中断，正在恢复")
        await anyio.lowlevel.checkpoint_if_cancelled()

    async def start(self) -> None:
        """Register before any work is admitted."""
        await self.heartbeat()
        await self.database.execute("INSERT INTO service_events(instance_id,kind,event) VALUES(%s,%s,'started')",
                                    (self.id, self.kind))

    async def monitor(self) -> None:
        """Signal permanent loss instead of allowing an unobserved background exception."""
        while not self.lost.is_set():
            if self.stop_file and await asyncio.to_thread(self.stop_file.exists):
                self.fence("SERVICE_STOP_REQUESTED")
                break
            if self.host_token:
                if not self.stop_file or self.host_slot not in {"0", "1", "2", "3"}:
                    self.fence("HOST_CONFIGURATION_INVALID")
                    break
                try:
                    file = self.stop_file.parent / f"{self.kind}-{self.host_slot}.json"
                    snapshot = json.loads(await asyncio.to_thread(file.read_text, encoding="utf-8"))
                    age = time.monotonic() - snapshot["heartbeat"]
                    if (snapshot["token"] != self.host_token or snapshot["state"] not in {"starting", "running"}
                        or not math.isfinite(age) or not 0 <= age < 10):
                        raise ValueError("Host heartbeat unavailable")
                except (OSError, ValueError, KeyError, TypeError):
                    self.fence("HOST_HEARTBEAT_LOST")
                    break
            try:
                await self.heartbeat()
            except Exception:
                break
            try:
                await asyncio.wait_for(self.lost.wait(), timeout=1)
            except TimeoutError:
                pass

    async def close(self) -> None:
        """Clean up via the pool when possible; stale heartbeats remain conservative otherwise."""
        try:
            async with asyncio.timeout(3):
                await self.database.execute("DELETE FROM service_instances WHERE id=%s", (self.id,))
                await self.database.execute("INSERT INTO service_events(instance_id,kind,event,reason) "
                    "VALUES(%s,%s,%s,%s)",
                    (self.id, self.kind, "fenced" if self.lost.is_set() else "stopped", self.reason))
        except Exception as error:
            logger.error("Service registration cleanup deferred (%s)", type(error).__name__)
        if getattr(self.database, "service_fence", None) is self:
            self.database.service_fence = None


async def run_fenced(fence: ServiceFence, work: Awaitable[None]) -> None:
    """Cancel the whole worker host on fence loss, preserving ordinary unknown-operation recovery."""
    job, monitor = asyncio.create_task(work), asyncio.create_task(fence.monitor())
    try:
        done, _ = await asyncio.wait({job, monitor}, return_when=asyncio.FIRST_COMPLETED)
        if job in done:
            await job
        else:
            fail(503, "SERVICE_RESTARTING", "服务会话失效，停止旧后台并等待安全重启")
    finally:
        job.cancel()
        monitor.cancel()
        await asyncio.gather(job, monitor, return_exceptions=True)
