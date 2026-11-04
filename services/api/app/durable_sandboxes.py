"""Task-bound OpenSandbox admission persisted across worker restarts."""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxApiException
from opensandbox.manager import SandboxManager
from opensandbox.models.sandboxes import SandboxFilter
from opensandbox.sandbox import Sandbox
from psycopg.errors import UniqueViolation

from app.host_resources import resource_budget
from app.sandbox_service import IMAGES, RESOURCES, SandboxKind
from app.storage import Database
from app.work_network import policy

EVALUATION_EFFECT_GUARD_VERSION = 'platform-effect-permit-v1'


class DurableSandboxes:
    """Reserve one durable slot per environment before sending remote creates."""

    def __init__(self, database: Database, config: ConnectionConfig) -> None:
        if not config.api_key or not config.use_server_proxy:
            raise ValueError("Sandbox management requires authentication and server proxy")
        self.database, self.config = database, config
        self.manager: SandboxManager | None = None
        self.manager_lock = asyncio.Lock()

    async def _get_manager(self) -> SandboxManager:
        async with self.manager_lock:
            if self.manager is None:
                self.manager = await SandboxManager.create(self.config)
            return self.manager

    async def reserve(self, run_id: UUID, token: UUID, kind: SandboxKind) -> dict | None:
        """Derive owner from an unexpired run lease, never from model parameters."""
        if kind not in IMAGES:
            raise ValueError("Unsupported sandbox type")
        budget = await resource_budget()
        needed = 5 * 1024**3 if kind == "coding" else 3 * 1024**3
        try:
            async with self.database.pool.connection() as connection:
                await connection.execute("SELECT pg_advisory_xact_lock(710016)")
                reserved = (
                    await (
                        await connection.execute(
                            "SELECT COALESCE(sum(CASE WHEN kind='coding' THEN 5368709120 ELSE 3221225472 END),0) "
                            "AS used "
                            "FROM work_sandbox_sessions WHERE state<>'RELEASED'"
                        )
                    ).fetchone()
                )["used"]
                media = await (await connection.execute(
                    "SELECT count(*)*536870912 AS used FROM knowledge_video_jobs WHERE holds_memory"
                )).fetchone()
                reserved += media["used"]
                if reserved + needed > budget:
                    return None
                rows = await (
                    await connection.execute(
                        "INSERT INTO work_sandbox_sessions(id,run_id,owner_id,kind,state) "
                        "SELECT %s,id,owner_id,%s,'CREATING' FROM work_queue_runs WHERE id=%s AND status='RUNNING' "
                        "AND lease_token=%s AND lease_until>now() AND NOT cancel_requested RETURNING *",
                        (uuid4(), kind, run_id, token),
                    )
                ).fetchall()
            return rows[0] if rows else None
        except UniqueViolation:
            return None

    async def create(self, run_id: UUID, token: UUID, kind: SandboxKind) -> tuple[dict, Sandbox] | None:
        """Keep ambiguous creations reserved until remote inventory is reconciled."""
        if kind == "browser":
            reused = await self.reuse_browser(run_id, token)
            if reused is not None:
                return reused
        rows = await self.database.fetch(
            "SELECT network_domains FROM work_queue_runs WHERE id=%s AND lease_token=%s AND status='RUNNING' "
            "AND lease_until>now()",
            (run_id, token),
        )
        if not rows:
            return None
        network = await policy(rows[0]["network_domains"])
        session = await self.reserve(run_id, token, kind)
        if session is None:
            return None
        namespace = (await self.database.fetch("SELECT id FROM work_sandbox_namespace"))[0]["id"]
        parent_guard = getattr(self.database, 'evaluation_effect_guard', None)
        if parent_guard is not None:
            try:
                await parent_guard('sandbox')
            except BaseException:
                # No SDK create was sent; this exact local reservation is known unused.
                await self.database.execute(
                    "UPDATE work_sandbox_sessions SET state='RELEASED',updated_at=now() "
                    "WHERE id=%s AND state='CREATING' AND sandbox_id IS NULL", (session["id"],))
                raise
        try:
            sandbox = await Sandbox.create(
                IMAGES[kind],
                connection_config=self.config,
                timeout=timedelta(minutes=60),
                ready_timeout=timedelta(seconds=90),
                resource=RESOURCES[kind],
                network_policy=network,
                metadata={"purpose": "owner-agent-work", "namespace": str(namespace), "session": str(session["id"])},
            )
        except (Exception, asyncio.CancelledError):
            await self.database.execute(
                "UPDATE work_sandbox_sessions SET state='UNKNOWN',updated_at=now() WHERE id=%s AND state='CREATING'",
                (session["id"],),
            )
            raise
        rows = await self.database.fetch(
            "UPDATE work_sandbox_sessions s SET sandbox_id=%s,state='READY',updated_at=now() "
            "WHERE s.id=%s AND s.state IN ('CREATING','UNKNOWN') AND EXISTS "
            "(SELECT 1 FROM work_queue_runs r WHERE r.id=s.run_id AND r.status='RUNNING' "
            "AND r.lease_token=%s AND r.lease_until>now()) RETURNING s.*",
            (sandbox.id, session["id"], token),
        )
        if rows:
            return rows[0], sandbox
        await sandbox.close()
        # Its metadata keeps the remotely created sandbox discoverable for cleanup.
        raise RuntimeError("Run lease expired during sandbox creation")

    async def reuse_browser(self, run_id: UUID, token: UUID) -> tuple[dict, Sandbox] | None:
        rows = await self.database.fetch(
            "UPDATE work_sandbox_sessions s SET browser_persistent=false,updated_at=now() "
            "FROM work_queue_runs r WHERE s.run_id=r.id AND r.id=%s AND r.status='RUNNING' "
            "AND r.lease_token=%s AND r.lease_until>now() AND NOT r.cancel_requested "
            "AND s.kind='browser' AND s.state='READY' AND s.browser_persistent "
            "AND NOT EXISTS(SELECT 1 FROM work_browser_controls c WHERE c.run_id=r.id AND c.status<>'CLOSED') "
            "RETURNING s.*",
            (run_id, token),
        )
        if not rows:
            return None
        session = rows[0]
        manager = await self._get_manager()
        try:
            await manager.get_sandbox_info(session["sandbox_id"])
        except SandboxApiException as error:
            if error.status_code != 404:
                raise
            await self.database.execute(
                "UPDATE work_sandbox_sessions SET state='RELEASED',retention_required=false,data_lost=false,"
                "updated_at=now() WHERE id=%s",
                (session["id"],),
            )
            await self.database.execute(
                "UPDATE work_browser_transports SET status='CLOSED',token_ciphertext='' WHERE session_id=%s",
                (session["id"],),
            )
            return None
        sandbox = await Sandbox.connect(session["sandbox_id"], connection_config=self.config)
        return {**session, "reused": True}, sandbox

    async def owned(self, run_id: UUID, token: UUID, session_id: UUID) -> dict | None:
        """Resolve a sandbox only through its run's current lease."""
        rows = await self.database.fetch(
            "SELECT s.* FROM work_sandbox_sessions s JOIN work_queue_runs r ON r.id=s.run_id "
            "WHERE s.id=%s AND r.id=%s AND r.lease_token=%s AND r.status='RUNNING' "
            "AND r.lease_until>now() AND s.state='READY'",
            (session_id, run_id, token),
        )
        return rows[0] if rows else None

    async def release(self, session_id: UUID) -> bool:
        """Retain capacity until the manager confirms the sandbox is absent."""
        rows = await self.database.fetch(
            "UPDATE work_sandbox_sessions SET state='CLOSING',updated_at=now() "
            "WHERE id=%s AND state<>'RELEASED' RETURNING *",
            (session_id,),
        )
        if not rows:
            return True
        manager = await self._get_manager()
        sandbox_id = rows[0]["sandbox_id"]
        if not sandbox_id:
            return False
        try:
            await manager.kill_sandbox(sandbox_id)
        except SandboxApiException as error:
            if error.status_code != 404:
                raise
        for _ in range(20):
            try:
                await manager.get_sandbox_info(sandbox_id)
            except SandboxApiException as error:
                if error.status_code != 404:
                    raise
                await self.database.execute(
                    "UPDATE work_sandbox_sessions SET state='RELEASED',updated_at=now() WHERE id=%s",
                    (session_id,),
                )
                await self.database.execute(
                    "UPDATE work_browser_transports SET status='CLOSED',token_ciphertext='',updated_at=now() "
                    "WHERE session_id=%s",
                    (session_id,),
                )
                return True
            await asyncio.sleep(0.25)
        return False

    async def reconcile(self) -> int:
        """Discover lost responses by installation metadata and clean inactive runs."""
        namespace = str((await self.database.fetch("SELECT id FROM work_sandbox_namespace"))[0]["id"])
        manager = await self._get_manager()
        page = 1
        while True:
            inventory = await manager.list_sandbox_infos(
                SandboxFilter(
                    metadata={"purpose": "owner-agent-work", "namespace": namespace},
                    page=page,
                    page_size=100,
                )
            )
            for info in inventory.sandbox_infos:
                metadata = info.metadata or {}
                if metadata.get("namespace") != namespace or metadata.get("purpose") != "owner-agent-work":
                    continue
                try:
                    identifier = UUID(metadata.get("session", ""))
                except ValueError:
                    continue
                await self.database.execute(
                    "UPDATE work_sandbox_sessions SET sandbox_id=%s,updated_at=now() "
                    "WHERE id=%s AND sandbox_id IS NULL AND state<>'RELEASED'",
                    (info.id, identifier),
                )
            if not inventory.pagination.has_next_page:
                break
            page += 1
        retained = await self.database.fetch(
            "SELECT s.id,s.sandbox_id FROM work_sandbox_sessions s JOIN work_queue_runs r ON r.id=s.run_id "
            "WHERE s.state<>'RELEASED' AND s.retention_required AND s.sandbox_id IS NOT NULL "
            "AND (r.status<>'RUNNING' OR r.lease_until<=now())"
        )
        for session in retained:
            try:
                await manager.get_sandbox_info(session["sandbox_id"])
            except SandboxApiException as error:
                if error.status_code != 404:
                    raise
                await self.database.execute(
                    "UPDATE work_sandbox_sessions SET state='RELEASED',retention_required=false,"
                    "data_lost=NOT browser_persistent,"
                    "updated_at=now() "
                    "WHERE id=%s AND retention_required",
                    (session["id"],),
                )
                await self.database.execute(
                    "UPDATE work_browser_transports SET status='CLOSED',token_ciphertext='',updated_at=now() "
                    "WHERE session_id=%s",
                    (session["id"],),
                )
        stale = await self.database.fetch(
            "SELECT s.id FROM work_sandbox_sessions s JOIN work_queue_runs r ON r.id=s.run_id "
            "WHERE s.state<>'RELEASED' AND (NOT s.retention_required OR (s.browser_persistent "
            "AND r.status IN ('CANCELLED','FAILED','COMPLETED') AND NOT EXISTS("
            "SELECT 1 FROM work_browser_controls c WHERE c.run_id=r.id AND c.status<>'CLOSED'))) "
            "AND (s.state='CLOSING' OR r.status<>'RUNNING' OR r.lease_until<=now())"
        )
        released = 0
        for session in stale:
            released += await self.release(session["id"])
        return released

    async def close(self) -> None:
        """Close this process's manager transport, preserving durable reservations."""
        if self.manager is not None:
            await self.manager.close()
