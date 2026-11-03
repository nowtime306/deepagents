"""Owned schedules and batches; external events require a separate signed ingress."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from app.automation_target import channel_pins, target
from app.automation_types import BatchInput, ScheduleInput
from app.errors import fail
from app.list_pagination import decode_cursor, page
from app.permissions import require_account
from app.schedule_clock import Calendar, next_due
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["主动任务"])


class Enabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class BatchAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    acknowledge_unknown: bool = False


def first_due(body: ScheduleInput):
    if body.kind == "webhook":
        return None
    if body.kind == "cron":
        return Calendar.parse(body.cron).next(body.starts_at - timedelta(microseconds=1), body.timezone)
    return body.starts_at


@router.post("/automations/preview")
async def preview(body: ScheduleInput, request: Request):
    await identity(request)
    point, items = first_due(body), []
    for _ in range(5):
        if point is None:
            break
        items.append(point)
        point = next_due(
            body.kind,
            point,
            start=body.starts_at,
            timezone=body.timezone,
            interval_seconds=body.interval_seconds,
            cron=body.cron,
        )
    return {"items": items, "timezone": body.timezone}


@router.get("/automations")
async def listing(request: Request):
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,name,agent_version_id,mode,kind,goal,timezone,starts_at,interval_seconds,cron,next_due_at,"
            "enabled,paused_at,completed_at,missed_policy,overlap_policy,budget,environment,network_domains,"
            "notify_channel_ids,webhook_hourly_limit,revision,last_error,created_at FROM automations "
            "WHERE owner_id=%s AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 100",
            (user["id"],),
        )
    }


@router.get("/automations/targets")
async def targets(request: Request):
    owner = (await identity(request))["id"]
    return {
        "items": await request.app.state.db.fetch(
            "SELECT v.id,v.version,v.snapshot->>'name' AS name FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
            "WHERE v.owner_id=%s AND a.owner_id=%s AND a.enabled AND v.active "
            "ORDER BY a.created_at DESC,v.version DESC",
            (owner, owner),
        )
    }


@router.get("/automations/channel-options")
async def channels(request: Request):
    owner = (await identity(request))["id"]
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,name,kind FROM notification_channels WHERE owner_id=%s AND enabled ORDER BY name",
            (owner,),
        )
    }


@router.put("/automations/{identifier}")
async def edit(identifier: UUID, body: ScheduleInput, request: Request):
    row = await owned(request, "automations", identifier)
    if row["kind"] != body.kind or row["completed_at"]:
        fail(409, "SCHEDULE_IMMUTABLE", "已结束的任务或触发类型不能修改，请新建任务")
    snapshot = await target(request.app.state, row["owner_id"], body)
    await channel_pins(request.app.state.db, row["owner_id"], body.notify_channel_ids)
    rows = await request.app.state.db.fetch(
        "UPDATE automations SET name=%s,agent_version_id=%s,snapshot=%s,mode=%s,goal=%s,timezone=%s,starts_at=%s,"
        "interval_seconds=%s,cron=%s,next_due_at=%s,missed_policy=%s,overlap_policy=%s,budget=%s,environment=%s,"
        "network_domains=%s,notify_channel_ids=%s,revision=revision+1,last_error=NULL,updated_at=now() "
        "WHERE id=%s AND owner_id=%s AND revision=%s AND completed_at IS NULL "
        "AND deleted_at IS NULL RETURNING revision",
        (
            body.name,
            body.agent_version_id,
            Jsonb(snapshot),
            body.mode,
            body.goal,
            body.timezone,
            body.starts_at,
            body.interval_seconds,
            body.cron,
            first_due(body),
            body.missed_policy,
            body.overlap_policy,
            Jsonb(body.budget.model_dump()),
            body.environment,
            Jsonb(body.network_domains),
            Jsonb(jsonable_encoder(body.notify_channel_ids)),
            identifier,
            row["owner_id"],
            body.revision,
        ),
    )
    if not rows:
        fail(409, "AUTOMATION_CONFLICT", "任务已变化，请刷新后再保存")
    return rows[0]


@router.post("/automations", status_code=201)
async def create(body: ScheduleInput, request: Request):
    owner = (await identity(request))["id"]
    return await create_schedule(request.app.state, owner, body)


async def create_schedule(state, owner: UUID, body: ScheduleInput, *, identifier: UUID | None = None, lease=None):
    """Create a paused schedule, sharing validation with lease-authorized chat tools."""
    await require_account(state.db, owner, "automation.manage")
    snapshot = await target(state, owner, body)
    await channel_pins(state.db, owner, body.notify_channel_ids)
    event_key = secrets.token_urlsafe(32) if body.kind == "webhook" else ""
    identifier = identifier or uuid4()
    async with state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710024,hashtext(%s))", (str(owner),))
        if lease:
            active = await (await connection.execute(
                "SELECT 1 FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
                "WHERE r.id=%s AND r.lease_token=%s AND r.lease_until>now() AND r.status='RUNNING' "
                "AND NOT r.cancel_requested AND r.origin='interactive' AND c.owner_id=%s",
                (*lease, owner),
            )).fetchone()
            if not active:
                fail(403, "ASK_LEASE_LOST", "对话执行授权已失效，未创建自动任务")
        existing = await (await connection.execute(
            "SELECT id,enabled,revision,owner_id,deleted_at FROM automations WHERE id=%s", (identifier,),
        )).fetchone()
        if existing:
            if existing["owner_id"] != owner or existing["deleted_at"]:
                fail(409, "AUTOMATION_DRAFT_UNAVAILABLE", "该对话草稿已删除或不可用，不会重新创建")
            return {key: existing[key] for key in ("id", "enabled", "revision")}
        count = await (
            await connection.execute(
                "SELECT count(*) AS count FROM automations WHERE owner_id=%s AND deleted_at IS NULL",
                (owner,),
            )
        ).fetchone()
        if count["count"] >= 20:
            fail(422, "AUTOMATION_LIMIT", "每账号最多保留 20 个主动任务")
        await connection.execute(
            "INSERT INTO automations(id,owner_id,name,agent_version_id,snapshot,mode,kind,goal,timezone,starts_at,"
            "interval_seconds,cron,next_due_at,missed_policy,overlap_policy,budget,environment,network_domains,"
            "notify_channel_ids,webhook_hourly_limit,webhook_key_ciphertext) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                owner,
                body.name,
                body.agent_version_id,
                Jsonb(snapshot),
                body.mode,
                body.kind,
                body.goal,
                body.timezone,
                body.starts_at,
                body.interval_seconds,
                body.cron,
                first_due(body),
                body.missed_policy,
                body.overlap_policy,
                Jsonb(body.budget.model_dump()),
                body.environment,
                Jsonb(body.network_domains),
                Jsonb(jsonable_encoder(body.notify_channel_ids)),
                body.webhook_hourly_limit,
                state.models.vault.encrypt(event_key),
            ),
        )
    result = {"id": identifier, "enabled": False, "revision": 1}
    if event_key:
        result.update(event_key=event_key, event_path=f"/api/public/v1/automations/{identifier}/events")
    return result


async def owned(request, table: str, identifier: UUID):
    owner = (await identity(request))["id"]
    rows = await request.app.state.db.fetch(
        f"SELECT * FROM {table} WHERE id=%s AND owner_id=%s AND deleted_at IS NULL",
        (identifier, owner),
    )
    if not rows:
        fail(404, "NOT_FOUND", "任务不存在或无权访问")
    return rows[0]


async def cancel_sources(connection, column: str, identifiers: list):
    for table in ("task_runs", "work_queue_runs"):
        await connection.execute(
            f"UPDATE {table} SET cancel_requested=true WHERE {column}=ANY(%s::uuid[]) "
            "AND status IN ('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')",
            (identifiers,),
        )


@router.put("/automations/{identifier}/state")
async def schedule_state(identifier: UUID, body: Enabled, request: Request):
    row = await owned(request, "automations", identifier)
    if body.enabled:
        await require_account(request.app.state.db, row["owner_id"], "automation.execute")
        await target(
            request.app.state,
            row["owner_id"],
            ScheduleInput(**{key: row[key] for key in ScheduleInput.model_fields if key in row}),
        )
        if row["completed_at"] or (row["next_due_at"] is None and row["kind"] != "webhook"):
            fail(409, "SCHEDULE_FINISHED", "此一次性任务已经结束，请新建任务")
    async with request.app.state.db.pool.connection() as connection:
        updated = await (
            await connection.execute(
                "UPDATE automations SET enabled=%s,paused_at=%s,revision=revision+1,last_error=NULL,updated_at=now() "
                "WHERE id=%s AND deleted_at IS NULL AND (NOT %s OR completed_at IS NULL) RETURNING id",
                (body.enabled, None if body.enabled else datetime.now(UTC), identifier, body.enabled),
            )
        ).fetchone()
        if not updated:
            fail(409, "SCHEDULE_CHANGED", "任务已结束或删除，请刷新")
        if not body.enabled:
            rows = await (
                await connection.execute("SELECT id FROM automation_occurrences WHERE automation_id=%s", (identifier,))
            ).fetchall()
            await cancel_sources(connection, "automation_occurrence_id", [r["id"] for r in rows])
    return {"enabled": body.enabled}


@router.delete("/automations/{identifier}")
async def remove(identifier: UUID, request: Request):
    await owned(request, "automations", identifier)
    await schedule_state(identifier, Enabled(enabled=False), request)
    await request.app.state.db.execute("UPDATE automations SET deleted_at=now() WHERE id=%s", (identifier,))
    return {"deleted": True}


@router.get("/automations/{identifier}/occurrences")
async def occurrences(identifier: UUID, request: Request,
                      before: str | None = Query(default=None, max_length=1000)):
    source = await owned(request, "automations", identifier)
    scope = "occurrences:" + str(identifier)
    stamp, cursor = decode_cursor(before, source["owner_id"], scope)
    rows = await request.app.state.db.fetch(
        "SELECT id,scheduled_for,status,ask_run_id,work_run_id,target_id,result_status,error_code,created_at "
        "FROM automation_occurrences WHERE automation_id=%s AND owner_id=%s "
        "AND (%s::timestamptz IS NULL OR (scheduled_for,id)<(%s,%s::uuid)) "
        "ORDER BY scheduled_for DESC,id DESC LIMIT 101", (identifier,source["owner_id"],stamp,stamp,cursor))
    return page(rows, 100, source["owner_id"], scope, time_key="scheduled_for")


@router.post("/batches", status_code=201)
async def batch_create(body: BatchInput, request: Request):
    owner = (await identity(request))["id"]
    snapshot = await target(request.app.state, owner, body)
    pins = await channel_pins(request.app.state.db, owner, body.notify_channel_ids)
    identifier = uuid4()
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute(
            "INSERT INTO agent_batches(id,owner_id,name,agent_version_id,snapshot,mode,max_parallel,on_failure,"
            "model_call_limit,token_limit,item_budget,environment,network_domains,notify_channel_ids,notify_pins) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                owner,
                body.name,
                body.agent_version_id,
                Jsonb(snapshot),
                body.mode,
                body.max_parallel,
                body.on_failure,
                body.model_call_limit,
                body.token_limit,
                Jsonb(body.budget.model_dump()),
                body.environment,
                Jsonb(body.network_domains),
                Jsonb(jsonable_encoder(body.notify_channel_ids)),
                Jsonb(pins),
            ),
        )
        for index, goal in enumerate(body.goals):
            await connection.execute(
                "INSERT INTO batch_items(id,batch_id,position,goal) VALUES(%s,%s,%s,%s)",
                (uuid4(), identifier, index, goal),
            )
    return {"id": identifier, "status": "ACTIVE"}


@router.get("/batches")
async def batch_list(request: Request, before: str | None = Query(default=None, max_length=1000),
                     q: str = Query(default="", max_length=200)):
    owner = (await identity(request))["id"]
    stamp, cursor = decode_cursor(before, owner, "batches")
    rows = await request.app.state.db.fetch(
        "SELECT b.id,b.name,b.mode,b.status,b.max_parallel,b.on_failure,b.model_call_limit,b.token_limit,"
        "b.last_error,b.created_at, (SELECT jsonb_agg(jsonb_build_object('id',i.id,'position',i.position,"
        "'goal',i.goal,'status',i.status,'error_code',i.error_code,'target_id',i.target_id) ORDER BY i.position) "
        "FROM batch_items i WHERE i.batch_id=b.id) AS items FROM agent_batches b "
        "WHERE b.owner_id=%s AND b.deleted_at IS NULL AND (%s='' OR strpos(lower(b.name),lower(%s))>0) "
        "AND (%s::timestamptz IS NULL OR (b.created_at,b.id)<(%s,%s::uuid)) "
        "ORDER BY b.created_at DESC,b.id DESC LIMIT 101", (owner,q,q,stamp,stamp,cursor))
    return page(rows, 100, owner, "batches")


@router.post("/batches/{identifier}/action")
async def batch_action(identifier: UUID, body: BatchAction, request: Request):
    row = await owned(request, "agent_batches", identifier)
    if body.action not in {"pause", "resume", "cancel"}:
        fail(422, "ACTION_INVALID", "不支持的批次操作")
    async with request.app.state.db.pool.connection() as connection:
        current = await (
            await connection.execute("SELECT status FROM agent_batches WHERE id=%s FOR UPDATE", (identifier,))
        ).fetchone()
        if current["status"] in {"COMPLETED", "CANCELLED"}:
            fail(409, "BATCH_FINISHED", "此批次已经结束")
        if body.action == "resume":
            await require_account(request.app.state.db, row["owner_id"], "automation.execute")
            unknown = await (
                await connection.execute(
                    "SELECT 1 FROM batch_items WHERE batch_id=%s AND status='UNKNOWN' AND NOT acknowledged",
                    (identifier,),
                )
            ).fetchone()
            if unknown and not body.acknowledge_unknown:
                fail(409, "UNKNOWN_REQUIRES_REVIEW", "请先核对未知结果；继续只执行尚未开始的项目，不重试旧请求")
            await connection.execute(
                "UPDATE batch_items SET acknowledged=true WHERE batch_id=%s "
                "AND status IN ('FAILED','UNKNOWN','CANCELLED')",
                (identifier,),
            )
        status = {"pause": "PAUSED", "resume": "ACTIVE", "cancel": "CANCELLED"}[body.action]
        await connection.execute(
            "UPDATE agent_batches SET status=%s,last_error=NULL,notified=false,revision=revision+1,updated_at=now() "
            "WHERE id=%s",
            (status, identifier),
        )
        if body.action == "cancel":
            items = await (
                await connection.execute("SELECT id FROM batch_items WHERE batch_id=%s", (identifier,))
            ).fetchall()
            await cancel_sources(connection, "batch_item_id", [item["id"] for item in items])
            await connection.execute(
                "UPDATE batch_items SET status='CANCELLED',finished_at=now() WHERE batch_id=%s AND status='WAITING'",
                (identifier,),
            )
    return {"status": status}
