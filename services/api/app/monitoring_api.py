"""Authorized operational dashboards and a separately authenticated Prometheus exporter."""

import hmac
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import IntEnum
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import fail
from app.list_pagination import decode_cursor, page
from app.monitoring import KINDS, MODES, PERSONAL_METRICS, SYSTEM_METRICS, run_summary, window_totals
from app.security import identity

router = APIRouter(tags=["监测与告警"])
PREFIX = "/api/v1/operations/monitoring"
MAX_RULES_PER_OWNER = 50


class Window(IntEnum):
    HOUR = 1
    DAY = 24
    WEEK = 168


async def operator(request, permission="monitoring.read"):
    user = await identity(request)
    if not user["is_admin"] and permission not in user["permissions"]:
        fail(403, "MONITORING_DENIED", "当前角色无系统监测权限")
    return user


async def current(db):
    rows = await db.fetch("SELECT * FROM monitor_samples ORDER BY sampled_at DESC LIMIT 1")
    sample = rows[0] if rows else {"sampled_at": None, "snapshot": None}
    age = (datetime.now(UTC)-sample["sampled_at"]).total_seconds() if rows else None
    return {**sample, "stale": age is None or age > 90, "age_seconds": age, "retention_days": 7}


@router.get(PREFIX)
async def dashboard(request: Request, hours: Window = Window.HOUR):
    await operator(request)
    hours = int(hours)
    sample = await current(request.app.state.db)
    end = datetime.now(UTC)
    return {**sample, "scope": "system", "hours": hours,
            "window": jsonable_encoder(await window_totals(request.app.state.db, end-timedelta(hours=hours), end),
                                       custom_encoder={Decimal: str}),
            "runs": await run_summary(request.app.state.db, hours),
            "note": "耗时为已完成运行的总耗时，不是模型首Token延迟；未报告的费用和资源不补0"}


@router.get(PREFIX + "/series")
async def series(request: Request, hours: Window = Window.HOUR):
    await operator(request)
    hours = int(hours)
    db = request.app.state.db
    rows = await db.fetch(
        "WITH sampled AS (SELECT sampled_at,snapshot,"
        "date_bin(make_interval(secs=>%s),sampled_at,'2020-01-01'::timestamptz) AS bucket "
        "FROM monitor_samples WHERE sampled_at>=now()-make_interval(hours=>%s)) "
        "SELECT DISTINCT ON(bucket) sampled_at,snapshot FROM sampled ORDER BY bucket,sampled_at DESC",
        (hours*30, hours))
    return {"items": rows, "hours": hours, "retention_days": 7, "scope": "system",
            "from": datetime.now(UTC)-timedelta(hours=hours), "to": datetime.now(UTC)}


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    scope: Literal["system", "personal"] = "system"
    metric: str
    target: str = Field(default="", max_length=30)
    threshold: Decimal = Field(ge=0, le=10**16, allow_inf_nan=False)
    duration_seconds: int = Field(default=0, ge=0, le=86400)
    cooldown_seconds: int = Field(default=300, ge=30, le=604800)
    enabled: bool = True
    channel_ids: list[UUID] = Field(default_factory=list, max_length=5)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_metric(self):
        allowed = SYSTEM_METRICS if self.scope == "system" else PERSONAL_METRICS
        if self.metric not in allowed:
            raise ValueError("告警指标与范围不匹配")
        choices = {"service_offline": KINDS, "queue_wait_seconds": MODES, "budget_ratio": ("CNY", "USD")}
        if self.metric in choices and self.target not in choices[self.metric]:
            raise ValueError("告警目标无效")
        if self.metric == "service_offline" and self.threshold != 1:
            raise ValueError("失联告警阈值应为1")
        if self.metric == "failure_rate" and self.threshold > 1:
            raise ValueError("失败率阈值应在0至1之间")
        return self


@router.get(PREFIX + "/alerts/rules")
async def rules(request: Request, before: str | None = None):
    user = await identity(request)
    system = user["is_admin"] or "monitoring.read" in user["permissions"]
    stamp, cursor_id = decode_cursor(before, user["id"], "monitor-rules")
    rows = await request.app.state.db.fetch(
        "SELECT * FROM monitor_rules WHERE (owner_id=%s OR (%s AND scope='system')) "
        "AND (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s)) "
        "ORDER BY created_at DESC,id DESC LIMIT 101", (user["id"], system, stamp, stamp, cursor_id))
    result = page(rows, 100, user["id"], "monitor-rules")
    result["items"] = [{**row, "can_edit": row["owner_id"] == user["id"] and
                       (row["scope"] == "personal" or user["is_admin"] or
                        "monitoring.manage" in user["permissions"])} for row in result["items"]]
    return result


async def authorize_rule(request, body):
    user = await identity(request)
    if body.scope == "system":
        await operator(request, "monitoring.manage")
    for channel in body.channel_ids:
        rows = await request.app.state.db.fetch(
            "SELECT 1 FROM notification_channels WHERE id=%s AND owner_id=%s AND enabled", (channel, user["id"]))
        if not rows:
            fail(422, "CHANNEL_UNAVAILABLE", "通知通道无权使用或已停用")
    return user


@router.post(PREFIX + "/alerts/rules", status_code=201)
async def add_rule(body: RuleInput, request: Request):
    user = await authorize_rule(request, body)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710039)")
        await connection.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user["id"],))
        count = await (await connection.execute(
            "SELECT count(*) AS count FROM monitor_rules WHERE owner_id=%s", (user["id"],))).fetchone()
        if count["count"] >= MAX_RULES_PER_OWNER:
            fail(409, "MONITOR_RULE_LIMIT", "每个账号最多保存50条告警规则，请编辑已有规则")
        total = await (await connection.execute("SELECT count(*) AS count FROM monitor_rules")).fetchone()
        if total["count"] >= 500:
            fail(409, "MONITOR_RULE_LIMIT", "当前单机最多保存500条告警规则，请编辑已有规则")
        rows = await (await connection.execute(
            "INSERT INTO monitor_rules(id,owner_id,name,scope,metric,target,threshold,duration_seconds,"
            "cooldown_seconds,enabled,channel_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (uuid4(), user["id"], body.name, body.scope, body.metric, body.target, body.threshold,
             body.duration_seconds, body.cooldown_seconds, body.enabled, body.channel_ids))).fetchone()
        return rows


@router.put(PREFIX + "/alerts/rules/{identifier}")
async def edit_rule(identifier: UUID, body: RuleInput, request: Request):
    user = await authorize_rule(request, body)
    async with request.app.state.db.pool.connection() as connection:
        previous = await (await connection.execute(
            "SELECT * FROM monitor_rules WHERE id=%s AND owner_id=%s FOR UPDATE",
            (identifier, user["id"]))).fetchone()
        if not previous or previous["revision"] != body.revision:
            fail(409, "MONITOR_RULE_CONFLICT", "规则不存在、无权修改或已被更新，请刷新后重试")
        if previous["scope"] == "system" and not user["is_admin"] and "monitoring.manage" not in user["permissions"]:
            fail(403, "MONITORING_DENIED", "当前角色无系统规则管理权限")
        active = await (await connection.execute(
            "SELECT 1 FROM monitor_alerts WHERE rule_id=%s AND status='FIRING'", (identifier,))).fetchone()
        fields = ("name", "scope", "metric", "target", "threshold", "duration_seconds",
                  "cooldown_seconds", "channel_ids")
        if active and any(previous[key] != getattr(body, key) for key in fields):
            fail(409, "ALERT_STILL_ACTIVE", "当前告警尚未恢复，只能切换启停；不能修改判断口径假装恢复")
        rows = await (await connection.execute(
            "UPDATE monitor_rules SET name=%s,scope=%s,metric=%s,target=%s,threshold=%s,duration_seconds=%s,"
            "cooldown_seconds=%s,enabled=%s,channel_ids=%s,revision=revision+1,pending_since=NULL,updated_at=now() "
            "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING *",
            (body.name, body.scope, body.metric, body.target, body.threshold, body.duration_seconds,
             body.cooldown_seconds, body.enabled, body.channel_ids, identifier, user["id"], body.revision))).fetchone()
        if not rows:
            fail(409, "MONITOR_RULE_CONFLICT", "规则不存在、无权修改或已被更新，请刷新后重试")
        return rows


@router.get(PREFIX + "/alerts/events")
async def events(request: Request, before: str | None = None):
    user = await identity(request)
    system = user["is_admin"] or "monitoring.read" in user["permissions"]
    stamp, cursor_id = decode_cursor(before, user["id"], "monitor-alerts")
    rows = await request.app.state.db.fetch(
        "SELECT * FROM monitor_alerts WHERE (owner_id=%s OR (%s AND scope='system')) "
        "AND (%s::timestamptz IS NULL OR (first_seen_at,id)<(%s,%s)) "
        "ORDER BY first_seen_at DESC,id DESC LIMIT 101",
        (user["id"], system, stamp, stamp, cursor_id))
    result = page(rows, 100, user["id"], "monitor-alerts", time_key="first_seen_at")
    result["items"] = [{**row, "can_ack": (row["scope"] == "personal" and row["owner_id"] == user["id"])
                         or (row["scope"] == "system" and
                             (user["is_admin"] or "monitoring.manage" in user["permissions"]))}
                        for row in result["items"]]
    return result


@router.post(PREFIX + "/alerts/events/{identifier}/acknowledge")
async def acknowledge(identifier: UUID, request: Request):
    user = await identity(request)
    system = user["is_admin"] or "monitoring.manage" in user["permissions"]
    rows = await request.app.state.db.fetch(
        "UPDATE monitor_alerts SET acknowledged_at=coalesce(acknowledged_at,now()),"
        "acknowledged_by=coalesce(acknowledged_by,%s) WHERE id=%s "
        "AND ((owner_id=%s AND scope='personal') OR (%s AND scope='system')) "
        "RETURNING id,status,acknowledged_at", (user["id"], identifier, user["id"], system))
    if not rows:
        fail(404, "ALERT_NOT_FOUND", "告警不存在或无权访问")
    return rows[0]


def exposition(sample):
    """Use only fixed low-cardinality labels; absent observations remain absent."""
    lines = []
    def emit(name, value, labels=""):
        if value is not None and math.isfinite(float(value)):
            metric = "owner_agent_" + name
            if not any(line.startswith("# TYPE "+metric+" ") for line in lines):
                lines.append("# TYPE "+metric+" gauge")
            lines.append(metric + ("{"+labels+"}" if labels else "") + " " + str(float(value)))
    data = sample["snapshot"]
    emit("sample_timestamp_seconds", sample["sampled_at"].timestamp())
    emit("sample_available", 1, 'signal="database"')
    emit("sample_available", int(data["host"].get("memory_total") is not None), 'signal="host"')
    for kind in KINDS:
        emit("service_online", sum(s["online"] for s in data["services"] if s["kind"] == kind), f'kind="{kind}"')
    for row in data["queues"]:
        if row["mode"] in MODES and row["status"] in {
            "PENDING", "RUNNING", "WAITING_APPROVAL", "PAUSED", "NEEDS_REVIEW", "COMPLETED", "SUCCEEDED",
            "FAILED", "CANCELLED", "REJECTED"}:
            emit("queue_runs", row["count"], f'mode="{row["mode"]}",status="{row["status"]}"')
    for mode in MODES:
        emit("queue_oldest_seconds", max((float(q["oldest_seconds"] or 0) for q in data["queues"]
             if q["mode"] == mode and q["status"] == "PENDING"), default=0), f'mode="{mode}"')
    for name in ("cpu_percent", "memory_available", "memory_total", "disk_free", "disk_total"):
        emit("host_"+name+("" if name == "cpu_percent" else "_bytes"), data["host"].get(name))
    for direction in ("input", "output"):
        values = [r[direction+"_tokens"] for r in data["usage"] if r[direction+"_tokens"] is not None]
        emit("model_tokens_sum", sum(float(v) for v in values) if values else None, f'direction="{direction}"')
    for result, state in (("success", "KNOWN"), ("failed", "FAILED"), ("unknown", "UNKNOWN"), ("pending", "PENDING")):
        emit("model_calls_sum", sum(r["calls"] for r in data["usage"] if r["status"] == state), f'result="{result}"')
    for row in data["costs"]:
        if row["currency"] in {"CNY", "USD"}:
            emit("cost_estimate_sum", row["estimated_cost"], f'currency="{row["currency"]}"')
    emit("cost_unknown_calls", data.get("unknown_cost_calls", sum(r["unknown_calls"] for r in data["costs"])))
    return "\n".join(lines)+"\n"


@router.get("/api/v1/internal/observability/metrics", response_class=PlainTextResponse)
async def metrics(request: Request):
    try:
        token = (request.app.state.settings.data_dir / "monitoring/metrics.token").read_text(encoding="utf-8").strip()
    except OSError:
        fail(503, "METRICS_NOT_CONFIGURED", "监测采集凭证尚未配置")
    header = request.headers.get("authorization", "")
    if len(token) < 32 or not hmac.compare_digest(header, "Bearer "+token):
        fail(403, "METRICS_DENIED", "采集凭证无效")
    sample = await current(request.app.state.db)
    if sample["snapshot"] is None:
        return PlainTextResponse("# no persisted sample\nowner_agent_sample_available{signal=\"database\"} 0\n")
    return PlainTextResponse(exposition(sample), media_type="text/plain; version=0.0.4")
