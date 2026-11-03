"""Internal OpenSandbox lifecycle; not exposed to users or agents yet."""

import asyncio
from datetime import timedelta
from typing import Literal

from opensandbox.config import ConnectionConfig
from opensandbox.models.sandboxes import NetworkPolicy
from opensandbox.sandbox import Sandbox

SandboxKind = Literal["coding", "browser"]
IMAGES = {"coding": "owner-agent/coding:dev", "browser": "owner-agent/browser:dev"}
RESOURCES = {"coding": {"cpu": "4", "memory": "5Gi"}, "browser": {"cpu": "2", "memory": "3Gi"}}


class SandboxLease:
    """Keep a reservation until the remote sandbox is confirmed destroyed."""

    def __init__(self, sandbox: Sandbox, kind: SandboxKind, service: "SandboxService") -> None:
        self.sandbox, self.kind, self.service = sandbox, kind, service
        self._destroyed = False
        self._lock = asyncio.Lock()

    async def destroy(self) -> None:
        async with self._lock:
            if self._destroyed:
                return
            await self.sandbox.destroy()
            self._destroyed = True
            await self.service._release(self.kind)


class SandboxService:
    """Queue one sandbox of each type within this service instance."""

    def __init__(self, config: ConnectionConfig) -> None:
        if not config.api_key or not config.use_server_proxy:
            raise ValueError("Sandbox management requires an API key and server proxy")
        self.config = config
        self._condition = asyncio.Condition()
        self._reserved: set[SandboxKind] = set()

    async def _release(self, kind: SandboxKind) -> None:
        async with self._condition:
            self._reserved.remove(kind)
            self._condition.notify_all()

    async def create(self, kind: SandboxKind) -> SandboxLease:
        if kind not in IMAGES:
            raise ValueError("Unsupported sandbox type")
        async with self._condition:
            await self._condition.wait_for(lambda: kind not in self._reserved and len(self._reserved) < 2)
            self._reserved.add(kind)
        task = asyncio.create_task(
            Sandbox.create(
                IMAGES[kind],
                connection_config=self.config,
                timeout=timedelta(minutes=60),
                ready_timeout=timedelta(seconds=90),
                resource=RESOURCES[kind],
                network_policy=NetworkPolicy(defaultAction="deny"),
                metadata={"purpose": "owner-agent-internal", "environment": kind},
            )
        )
        try:
            sandbox = await asyncio.shield(task)
        except asyncio.CancelledError:
            # A creation request may already have reached the server; wait for its result
            # and destroy it before returning the slot to avoid silently orphaning it.
            try:
                sandbox = await task
            except Exception:
                # A transport failure can leave a remotely created sandbox whose ID
                # was never received. Keep the slot reserved until reconciliation.
                pass
            else:
                await sandbox.destroy()
                await self._release(kind)
            raise
        except Exception:
            # The server may have created a sandbox before the response was lost.
            # Do not admit another of the same kind without reconciliation.
            raise
        return SandboxLease(sandbox, kind, self)
