"""Bounded operational samples and durable alert transitions; never inspect user content."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from psycopg.types.json import Jsonb

from app.errors import APIError
from app.maintenance import GATE
from app.notifications import notify
from app.operations_health import host

KINDS = ("api", "ask", "work", "ops")
MODES = ("ask", "work", "workflow")
SYSTEM_METRICS = {"service_offline", "queue_wait_seconds", "memory_available_bytes", "disk_free_bytes", "failure_rate"}
PERSONAL_METRICS = {"failure_rate", "budget_ratio"}
logger = logging.getLogger(__name__)


async def run_summary(db, hours=1, owner=None):
    """Return completed-run duration, not model first-token latency."""
    return (await db.fetch(
        "WITH runs AS (SELECT r.status,r.created_at,r.finished_at FROM task_runs r "
        "JOIN conversations c ON c.id=r.conversation_id WHERE (%s::uuid IS NULL OR c.owner_id=%s) "
        "UNION ALL SELECT status,created_at,CASE WHEN status IN ('COMPLETED','FAILED','CANCELLED') "
        "THEN updated_at END FROM work_queue_runs WHERE (%s::uuid IS NULL OR owner_id=%s) "
        "UNION ALL SELECT r.status,r.created_at,r.finished_at FROM workflow_runs r "
        "JOIN workflows w ON w.id=r.workflow_id "
        "WHERE (%s::uuid IS NULL OR w.owner_id=%s)) SELECT count(*) AS total,"
        "count(*) FILTER(WHERE status IN ('SUCCEEDED','COMPLETED')) AS succeeded,"
        "count(*) FILTER(WHERE status='FAILED') AS failed,"
        "count(*) FILTER(WHERE finished_at IS NOT NULL) AS duration_samples,"
        "percentile_cont(.95) WITHIN GROUP(ORDER BY extract(epoch FROM(finished_at-created_at))) "
        "FILTER(WHERE finished_at IS NOT NULL) AS p95_seconds FROM runs "
        "WHERE created_at>=now()-make_interval(hours=>%s)",
        (owner, owner, owner, owner, owner, owner, hours),
    ))[0]


async def collect(db, path):
    """Collect anonymous, bounded aggregate values from the real database and host."""
    services = await db.fetch(
        "SELECT kind,count(*) FILTER(WHERE heartbeat_at>now()-interval '15 seconds') AS online,"
        "max(heartbeat_at) AS last_heartbeat FROM service_instances GROUP BY kind")
    queues = await db.fetch(
        "WITH q AS (SELECT 'ask' AS mode,status,created_at FROM task_runs UNION ALL "
        "SELECT 'work',status,created_at FROM work_queue_runs UNION ALL SELECT 'workflow',status,created_at "
        "FROM workflow_runs) SELECT mode,status,count(*) AS count,"
        "max(extract(epoch FROM(now()-created_at))) FILTER(WHERE status='PENDING') AS oldest_seconds "
        "FROM q GROUP BY mode,status ORDER BY mode,status")
    usage = await db.fetch(
        "SELECT status,count(*) AS calls,sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,"
        "count(*) FILTER(WHERE input_tokens IS NULL OR output_tokens IS NULL) AS unknown_tokens "
        "FROM model_usage GROUP BY status")
    costs = await db.fetch(
        "WITH charges AS (SELECT call_id,currency,estimated_cost AS amount FROM model_usage UNION ALL "
        "SELECT u.call_id,p.currency,u.amount FROM extra_cost_usage u JOIN extra_cost_prices p ON p.id=u.price_id) "
        "SELECT currency,sum(amount) AS estimated_cost,count(DISTINCT call_id) FILTER(WHERE amount IS NULL) "
        "AS unknown_calls FROM charges GROUP BY currency")
    unknown = (await db.fetch(
        "SELECT count(DISTINCT call_id) AS count FROM (SELECT call_id FROM model_usage WHERE estimated_cost IS NULL "
        "UNION SELECT call_id FROM extra_cost_usage WHERE amount IS NULL) u"))[0]["count"]
    environments = await db.fetch(
        "SELECT kind,state,count(*) AS count FROM work_sandbox_sessions WHERE state<>'RELEASED' GROUP BY kind,state")
    return jsonable_encoder({"services": services, "queues": queues, "usage": usage, "costs": costs,
        "unknown_cost_calls": unknown,
        "environments": environments, "host": await asyncio.to_thread(host, path),
        "runs_1h": await run_summary(db), "runs_24h": await run_summary(db, 24)}, custom_encoder={Decimal: str})


async def window_totals(db, start, end):
    """Attribute late extra charges to the original model call, never its bookkeeping date."""
    usage = (await db.fetch(
        "SELECT count(*) AS calls,CASE WHEN count(*)=0 THEN 0 ELSE sum(input_tokens) END AS input_tokens,"
        "CASE WHEN count(*)=0 THEN 0 ELSE sum(output_tokens) END AS output_tokens,"
        "count(*) FILTER(WHERE input_tokens IS NULL OR output_tokens IS NULL) AS unknown_tokens "
        "FROM model_usage WHERE created_at>=%s AND created_at<%s", (start, end)))[0]
    charges = """WITH calls AS (SELECT * FROM model_usage WHERE created_at>=%s AND created_at<%s),
        charges AS (SELECT call_id,currency,estimated_cost AS amount FROM calls UNION ALL
        SELECT u.call_id,p.currency,u.amount FROM extra_cost_usage u JOIN calls c ON c.call_id=u.call_id
        JOIN extra_cost_prices p ON p.id=u.price_id) """
    costs = await db.fetch(charges + "SELECT currency,sum(amount) AS estimated_cost,"
        "count(DISTINCT call_id) FILTER(WHERE amount IS NULL) AS unknown_calls FROM charges GROUP BY currency",
        (start, end))
    unknown = (await db.fetch(charges + "SELECT count(DISTINCT call_id) AS count FROM charges WHERE amount IS NULL",
                             (start, end)))[0]["count"]
    return {"from": start, "to": end, "usage": usage, "costs": costs, "unknown_cost_calls": unknown}


async def metric_value(db, rule, sample):
    metric = rule["metric"]
    if metric == "service_offline":
        return float(not any(s["kind"] == rule["target"] and s["online"] > 0 for s in sample["services"]))
    if metric == "queue_wait_seconds":
        return max((float(q["oldest_seconds"] or 0) for q in sample["queues"]
                    if q["mode"] == rule["target"] and q["status"] == "PENDING"), default=0)
    if metric in {"memory_available_bytes", "disk_free_bytes"}:
        return sample["host"].get(metric.removesuffix("_bytes"))
    if metric == "failure_rate":
        row = sample["runs_1h"] if rule["scope"] == "system" else await run_summary(db, owner=rule["owner_id"])
        return row["failed"] / row["total"] if row["total"] else None
    if metric == "budget_ratio":
        rows = await db.fetch(
            "SELECT amount,anchor_at+floor(extract(epoch FROM(now()-anchor_at))/(period_days*86400))"
            "*make_interval(days=>period_days) AS start FROM money_budgets "
            "WHERE owner_id=%s AND currency=%s AND enabled", (rule["owner_id"], rule["target"]))
        if not rows:
            return None
        from app.extra_cost_budget import spent

        async with db.pool.connection() as c:
            try:
                amount = await spent(c, rule["owner_id"], rule["target"], rows[0]["start"])
            except APIError as error:
                if error.code != "MONEY_EXTRA_UNKNOWN":
                    raise
                return None
        return float(amount / rows[0]["amount"])
    return None


def breached(rule, value):
    if value is None:
        return None
    if rule["metric"] in {"memory_available_bytes", "disk_free_bytes"}:
        return value < rule["threshold"]
    return value >= rule["threshold"]


async def transition(connection, rule, value, now):
    """Acknowledge never resolves an incident; only measured recovery does."""
    active = await (await connection.execute(
        "SELECT * FROM monitor_alerts WHERE rule_id=%s AND status='FIRING' FOR UPDATE", (rule["id"],))).fetchone()
    state = breached(rule, value)
    if state is None:
        await connection.execute("UPDATE monitor_rules SET pending_since=NULL WHERE id=%s", (rule["id"],))
        return
    if not state:
        await connection.execute("UPDATE monitor_rules SET pending_since=NULL WHERE id=%s", (rule["id"],))
        if active:
            await connection.execute(
                "UPDATE monitor_alerts SET status='RECOVERED',recovered_at=%s,last_seen_at=%s,value=%s WHERE id=%s",
                (now, now, value, active["id"]))
        return
    if active:
        await connection.execute("UPDATE monitor_alerts SET last_seen_at=%s,value=%s WHERE id=%s",
                                 (now, value, active["id"]))
        return
    if not rule["enabled"]:
        return
    pending = rule["pending_since"] or now
    await connection.execute("UPDATE monitor_rules SET pending_since=%s WHERE id=%s", (pending, rule["id"]))
    if (now-pending).total_seconds() < rule["duration_seconds"]:
        return
    identifier = uuid4()
    await connection.execute(
        "INSERT INTO monitor_alerts(id,rule_id,owner_id,rule_revision,name,scope,status,value,"
        "first_seen_at,last_seen_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,'FIRING',%s,%s,%s)",
        (identifier, rule["id"], rule["owner_id"], rule["revision"], rule["name"], rule["scope"], value, now, now))
    await deliver(connection, rule, identifier, now)


async def deliver(connection, rule, identifier, now):
    # Local incident always exists. An immediately repeated incident may be inside notification cooldown.
    if rule["last_notified_at"] and (now-rule["last_notified_at"]).total_seconds() < rule["cooldown_seconds"]:
        return
    channels = await (await connection.execute(
        "SELECT id,revision FROM notification_channels WHERE id=ANY(%s::uuid[]) AND owner_id=%s AND enabled",
        (rule["channel_ids"], rule["owner_id"]))).fetchall()
    await notify(connection, rule["owner_id"], "监测告警："+rule["name"], "PAUSED", "monitoring", identifier,
                 "monitor:"+str(identifier), channels)
    await connection.execute("UPDATE monitor_rules SET last_notified_at=%s WHERE id=%s", (now, rule["id"]))


async def tick(db, settings):
    """Share the existing Ops loop and maintenance admission; retain seven days only."""
    async with db.pool.connection() as connection:
        locked = await (await connection.execute(
            "SELECT pg_try_advisory_xact_lock_shared(%s) AND pg_try_advisory_xact_lock(710039) AS ok", (GATE,)
        )).fetchone()
        if not locked["ok"]:
            return
        latest = await (await connection.execute("SELECT max(sampled_at) AS at FROM monitor_samples")).fetchone()
        now = datetime.now(UTC)
        if latest["at"] and (now-latest["at"]).total_seconds() < 30:
            return
        sample = await collect(db, settings.data_dir)
        await connection.execute("INSERT INTO monitor_samples VALUES(%s,%s)", (now, Jsonb(sample)))
        rules = await (await connection.execute(
            "SELECT * FROM monitor_rules r WHERE enabled OR EXISTS(SELECT 1 FROM monitor_alerts a "
            "WHERE a.rule_id=r.id AND a.status='FIRING') FOR UPDATE")).fetchall()
        for rule in rules:
            await transition(connection, rule, await metric_value(db, rule, sample), now)
        await connection.execute("DELETE FROM monitor_samples WHERE sampled_at<%s", (now-timedelta(days=7),))


async def safe_tick(db, settings):
    try:
        await tick(db, settings)
    except Exception as error:
        # A broken sampler must not stop backup coordination; age remains visible to independent scrapers.
        logger.error("Monitoring sampling failed (%s)", type(error).__name__)
