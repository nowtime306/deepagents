"""Target-lifetime sandbox creation bound, independent of individual run retries."""

import asyncio
import json
import os
from pathlib import Path

from app.durable_sandboxes import DurableSandboxes
from opensandbox.models.sandboxes import SandboxFilter


class BoundedSandboxes(DurableSandboxes):
    maximum = 2

    def __init__(self, database, config, state_path: Path):
        super().__init__(database, config)
        self.state_path = state_path
        self.creation_lock = asyncio.Lock()
        self.attempts = 0
        self.storage_failed = False
        if state_path.exists():
            if state_path.is_symlink() or state_path.stat().st_size > 1024:
                raise ValueError("Invalid evaluation sandbox bound state")
            value = json.loads(state_path.read_text(encoding="utf-8"))
            count = value.get("attempts")
            if type(count) is not int or not 0 <= count <= self.maximum:
                raise ValueError("Invalid evaluation sandbox attempt count")
            self.attempts = count

    def save_bound(self):
        temporary = self.state_path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"attempts": self.attempts}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except OSError:
            self.storage_failed = True
            raise

    async def reserve(self, run_id, token, kind):
        if kind != "coding":
            raise ValueError("The controlled evaluation target does not allow browser tasks")
        async with self.creation_lock:
            if self.storage_failed or self.attempts >= self.maximum:
                raise RuntimeError("Evaluation target sandbox creation budget exhausted")
            self.attempts += 1
            self.save_bound()  # Crash/unknown after this point consumes the target reservation.
            result = await super().reserve(run_id, token, kind)
            if result is None:  # A known local admission refusal made no remote create request.
                self.attempts -= 1
                self.save_bound()
            return result

    async def create(self, run_id, token, kind):
        if kind != "coding":
            raise ValueError("Only fixed Coding profiles may create evaluation sandboxes")
        return await super().create(run_id, token, kind)

    def capability(self):
        return {"enforcement": "controlled-sandbox-reserve-v1", "scope": "test-environment",
                "max_created": self.maximum, "attempts": self.attempts,
                "storage_healthy": not self.storage_failed}

    async def readiness(self):
        namespace = str((await self.database.fetch("SELECT id FROM work_sandbox_namespace"))[0]["id"])
        manager = await self._get_manager()
        page = await asyncio.wait_for(manager.list_sandbox_infos(SandboxFilter(
            metadata={"purpose": "owner-agent-work", "namespace": namespace}, page=1, page_size=1)), timeout=5)
        if any((item.metadata or {}).get("namespace") != namespace for item in page.sandbox_infos):
            raise ValueError("Manager did not honor the evaluation namespace filter")
        return {"ready": True, "scope": "registered-namespace", "image_execution_verified": False}
