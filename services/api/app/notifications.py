"""Owned, deduplicated completion and action-required notifications."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request

from app.security import identity

router = APIRouter(prefix="/api/v1/notifications", tags=["通知"])
LABELS = {
    "SUCCEEDED": "已完成",
    "FAILED": "执行失败",
    "UNKNOWN": "结果未知，需核对",
    "CANCELLED": "已取消",
    "REJECTED": "配置或权限已失效",
    "PAUSED": "已暂停，需处理",
    "COMPLETED": "批次已结束",
}


async def notify(connection, owner, title, status, kind, target, key, pins=None):
    row = await (
        await connection.execute(
            "INSERT INTO notifications(id,owner_id,title,message,target_kind,target_id,dedupe_key) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(owner_id,dedupe_key) DO UPDATE SET dedupe_key=EXCLUDED.dedupe_key RETURNING id",
            (uuid4(), owner, title, LABELS.get(status, status), kind, target, key),
        )
    ).fetchone()
    for pin in pins or []:
        await connection.execute(
            "INSERT INTO notification_deliveries(id,notification_id,channel_id,channel_revision) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(notification_id,channel_id) WHERE retry_of IS NULL DO NOTHING",
            (uuid4(), row["id"], pin["id"], pin["revision"]),
        )


async def collect(database):
    async with database.pool.connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT o.*,a.mode FROM automation_occurrences o JOIN automations a ON a.id=o.automation_id "
                "WHERE NOT o.notified AND (o.result_status IS NOT NULL OR o.status='REJECTED') "
                "ORDER BY o.created_at FOR UPDATE OF o SKIP LOCKED LIMIT 100",
            )
        ).fetchall()
        for row in rows:
            await notify(
                connection,
                row["owner_id"],
                row["title"],
                row["result_status"] or row["status"],
                "chat" if row["mode"] == "ask" else "work",
                row["target_id"],
                "occurrence:" + str(row["id"]),
                row["notify_pins"],
            )
            await connection.execute("UPDATE automation_occurrences SET notified=true WHERE id=%s", (row["id"],))
        rows = await (
            await connection.execute(
                "SELECT * FROM agent_batches WHERE status IN ('PAUSED','COMPLETED','CANCELLED') AND NOT notified "
                "FOR UPDATE SKIP LOCKED LIMIT 100",
            )
        ).fetchall()
        for row in rows:
            await notify(
                connection,
                row["owner_id"],
                row["name"],
                row["status"],
                "batch",
                row["id"],
                f"batch:{row['id']}:{row['revision']}:{row['status']}",
                row["notify_pins"],
            )
            await connection.execute("UPDATE agent_batches SET notified=true WHERE id=%s", (row["id"],))
        # Approval IDs distinguish repeated approval cycles on the same Work run.
        rows = await (
            await connection.execute(
                "SELECT a.id,r.id AS run_id,r.owner_id,r.title FROM work_approvals a "
                "JOIN work_queue_runs r ON r.id=a.run_id "
                "WHERE r.status='WAITING_APPROVAL' AND r.origin<>'interactive' AND a.decision IS NULL LIMIT 100",
            )
        ).fetchall()
        for row in rows:
            await notify(
                connection,
                row["owner_id"],
                row["title"],
                "任务需要审批",
                "work",
                row["run_id"],
                "approval:" + str(row["id"]),
            )


@router.get("")
async def listing(request: Request):
    owner = (await identity(request))["id"]
    rows = await request.app.state.db.fetch(
        "SELECT id,title,message,target_kind,target_id,read_at,created_at FROM notifications "
        "WHERE owner_id=%s ORDER BY created_at DESC LIMIT 100",
        (owner,),
    )
    count = await request.app.state.db.fetch(
        "SELECT count(*) AS count FROM notifications WHERE owner_id=%s AND read_at IS NULL", (owner,)
    )
    return {"items": rows, "unread": count[0]["count"]}


@router.post("/read-all")
async def read_all(request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.db.execute(
        "UPDATE notifications SET read_at=coalesce(read_at,now()) WHERE owner_id=%s", (owner,)
    )
    return {"read": True}


@router.post("/{identifier}/read")
async def read(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    rows = await request.app.state.db.fetch(
        "UPDATE notifications SET read_at=coalesce(read_at,now()) WHERE id=%s AND owner_id=%s RETURNING id",
        (identifier, owner),
    )
    from app.errors import fail

    if not rows:
        fail(404, "NOT_FOUND", "通知不存在或无权访问")
    return {"read": True}
