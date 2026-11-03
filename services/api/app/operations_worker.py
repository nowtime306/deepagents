"""Durable offline backup coordinator; busy tasks defer maintenance without cancellation."""

import asyncio
import logging
import re
import shutil
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.backup import create, file_digest, restore_check, verify_files
from app.config import Settings
from app.maintenance import GATE, idle
from app.managed_services import ManagedServices, ServiceControlProfileError
from app.service_fence import ServiceFence, run_fenced
from app.storage import Database

logger = logging.getLogger(__name__)


def next_due(hour, minute, now=None):
    current = (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
    value = datetime.combine(current.date(), time(hour, minute), tzinfo=current.tzinfo)
    return (value if value > current else value + timedelta(days=1)).astimezone(UTC)


def remove_registered(root: Path, name: str, digest: str):
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", name):
        raise ValueError("BACKUP_NAME_UNSAFE")
    target = root / name
    if (
        root.is_symlink()
        or root.is_junction()
        or target.is_symlink()
        or target.is_junction()
        or target.resolve() != root.resolve() / name
        or not target.is_dir()
        or any(file.is_symlink() or file.is_junction() for file in target.rglob("*"))
    ):
        raise ValueError("BACKUP_PATH_UNSAFE")
    if file_digest(target / "manifest.json") != digest:
        raise ValueError("BACKUP_MANIFEST_CHANGED")
    manifest = verify_files(target)
    actual = {file.relative_to(target).as_posix() for file in target.rglob("*") if file.is_file()}
    if actual != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("BACKUP_ADDITIONAL_FILES")
    shutil.rmtree(target)  # Only the resolved, registered and hash-verified backup above is eligible.


class OperationsWorker:
    def __init__(self, database, settings, *, fence=None, services=None, creator=None, verifier=None):
        self.db, self.settings, self.fence = database, settings, fence
        self.services = services or ManagedServices(settings, database)
        self.creator, self.verifier = creator or create, verifier or restore_check

    async def owned(self, job):
        if self.fence:
            await self.fence.check()
        rows = await self.db.fetch(
            "SELECT 1 FROM backup_jobs WHERE id=%s AND lease_token=%s AND status='RUNNING'",
            (job["id"], job["lease_token"]),
        )
        if not rows:
            raise RuntimeError("BACKUP_LEASE_LOST")

    async def phase(self, job, phase):
        await self.owned(job)
        await self.db.execute(
            "UPDATE backup_jobs SET phase=%s WHERE id=%s AND lease_token=%s", (phase, job["id"], job["lease_token"])
        )

    async def schedule(self):
        async with self.db.pool.connection() as connection:
            row = await (
                await connection.execute("SELECT * FROM backup_settings WHERE singleton FOR UPDATE")
            ).fetchone()
            if not row["enabled"] or not row["next_due_at"] or row["next_due_at"] > datetime.now(UTC):
                return
            if await (
                await connection.execute("SELECT 1 FROM backup_jobs WHERE status IN ('PENDING','RUNNING')")
            ).fetchone():
                return
            await connection.execute(
                "INSERT INTO backup_jobs(id,dedupe_key) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (uuid4(), row["next_due_at"].isoformat()),
            )
            await connection.execute(
                "UPDATE backup_settings SET next_due_at=%s WHERE singleton", (next_due(row["hour"], row["minute"]),)
            )

    async def claim(self):
        async with self.db.pool.connection() as connection:
            if not (await (await connection.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (GATE,))).fetchone())[
                "ok"
            ]:
                return None
            row = await (
                await connection.execute(
                    "SELECT * FROM backup_jobs WHERE status='PENDING' AND available_at<=now() "
                    "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
            ).fetchone()
            if not row:
                return None
            if not await idle(connection):
                await connection.execute(
                    "UPDATE backup_jobs SET phase='DEFERRED',reason='ACTIVE_TASKS',"
                    "available_at=now()+interval '5 minutes' WHERE id=%s",
                    (row["id"],),
                )
                return None
            try:
                services = await self.services.snapshot()
            except ServiceControlProfileError:
                await connection.execute(
                    "UPDATE backup_jobs SET status='FAILED',phase='ADMISSION',"
                    "reason='SERVICE_CONTROL_PROFILE_MISMATCH',finished_at=now() WHERE id=%s",
                    (row["id"],),
                )
                return None
            online = await (
                await connection.execute(
                    "SELECT DISTINCT kind FROM service_instances WHERE kind<>'ops' "
                    "AND heartbeat_at>now()-interval '15 seconds'"
                )
            ).fetchall()
            if any(item["kind"] not in {service["kind"] for service in services} for item in online):
                await connection.execute(
                    "UPDATE backup_jobs SET status='FAILED',phase='ADMISSION',reason='UNMANAGED_SERVICE',"
                    "finished_at=now() WHERE id=%s",
                    (row["id"],),
                )
                return None
            token = uuid4()
            services = [{**service, "stop_marker": "backup:" + str(token)} for service in services]
            await connection.execute(
                "UPDATE backup_jobs SET status='RUNNING',phase='STOPPING',lease_token=%s,"
                "attempt=attempt+1,resume_services=%s WHERE id=%s",
                (token, Jsonb(services), row["id"]),
            )
            await connection.execute(
                "UPDATE platform_maintenance SET active=true,job_id=%s,lease_token=%s,started_at=now() WHERE singleton",
                (row["id"], token),
            )
            return {**row, "lease_token": token, "resume_services": services}

    async def finish(self, job, success, reason=None):
        await self.owned(job)
        await self.phase(job, "RESTARTING")
        restored = await self.services.start(job["resume_services"])
        if restored is False:
            reason = "MANUAL_STOP_PRESERVED"
        await self.owned(job)
        async with self.db.pool.connection() as connection:
            await connection.execute(
                "UPDATE platform_maintenance SET active=false,job_id=NULL,lease_token=NULL "
                "WHERE singleton AND job_id=%s AND lease_token=%s",
                (job["id"], job["lease_token"]),
            )
            await connection.execute(
                "UPDATE backup_jobs SET status=%s,phase='FINISHED',reason=%s,finished_at=now() "
                "WHERE id=%s AND lease_token=%s",
                ("SUCCEEDED" if success else "FAILED", reason, job["id"], job["lease_token"]),
            )

    async def perform(self, job):
        succeeded, reason = False, None
        try:
            await self.owned(job)
            await self.services.stop(job["resume_services"])
            await self.phase(job, "COPYING")
            target = await self.creator(self.settings)
            await self.owned(job)
            await self.db.execute(
                "UPDATE backup_jobs SET backup_name=%s WHERE id=%s AND lease_token=%s",
                (target.name, job["id"], job["lease_token"]),
            )
            await self.phase(job, "VERIFYING")
            result = await self.verifier(self.settings, target)
            await self.owned(job)
            await self.db.execute(
                "UPDATE backup_jobs SET verification=%s,manifest_digest=%s WHERE id=%s AND lease_token=%s",
                (Jsonb(result), file_digest(target / "manifest.json"), job["id"], job["lease_token"]),
            )
            succeeded = True
        except asyncio.CancelledError:
            raise  # New fenced host reconciles this durable RUNNING record; never replays a partial copy.
        except Exception as error:
            reason = str(error) if re.fullmatch(r"[A-Z_]{3,80}", str(error)) else type(error).__name__
        await self.finish(job, succeeded, reason)
        if succeeded:
            await self.rotate()

    async def recover(self):
        rows = await self.db.fetch("SELECT * FROM backup_jobs WHERE status='RUNNING'")
        for row in rows:
            token = uuid4()
            await self.db.execute(
                "UPDATE backup_jobs SET lease_token=%s WHERE id=%s AND status='RUNNING'", (token, row["id"])
            )
            await self.db.execute("UPDATE platform_maintenance SET lease_token=%s WHERE job_id=%s", (token, row["id"]))
            await self.finish({**row, "lease_token": token}, False, "BACKUP_INTERRUPTED_REVIEW")

    async def rotate(self):
        settings = (await self.db.fetch("SELECT retain FROM backup_settings WHERE singleton"))[0]
        rows = await self.db.fetch(
            "SELECT id,backup_name,manifest_digest FROM backup_jobs WHERE status='SUCCEEDED' "
            "ORDER BY finished_at DESC,id DESC"
        )
        for row in rows[max(2, settings["retain"]) :]:
            if self.fence:
                await self.fence.check()
            try:
                await asyncio.to_thread(
                    remove_registered, self.settings.data_dir / "backups", row["backup_name"], row["manifest_digest"]
                )
                await self.db.execute(
                    "UPDATE backup_jobs SET status='DELETED',deleted_at=now() WHERE id=%s AND status='SUCCEEDED'",
                    (row["id"],),
                )
            except (OSError, ValueError):
                await self.db.execute("UPDATE backup_jobs SET reason='ROTATION_REVIEW' WHERE id=%s", (row["id"],))

    async def serve(self):
        await self.recover()
        while True:
            if self.fence:
                await self.fence.check()
            from app.monitoring import safe_tick

            await safe_tick(self.db, self.settings)
            await self.schedule()
            job = await self.claim()
            if job:
                await self.perform(job)
            await asyncio.sleep(2)


async def main():
    settings = Settings.from_env()
    database = Database(settings.database_url)
    try:
        await database.pool.open(wait=True)
        async with await AsyncConnection.connect(settings.database_url, autocommit=True) as connection:
            if not (await (await connection.execute("SELECT pg_try_advisory_lock(710033)")).fetchone())[0]:
                raise RuntimeError("运维后台已运行")
            fence = ServiceFence(
                database, connection, "ops", [(710033, False)], stop_file=settings.data_dir / "services/ops.stop"
            )
            await fence.start()
            try:
                await run_fenced(fence, OperationsWorker(database, settings, fence=fence).serve())
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
