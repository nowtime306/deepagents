"""Serialize maintenance admission with HTTP writes and durable queue claims."""

from contextlib import asynccontextmanager

from psycopg import AsyncConnection

from app.errors import fail

GATE = 710035


async def slot(connection):
    await connection.execute("SELECT pg_advisory_xact_lock_shared(%s)", (GATE,))
    row = await (await connection.execute("SELECT active FROM platform_maintenance WHERE singleton")).fetchone()
    active = row["active"] if isinstance(row, dict) else row[0]
    return not active


async def admitted(database, statement, params=()):
    async with database.pool.connection() as connection:
        if not await slot(connection):
            return []
        return await (await connection.execute(statement, params)).fetchall()


@asynccontextmanager
async def request_slot(url):
    # A separate bounded connection avoids holding a pool slot while a handler needs the same pool.
    async with await AsyncConnection.connect(url, autocommit=True, connect_timeout=5) as connection:
        await connection.execute("SELECT pg_advisory_lock_shared(%s)", (GATE,))
        row = await (await connection.execute("SELECT active FROM platform_maintenance WHERE singleton")).fetchone()
        if row[0]:
            fail(503, "PLATFORM_MAINTENANCE", "平台正在离线备份，请稍后重试；运行中的任务不会为备份被取消")
        yield


async def idle(connection):
    # Also fence message delivery and local/manual operations, not only LLM calls.
    row = await (
        await connection.execute(
            "SELECT EXISTS(SELECT 1 FROM task_runs WHERE status IN ('PENDING','RUNNING') "
            "UNION ALL SELECT 1 FROM work_queue_runs WHERE status IN ('PENDING','RUNNING') "
            "UNION ALL SELECT 1 FROM workflow_runs WHERE status IN ('PENDING','RUNNING') "
            "UNION ALL SELECT 1 FROM resource_operations WHERE status='RUNNING' "
            "UNION ALL SELECT 1 FROM notification_deliveries WHERE status IN ('PENDING','SENDING') "
            "UNION ALL SELECT 1 FROM knowledge_experiments WHERE status IN ('PENDING','RUNNING') "
            "UNION ALL SELECT 1 FROM work_browser_controls WHERE status<>'CLOSED' "
            "UNION ALL SELECT 1 FROM repository_actions WHERE status='STARTED') AS busy"
        )
    ).fetchone()
    if row["busy"] if isinstance(row, dict) else row[0]:
        return False
    # Optional new tables must not break the pre-migration offline backup of an older database.
    for table, condition in (("identity_deliveries", "status='sending'"),
                             ("knowledge_video_jobs", "holds_memory OR status IN ('RUNNING','UNKNOWN')")):
        present = await (await connection.execute("SELECT to_regclass(%s) AS name", (table,))).fetchone()
        if (present["name"] if isinstance(present, dict) else present[0]):
            busy = await (await connection.execute(
                "SELECT EXISTS(SELECT 1 FROM " + table + " WHERE " + condition + ") AS busy")).fetchone()
            if busy["busy"] if isinstance(busy, dict) else busy[0]:
                return False
    return True
