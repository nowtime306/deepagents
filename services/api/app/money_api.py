"""Price history, owned budgets and explicit unknown-charge reconciliation."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import fail
from app.security import administrator, identity

router = APIRouter(prefix="/api/v1/billing", tags=["费用与预算"])
Rate = Decimal


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    model_id: str = Field(min_length=1, max_length=120)
    currency: Literal["CNY", "USD"]
    scheme: Literal["uniform", "split"] = "uniform"
    input_per_million: Rate = Field(ge=0, le=1000000000, decimal_places=6)
    output_per_million: Rate = Field(ge=0, le=1000000000, decimal_places=6)
    cache_read_per_million: Rate | None = Field(default=None, ge=0, le=1000000000, decimal_places=6)
    cache_write_per_million: Rate | None = Field(default=None, ge=0, le=1000000000, decimal_places=6)
    source: str = Field(min_length=1, max_length=1000)
    effective_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def details(self):
        if self.effective_at.tzinfo is None:
            raise ValueError("生效时间必须包含时区")
        if self.scheme == "split" and (self.cache_read_per_million is None or self.cache_write_per_million is None):
            raise ValueError("分项价格必须填写缓存读写单价")
        return self


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    amount: Decimal = Field(gt=0, le=1000000000, decimal_places=6)
    period_days: int = Field(default=30, ge=1, le=366)
    enabled: bool = True
    revision: int | None = Field(default=None, ge=1)


class Reconcile(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    actual: Decimal = Field(ge=0, le=1000000000, decimal_places=9)
    note: str = Field(min_length=5, max_length=2000)


@router.get("/prices")
async def prices(request: Request) -> dict:
    await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT p.*,coalesce(m.name,p.model_id) AS name FROM model_price_versions p "
            "LEFT JOIN model_configs m ON m.id=p.model_id ORDER BY p.created_at DESC,p.id DESC LIMIT 200"
        )
    }


@router.post("/prices", status_code=201)
async def add_price(body: Price, request: Request) -> dict:
    await administrator(request)
    identifier = uuid4()
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710015)")
        if not await (await connection.execute("SELECT 1 FROM model_configs WHERE id=%s", (body.model_id,))).fetchone():
            fail(422, "MODEL_NOT_FOUND", "模型不存在")
        await connection.execute(
            "INSERT INTO model_price_versions(id,model_id,currency,scheme,input_per_million,"
            "output_per_million,cache_read_per_million,cache_write_per_million,source,effective_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                body.model_id,
                body.currency,
                body.scheme,
                body.input_per_million,
                body.output_per_million,
                body.cache_read_per_million,
                body.cache_write_per_million,
                body.source,
                body.effective_at,
            ),
        )
    return {"id": identifier}


@router.get("/budgets")
async def budgets(request: Request) -> dict:
    user = await identity(request)
    db = request.app.state.db
    rows = await db.fetch(
        "SELECT b.*,anchor_at+floor(extract(epoch FROM(now()-anchor_at))/(period_days*86400))"
        "*make_interval(days=>period_days) AS window_start FROM money_budgets b WHERE owner_id=%s ORDER BY currency",
        (user["id"],),
    )
    for row in rows:
        totals = await db.fetch(
            "SELECT coalesce(sum(actual),0) AS settled,"
            "coalesce(sum(reserved) FILTER(WHERE actual IS NULL),0) AS frozen "
            "FROM (SELECT actual,reserved FROM model_money_holds WHERE owner_id=%s AND currency=%s "
            "AND (window_end>%s OR actual IS NULL) UNION ALL SELECT actual,reserved FROM extra_cost_holds "
            "WHERE owner_id=%s AND currency=%s AND (window_end>%s OR actual IS NULL) UNION ALL "
            "SELECT u.amount,0 FROM extra_cost_usage u JOIN extra_cost_prices p ON p.id=u.price_id "
            "LEFT JOIN extra_cost_holds h ON h.call_id=u.call_id AND h.item=u.item "
            "WHERE u.owner_id=%s AND p.currency=%s AND h.call_id IS NULL AND u.created_at>=%s) h",
            (
                user["id"],
                row["currency"],
                row["window_start"],
                user["id"],
                row["currency"],
                row["window_start"],
                user["id"],
                row["currency"],
                row["window_start"],
            ),
        )
        row.update(totals[0])
        unknown = await db.fetch(
            "SELECT count(*) AS count FROM extra_cost_usage u JOIN extra_cost_prices p ON p.id=u.price_id "
            "LEFT JOIN extra_cost_holds h ON h.call_id=u.call_id AND h.item=u.item "
            "WHERE u.owner_id=%s AND p.currency=%s AND h.call_id IS NULL AND u.amount IS NULL",
            (user["id"], row["currency"]),
        )
        row["unknown_extra_calls"] = unknown[0]["count"]
    return {
        "items": rows,
        "note": "按币种独立估算；含已配置的额外调用/图片费用预留。未知调用继续冻结，"
        "未接入计费适配的搜索/音视频等费用不能视为已覆盖。",
    }


@router.put("/budgets/{currency}")
async def save_budget(currency: Literal["CNY", "USD"], body: Budget, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(user["id"]),))
        row = await (
            await connection.execute(
                "SELECT revision FROM money_budgets WHERE owner_id=%s AND currency=%s", (user["id"], currency)
            )
        ).fetchone()
        if row and row["revision"] != body.revision:
            fail(409, "BUDGET_CONFLICT", "预算已修改，请刷新")
        await connection.execute(
            "INSERT INTO money_budgets(owner_id,currency,amount,period_days,enabled) VALUES(%s,%s,%s,%s,%s) "
            "ON CONFLICT(owner_id,currency) DO UPDATE SET amount=excluded.amount,period_days=excluded.period_days,"
            "enabled=excluded.enabled,revision=money_budgets.revision+1",
            (user["id"], currency, body.amount, body.period_days, body.enabled),
        )
    return {"ok": True}


@router.get("/holds")
async def holds(request: Request) -> dict:
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT h.*,u.status,u.model_id,u.failure_code,u.price_version_id "
            "FROM model_money_holds h JOIN model_usage u ON u.call_id=h.call_id WHERE h.owner_id=%s "
            "ORDER BY u.created_at DESC LIMIT 100",
            (user["id"],),
        )
    }


@router.post("/holds/{call}/reconcile")
async def reconcile(call: UUID, body: Reconcile, request: Request) -> dict:
    admin = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        row = await (
            await connection.execute("SELECT owner_id FROM model_money_holds WHERE call_id=%s", (call,))
        ).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "预留记录不存在")
        await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(row["owner_id"]),))
        # Reconciliation is single-use and does not alter missing tokens or provider statistics.
        updated = await (
            await connection.execute(
                "UPDATE model_money_holds h SET actual=%s,confirmation_note=%s,"
                "confirmed_by=%s,confirmed_at=now() FROM model_usage u WHERE h.call_id=u.call_id AND h.call_id=%s "
                "AND h.actual IS NULL AND h.confirmed_at IS NULL AND (u.status='UNKNOWN' "
                "OR (u.status='KNOWN' AND u.cost_status='UNKNOWN') "
                "OR (u.status='PENDING' AND u.created_at<now()-interval '1 day' "
                "AND NOT EXISTS(SELECT 1 FROM task_runs r WHERE r.id=u.run_id AND r.status IN ('PENDING','RUNNING')) "
                "AND NOT EXISTS(SELECT 1 FROM work_queue_runs r WHERE r.id=u.run_id AND r.status IN "
                "('PENDING','RUNNING','WAITING_APPROVAL','PAUSED')) "
                "AND NOT EXISTS(SELECT 1 FROM workflow_runs r WHERE r.id=u.run_id "
                "AND r.status IN ('PENDING','RUNNING')) "
                "AND NOT EXISTS(SELECT 1 FROM resource_operations r WHERE r.id=u.run_id AND r.status='RUNNING' "
                "AND r.lease_until>now()))) RETURNING h.call_id",
                (body.actual, body.note, admin["id"], call),
            )
        ).fetchone()
        if not updated:
            fail(409, "MONEY_RECONCILE_CONFLICT", "调用仍在进行、金额已结算或已核对，请先检查运行和供应商账单")
    return {"ok": True, "note": "只核对金额，原Token未知状态保持不变"}


@router.get("/calibration")
async def calibration(request: Request) -> dict:
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT model_id,model_revision,count(*) AS samples,"
            "avg(input_tokens-estimated_input_tokens) AS mean_error,"
            "avg(input_tokens::numeric/nullif(estimated_input_tokens,0)) AS mean_ratio "
            "FROM model_usage WHERE owner_id=%s AND status='KNOWN' AND estimated_input_tokens>0 "
            "GROUP BY model_id,model_revision ORDER BY model_id,model_revision",
            (user["id"],),
        )
    }
