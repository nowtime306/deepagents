"""Finite audio/video fee adapters: local duration is a reserve estimate, never billed seconds."""

from decimal import Decimal
from uuid import uuid4

from app.errors import APIError, fail
from app.extra_cost_budget import reserve, settle
from app.media_retrieval_probe import duration
from app.permissions import require_account
from app.resource_usage import finish_operation, link_model_snapshot, operation, parent_authority


class ReportedText(str):
    """Preserve existing text-returning adapters while carrying explicit provider duration usage."""

    def __new__(cls, value, seconds=None):
        result = super().__new__(cls, value)
        result.billed_seconds = seconds
        return result


def billed_duration(payload):
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict) or usage.get("type") != "duration":
        return None
    value = usage.get("seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = Decimal(str(value))
    return seconds if seconds.is_finite() and 0 <= seconds <= 86400 else None


async def begin(state, owner, config, unit, data=None, output_reserve=Decimal(600), permission="media.use"):
    await require_account(state.db, owner, permission)
    seconds_prices = await state.db.fetch(
        "SELECT 1 FROM extra_cost_prices WHERE model_id=%s AND unit=%s AND effective_at<=now() LIMIT 1",
        (config.id, unit),
    )
    if seconds_prices and data is None and unit in {"audio_second", "video_second"}:
        budgets = await state.db.fetch("SELECT 1 FROM money_budgets WHERE owner_id=%s AND enabled LIMIT 1", (owner,))
        if budgets:
            fail(422, "MEDIA_OUTPUT_UNBOUNDED", "此生成协议没有可执行的输出时长上限，不能保证按秒金额预留；未发送请求")
    try:
        estimate = await duration(state, owner, config, data) if seconds_prices and data else output_reserve
    except APIError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError):
        fail(503, "MEDIA_DURATION_UNAVAILABLE", "受管媒体时长检测失败，未发送模型请求；请检查执行环境")
    await require_account(state.db, owner, permission)
    current = await state.models.get(config.id)
    if (current.kind, current.protocol) != (config.kind, config.protocol):
        fail(409, "MODEL_INCOMPATIBLE", "媒体模型用途或协议已不兼容，未发送请求")
    call = uuid4()
    context = await operation(state.db, owner, config, "media", "query")
    await state.db.execute(
        "UPDATE resource_operations SET lease_until=now()+interval '180 seconds' WHERE id=%s", (context["id"],)
    )
    try:
        async with state.db.pool.connection() as c:
            await c.execute(
                "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status) "
                "VALUES(%s,%s,%s,'resource',%s,%s,'PENDING')",
                (call, context["id"], owner, config.id, config.revision),
            )
            await link_model_snapshot(c, call, owner, context["id"], config.id)
            await reserve(c, owner, call, config.id, "call", Decimal(1), require_price=not seconds_prices)
            if seconds_prices:
                await reserve(c, owner, call, config.id, unit, estimate, require_price=True)
    except Exception:
        await finish_operation(state.db, context, "FAILED", "MEDIA_REQUEST_NOT_SENT")
        raise
    return {"call_id": call, "owner_id": owner, "context": context, "permission": permission}


async def finish(state, call, unit, seconds=None, *, success=True):
    context = call["context"]
    await require_account(state.db, call["owner_id"], call["permission"])
    await parent_authority(state.db, call["owner_id"], context)
    active = await state.db.fetch(
        "SELECT 1 FROM resource_operations WHERE id=%s AND owner_id=%s AND lease_token=%s "
        "AND status='RUNNING' AND NOT cancel_requested AND lease_until>now()",
        (context["id"], call["owner_id"], context["lease_token"]),
    )
    if not active:
        fail(409, "RESOURCE_LEASE_LOST", "媒体执行租约已失效，未返回迟到结果")
    identifier = call["call_id"]
    async with state.db.pool.connection() as c:
        if success:
            await settle(c, identifier, "call", Decimal(1))
            if seconds is not None:
                await settle(c, identifier, unit, seconds)
        await c.execute("UPDATE model_usage SET status='UNKNOWN',finished_at=now() WHERE call_id=%s", (identifier,))
    if not await finish_operation(
        state.db, context, "SUCCEEDED" if success else "UNKNOWN", None if success else "MEDIA_PROVIDER_RESULT_UNKNOWN"
    ):
        fail(409, "RESOURCE_LEASE_LOST", "媒体执行租约已失效，未返回迟到结果")
