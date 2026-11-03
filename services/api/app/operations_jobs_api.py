"""Administrator backup scheduling and measured local capacity."""

import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.operations_health import host
from app.operations_worker import next_due
from app.security import administrator

router = APIRouter(prefix="/api/v1/operations", tags=["备份与容量"])


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    retain: int = Field(ge=2, le=30)
    revision: int = Field(ge=1)


@router.get("/backups")
async def backups(request: Request) -> dict:
    await administrator(request)
    db = request.app.state.db
    return {
        "settings": (await db.fetch("SELECT * FROM backup_settings WHERE singleton"))[0],
        "maintenance": (await db.fetch("SELECT active,started_at FROM platform_maintenance WHERE singleton"))[0],
        "items": await db.fetch(
            "SELECT id,status,phase,reason,attempt,available_at,backup_name,verification,"
            "created_at,finished_at,deleted_at FROM backup_jobs ORDER BY created_at DESC LIMIT 50"
        ),
    }


@router.put("/backups/schedule")
async def schedule(body: Schedule, request: Request) -> dict:
    await administrator(request)
    rows = await request.app.state.db.fetch(
        "UPDATE backup_settings SET enabled=%s,hour=%s,minute=%s,retain=%s,"
        "next_due_at=%s,revision=revision+1,updated_at=now() WHERE singleton AND revision=%s RETURNING revision",
        (
            body.enabled,
            body.hour,
            body.minute,
            body.retain,
            next_due(body.hour, body.minute) if body.enabled else None,
            body.revision,
        ),
    )
    if not rows:
        fail(409, "BACKUP_SETTINGS_CHANGED", "备份设置已修改，请刷新后重试")
    return {"ok": True}


@router.post("/backups/jobs", status_code=202)
async def enqueue(request: Request) -> dict:
    owner = (await administrator(request))["id"]
    online = await request.app.state.db.fetch(
        "SELECT 1 FROM service_instances WHERE kind='ops' AND heartbeat_at>now()-interval '15 seconds'"
    )
    if not online:
        fail(503, "OPERATIONS_OFFLINE", "运维后台未运行，请先启动本项目备份运维服务")
    identifier = uuid4()
    try:
        await request.app.state.db.execute("INSERT INTO backup_jobs(id,owner_id) VALUES(%s,%s)", (identifier, owner))
    except UniqueViolation:
        fail(409, "BACKUP_BUSY", "已有排队或执行中的备份，请等待当前备份完成")
    return {"id": identifier}


@router.delete("/backups/jobs/{identifier}")
async def cancel(identifier: UUID, request: Request) -> dict:
    await administrator(request)
    rows = await request.app.state.db.fetch(
        "UPDATE backup_jobs SET status='FAILED',phase='CANCELLED',"
        "reason='CANCELLED_BY_ADMIN',finished_at=now() WHERE id=%s AND status='PENDING' RETURNING id",
        (identifier,),
    )
    if not rows:
        fail(409, "BACKUP_NOT_PENDING", "仅可取消尚未开始的备份")
    return {"ok": True}


@router.get("/capacity")
async def capacity(request: Request) -> dict:
    await administrator(request)
    db = request.app.state.db
    return {
        "host": await asyncio.to_thread(host, request.app.state.settings.data_dir),
        "queue": await db.fetch("SELECT status,count(*) AS n FROM work_queue_runs GROUP BY status"),
        "sandboxes": await db.fetch(
            "SELECT kind,state,count(*) AS n FROM work_sandbox_sessions WHERE state<>'RELEASED' GROUP BY kind,state"
        ),
        "waiting": await db.fetch(
            "SELECT count(*) FILTER(WHERE available_after>now()) AS delayed,"
            "count(*) FILTER(WHERE pause_requested) AS pause_requested FROM work_queue_runs WHERE status='PENDING'"
        ),
        "note": "本机实时观测；CPU首次采样需等待下一次刷新。编程与浏览器环境各占独立配额，"
        "保留环境与人工接管也占用配额，未据此推断并发上限。",
    }
