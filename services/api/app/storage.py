"""Small PostgreSQL repository with durable ordered run events."""

from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.errors import fail

# 710013 migrations, 710014 API, 710015 model writes, 710016 sandbox admission.
WORK_MAINTENANCE_LOCK = 710017


class Database:
    """Own the application pool and transaction boundaries."""

    def __init__(self, url: str) -> None:
        self.pool = AsyncConnectionPool(url, open=False, min_size=1, max_size=8, kwargs={"row_factory": dict_row})

    async def fetch(self, sql: str, params: tuple = ()) -> list[dict]:
        """Fetch rows in a short transaction."""
        async with self.pool.connection() as connection:
            cursor = await connection.execute(sql, params)
            return await cursor.fetchall()

    async def execute(self, sql: str, params: tuple = ()) -> None:
        """Commit a single write statement."""
        async with self.pool.connection() as connection:
            await connection.execute(sql, params)

    async def admitted(self, sql: str, params: tuple = ()) -> list[dict]:
        """Claim only while the shared maintenance admission gate is open."""
        from app.maintenance import admitted

        return await admitted(self, sql, params)

    async def event(
        self,
        run_id: UUID,
        name: str,
        data: dict,
        *,
        status: str | None = None,
        error: str | None = None,
        lease_token: UUID | None = None,
        message: dict | None = None,
    ) -> None:
        """Serialize event allocation and an optional terminal state in one transaction."""
        async with self.pool.connection() as connection:
            if lease_token:
                cursor = await connection.execute(
                    "SELECT id FROM task_runs WHERE id=%s AND lease_token=%s AND lease_until>now() "
                    "AND status='RUNNING' FOR UPDATE",
                    (run_id, lease_token),
                )
                if not await cursor.fetchone():
                    fail(409, "ASK_LEASE_LOST", "对话执行租约已失效")
            if name == "run.completed":
                result = await connection.execute(
                    "SELECT cancel_requested FROM task_runs WHERE id=%s FOR UPDATE", (run_id,)
                )
                current = await result.fetchone()
                if current["cancel_requested"]:
                    name, status = "run.cancelled", "CANCELLED"
            cursor = await connection.execute(
                "UPDATE task_runs SET next_event_id=next_event_id+1 WHERE id=%s RETURNING next_event_id",
                (run_id,),
            )
            row = await cursor.fetchone()
            await connection.execute(
                "INSERT INTO run_events(run_id,event_id,event,data) VALUES (%s,%s,%s,%s)",
                (run_id, row["next_event_id"], name, Jsonb(data)),
            )
            if message:
                await connection.execute(
                    "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) VALUES (%s,%s,%s,%s,%s)",
                    (message["id"], message["conversation_id"], run_id, message["role"], Jsonb(message["content"])),
                )
            if status:
                await connection.execute(
                    "UPDATE task_runs SET status=%s,error_code=%s,finished_at=now() WHERE id=%s",
                    (status, error, run_id),
                )
