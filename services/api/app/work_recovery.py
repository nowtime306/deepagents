"""User-requested rescue exports stop old task processes and never resume agent effects."""

import asyncio
from datetime import timedelta
from uuid import uuid4

from opensandbox.models.execd import RunCommandOpts
from opensandbox.sandbox import Sandbox

from app.work_backend import WorkBackend

STOP_TASK_PROCESSES = """import os,signal,time
if os.getuid()!=0:raise RuntimeError('Root cleanup required')
for _ in range(3):
 for name in os.listdir('/proc'):
  if not name.isdigit():continue
  try:
   with open('/proc/'+name+'/status') as file:
    uid=next(line for line in file if line.startswith('Uid:')).split()[1]
   if uid=='1000':os.kill(int(name),signal.SIGKILL)
  except (FileNotFoundError,ProcessLookupError,StopIteration):pass
 time.sleep(0.1)
print('task-processes-stopped')
"""


class WorkRecovery:
    def __init__(self, database, sandboxes, artifacts, *, vault=None) -> None:
        self.database, self.sandboxes, self.artifacts = database, sandboxes, artifacts
        self.vault = vault

    async def guard(self, job: dict) -> None:
        rows = await self.database.fetch(
            "UPDATE work_recovery_jobs j SET lease_until=now()+interval '120 seconds' "
            "FROM work_queue_runs r WHERE j.run_id=r.id AND j.run_id=%s AND j.token=%s "
            "AND j.status='RUNNING' AND j.lease_until>now() "
            "AND r.status IN ('NEEDS_REVIEW','CANCELLED','FAILED','COMPLETED') "
            "AND NOT EXISTS(SELECT 1 FROM work_browser_controls c WHERE c.run_id=r.id AND c.status<>'CLOSED') "
            "RETURNING j.run_id",
            (job["run_id"], job["token"]),
        )
        if not rows:
            raise RuntimeError("Recovery lease lost")

    async def tick(self) -> bool:
        await self.database.execute(
            "UPDATE work_recovery_jobs SET status='FAILED',error_code='RECOVERY_INTERRUPTED',updated_at=now() "
            "WHERE status='RUNNING' AND lease_until<=now()"
        )
        async with self.database.pool.connection() as connection:
            job = await (
                await connection.execute(
                    "SELECT j.*,r.id,r.status AS run_status FROM work_recovery_jobs j "
                    "JOIN work_queue_runs r ON r.id=j.run_id "
                    "WHERE j.status='PENDING' AND j.not_before<=now() "
                    "AND r.status IN ('NEEDS_REVIEW','CANCELLED','FAILED','COMPLETED') "
                    "ORDER BY j.updated_at FOR UPDATE OF j,r SKIP LOCKED LIMIT 1"
                )
            ).fetchone()
            if not job:
                return False
            if await (
                await connection.execute(
                    "SELECT 1 FROM work_browser_controls WHERE run_id=%s AND status<>'CLOSED' LIMIT 1",
                    (job["run_id"],),
                )
            ).fetchone():
                return False
            job["token"] = uuid4()
            await connection.execute(
                "UPDATE work_recovery_jobs SET status='RUNNING',token=%s,lease_until=now()+interval '120 seconds' "
                "WHERE run_id=%s",
                (job["token"], job["run_id"]),
            )
        try:
            async with asyncio.timeout(180):
                snapshot = await self.export(job)
            await self.database.execute(
                "UPDATE work_recovery_jobs SET status='COMPLETED',snapshot_id=%s,error_code=NULL,updated_at=now() "
                "WHERE run_id=%s AND token=%s AND status='RUNNING'",
                (snapshot, job["run_id"], job["token"]),
            )
        except BaseException as error:
            await self.database.execute(
                "UPDATE work_recovery_jobs SET status='FAILED',error_code='RECOVERY_FAILED',updated_at=now() "
                "WHERE run_id=%s AND token=%s AND status='RUNNING'",
                (job["run_id"], job["token"]),
            )
            if not isinstance(error, Exception):
                raise
        return True

    async def export(self, job: dict):
        await self.guard(job)
        sessions = await self.database.fetch(
            "SELECT * FROM work_sandbox_sessions WHERE run_id=%s AND owner_id=%s "
            "AND retention_required AND sandbox_id IS NOT NULL AND state<>'RELEASED'",
            (job["run_id"], job["owner_id"]),
        )
        if len(sessions) != 1:
            raise RuntimeError("Exactly one retained workspace is required")
        session = sessions[0]
        namespace = str((await self.database.fetch("SELECT id FROM work_sandbox_namespace"))[0]["id"])
        manager = await self.sandboxes._get_manager()
        info = await manager.get_sandbox_info(session["sandbox_id"])
        metadata = info.metadata or {}
        expected = {"purpose": "owner-agent-work", "namespace": namespace, "session": str(session["id"])}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise PermissionError("Workspace ownership mismatch")
        sandbox = await Sandbox.connect(session["sandbox_id"], connection_config=self.sandboxes.config)
        try:
            import shlex

            stopped = await sandbox.commands.run(
                shlex.join(["python3", "-c", STOP_TASK_PROCESSES]), opts=RunCommandOpts(timeout=timedelta(seconds=10))
            )
            if stopped.exit_code != 0 or stopped.error:
                raise RuntimeError("Task processes could not be stopped")
            await sandbox.renew(timedelta(minutes=15))
            backend = WorkBackend(sandbox, lambda: self.guard(job))
            run = {"id": job["run_id"], "owner_id": job["owner_id"]}
            transport = await self.database.fetch(
                "SELECT * FROM work_browser_transports WHERE session_id=%s AND status<>'CLOSED'",
                (session["id"],),
            )
            browser = None
            if transport:
                from app.browser_bridge import BrowserBridge

                if self.vault is None or not transport[0]["token_ciphertext"]:
                    raise RuntimeError("Private browser state requires a verified transport before cleanup")
                run = (await self.database.fetch("SELECT * FROM work_queue_runs WHERE id=%s", (job["run_id"],)))[0]
                browser = BrowserBridge(self.database, self.vault, backend, run, session)
                browser.transport.key = self.vault.decrypt(transport[0]["token_ciphertext"]).get_secret_value()
                browser.started, browser.revision = True, transport[0]["state_revision"]
                await browser.save()
            snapshot = await self.artifacts.capture(run, backend)
            await self.guard(job)
            await self.database.execute(
                "UPDATE work_sandbox_sessions SET retention_required=false WHERE id=%s", (session["id"],)
            )
            await self.sandboxes.release(session["id"])
            if browser:
                await browser.closed()
            return snapshot
        finally:
            await sandbox.close()
