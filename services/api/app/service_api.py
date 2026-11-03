"""Administrator-only operational liveness; no provider keys, PIDs or private task content."""

from fastapi import APIRouter, Request

from app.security import administrator

router = APIRouter(prefix="/api/v1/operations/services", tags=["后台状态"])


@router.get("")
async def status(request: Request) -> dict:
    await administrator(request)
    db = request.app.state.db
    rows = await db.fetch("SELECT kind,count(*) FILTER(WHERE heartbeat_at>now()-interval '15 seconds') AS online,"
        "max(heartbeat_at) AS last_heartbeat FROM service_instances GROUP BY kind ORDER BY kind")
    events = await db.fetch("SELECT kind,event,reason,created_at FROM service_events ORDER BY id DESC LIMIT 20")
    return {"services": rows, "events": events, "note": "在线数量按最近15秒的心跳统计；未知任务不会自动重放"}
