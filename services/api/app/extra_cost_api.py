"""Immutable non-token prices and owner-scoped bill comparison, never rewrite token usage."""

import csv
import hashlib
import io
import json
from datetime import datetime
from decimal import ROUND_UP, Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import fail
from app.list_pagination import decode_cursor, page
from app.security import administrator, identity

router = APIRouter(prefix="/api/v1/operations/extra-costs", tags=["额外费用与对账"])


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model_id: str = Field(min_length=1, max_length=120)
    item: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    unit: Literal["call", "image", "audio_second", "video_second"]
    currency: Literal["CNY", "USD"]
    unit_price: Decimal = Field(ge=0, le=1000000, max_digits=24, decimal_places=9, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=500)
    effective_at: datetime


class Charge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: UUID
    price_id: UUID
    quantity: Decimal | None = Field(
        default=None, ge=0, le=10**12, max_digits=24, decimal_places=9, allow_inf_nan=False
    )


@router.get("/models")
async def fee_models(request: Request):
    user = await identity(request)
    # Billing metadata must include media; the chat selector deliberately excludes these types.
    rows = await request.app.state.db.fetch(
        "SELECT m.id,m.name,m.kind,m.enabled FROM model_configs m WHERE %s OR EXISTS("
        "SELECT 1 FROM model_usage u WHERE u.owner_id=%s AND u.model_id=m.id) ORDER BY m.name,m.id",
        (user["is_admin"], user["id"]),
    )
    return {"items": rows}


@router.get("/prices")
async def prices(request: Request, before: str | None = None):
    user = await identity(request)
    timestamp, identifier = decode_cursor(before, user["id"], "extra-prices")
    rows = await request.app.state.db.fetch(
        "SELECT * FROM extra_cost_prices WHERE (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s)) "
        "ORDER BY created_at DESC,id DESC LIMIT 101",
        (timestamp, timestamp, identifier),
    )
    return page(rows, 100, user["id"], "extra-prices")


@router.post("/prices", status_code=201)
async def add_price(body: Price, request: Request):
    await administrator(request)
    if body.effective_at.tzinfo is None:
        fail(422, "PRICE_TIME_INVALID", "价格生效时间需要时区")
    models = await request.app.state.db.fetch("SELECT 1 FROM model_configs WHERE id=%s", (body.model_id,))
    if not models:
        fail(404, "MODEL_NOT_FOUND", "模型不存在")
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("extra-price:" + body.model_id + ":" + body.item,)
        )
        old = await (
            await connection.execute(
                "SELECT unit,currency FROM extra_cost_prices WHERE model_id=%s AND item=%s LIMIT 1",
                (body.model_id, body.item),
            )
        ).fetchone()
        if old and (old["unit"] != body.unit or old["currency"] != body.currency):
            fail(422, "PRICE_ITEM_UNIT_FIXED", "同一模型计费项的单位和币种不可改变，请新增不同计费项")
        return await (
            await connection.execute(
                "INSERT INTO extra_cost_prices(id,model_id,item,unit,currency,unit_price,source,effective_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    uuid4(),
                    body.model_id,
                    body.item,
                    body.unit,
                    body.currency,
                    body.unit_price,
                    body.source,
                    body.effective_at,
                ),
            )
        ).fetchone()


async def record_charge(db, owner, call_id, price_id, quantity, *, source="provider"):
    """Provider adapters call only with explicitly reported quantities; None stays unknown."""
    async with db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(owner),))
        row = await (
            await connection.execute(
                "SELECT u.model_id,u.created_at,p.* FROM model_usage u CROSS JOIN extra_cost_prices p "
                "WHERE u.call_id=%s AND u.owner_id=%s AND p.id=%s AND p.model_id=u.model_id "
                "AND p.effective_at<=u.created_at",
                (call_id, owner, price_id),
            )
        ).fetchone()
        if not row:
            fail(422, "EXTRA_PRICE_UNAVAILABLE", "调用不属于本人，或价格与调用模型/时间不匹配")
        amount = (
            (quantity * row["unit_price"]).quantize(Decimal(".000000001"), rounding=ROUND_UP)
            if quantity is not None
            else None
        )
        result = await (
            await connection.execute(
                "INSERT INTO extra_cost_usage(id,call_id,owner_id,item,price_id,quantity,amount,source) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(call_id,item) DO NOTHING RETURNING *",
                (uuid4(), call_id, owner, row["item"], price_id, quantity, amount, source),
            )
        ).fetchone()
        if result:
            return result
        existing = await (
            await connection.execute(
                "SELECT * FROM extra_cost_usage WHERE call_id=%s AND item=%s", (call_id, row["item"])
            )
        ).fetchone()
        if existing["price_id"] != price_id or existing["source"] != source:
            fail(409, "EXTRA_CHARGE_CONFLICT", "此调用计费项已记录，不能改写历史用量或价格")
        if (
            source == "provider"
            and existing["quantity"] is None
            and quantity is not None
            and existing["confirmed_at"] is None
        ):
            updated = await (
                await connection.execute(
                    "UPDATE extra_cost_usage SET quantity=%s,amount=%s WHERE id=%s RETURNING *",
                    (quantity, amount, existing["id"]),
                )
            ).fetchone()
            await connection.execute(
                "UPDATE extra_cost_holds SET actual=%s WHERE call_id=%s AND item=%s", (amount, call_id, row["item"])
            )
            return updated
        if existing["quantity"] != quantity:
            fail(409, "EXTRA_CHARGE_CONFLICT", "已确认的实际用量不能被不同回调覆盖")
        return existing


@router.post("/usage", status_code=201)
async def add_charge(body: Charge, request: Request):
    user = await administrator(request)
    return await record_charge(
        request.app.state.db, user["id"], body.call_id, body.price_id, body.quantity, source="manual"
    )


@router.get("/usage")
async def usage(request: Request):
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT u.*,p.currency,p.unit,p.model_id FROM extra_cost_usage u JOIN extra_cost_prices p ON p.id=u.price_id "
        "WHERE u.owner_id=%s ORDER BY u.created_at DESC,u.id DESC LIMIT 500",
        (user["id"],),
    )
    return {"items": rows, "note": "只列额外费用，不重复计算Token；manual为人工记录，不是供应商上报。未知数量不计免费"}


class BillLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    call_id: UUID
    item: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    currency: Literal["CNY", "USD"]
    amount: Decimal = Field(ge=0, le=10**15, max_digits=24, decimal_places=9, allow_inf_nan=False)
    billed_at: datetime | None = None


class Bill(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(default="账单", min_length=1, max_length=120)
    rows: list[BillLine] = Field(min_length=1, max_length=1000)
    period_start: datetime | None = None
    period_end: datetime | None = None
    model_ids: list[str] = Field(default_factory=list, max_length=50)
    currency: Literal["CNY", "USD"] | None = None

    @model_validator(mode="after")
    def period_valid(self):
        if any(not 1 <= len(model) <= 120 for model in self.model_ids):
            raise ValueError("模型标识长度无效")
        if bool(self.period_start) != bool(self.period_end):
            raise ValueError("账期开始和结束需同时提供")
        if self.period_start:
            if not self.period_start.tzinfo or not self.period_end.tzinfo:
                raise ValueError("账期需要明确时区")
            seconds = (self.period_end - self.period_start).total_seconds()
            if not 0 < seconds <= 31 * 86400 or not self.model_ids or not self.currency:
                raise ValueError("账期须在31天内，并明确模型范围和币种")
        for row in self.rows:
            if row.billed_at and not row.billed_at.tzinfo:
                raise ValueError("账单时间需要时区")
            if self.period_start and (
                not row.billed_at
                or not self.period_start <= row.billed_at < self.period_end
                or row.currency != self.currency
            ):
                raise ValueError("账单行需有范围内带时区时间及同账期币种")
        return self


async def compare_bill(db, owner, bill):
    keys = [(str(row.call_id), row.item) for row in bill.rows]
    if len(keys) != len(set(keys)):
        fail(422, "DUPLICATE_BILL_LINE", "账单内调用ID与计费项不能重复")
    if bill.period_start:
        calls = await db.fetch(
            "SELECT mu.call_id,mu.model_id,mu.created_at,u.item,p.currency FROM model_usage mu "
            "LEFT JOIN extra_cost_usage u ON u.call_id=mu.call_id AND u.owner_id=mu.owner_id "
            "LEFT JOIN extra_cost_prices p ON p.id=u.price_id "
            "WHERE mu.owner_id=%s AND mu.call_id=ANY(%s::uuid[])",
            (owner, [row.call_id for row in bill.rows]),
        )
        submitted = set(keys)
        for call in calls:
            if (call["model_id"] not in bill.model_ids
                    or not bill.period_start <= call["created_at"] < bill.period_end
                    or ((str(call["call_id"]), call["item"]) in submitted
                        and call["currency"] != bill.currency)):
                fail(422, "BILL_SCOPE_MISMATCH", "本人已知调用的模型、币种或实际发生时间不在声明账期范围内")
    rows = await db.fetch(
        "SELECT u.call_id,u.item,u.amount,p.currency FROM extra_cost_usage u "
        "JOIN extra_cost_prices p ON p.id=u.price_id "
        "JOIN model_usage mu ON mu.call_id=u.call_id "
        "WHERE u.owner_id=%s AND ((%s::timestamptz IS NULL AND u.call_id=ANY(%s::uuid[])) "
        "OR (%s::timestamptz IS NOT NULL "
        "AND mu.created_at>=%s AND mu.created_at<%s AND p.model_id=ANY(%s::text[]) AND p.currency=%s))",
        (
            owner,
            bill.period_start,
            [r.call_id for r in bill.rows],
            bill.period_start,
            bill.period_start,
            bill.period_end,
            bill.model_ids,
            bill.currency,
        ),
    )
    known = {(str(row["call_id"]), row["item"]): row for row in rows}
    report = []
    for row in bill.rows:
        existing = known.pop((str(row.call_id), row.item), None)
        status = "platform_missing"
        if existing:
            status = (
                "unknown"
                if existing["amount"] is None
                else "currency_mismatch"
                if existing["currency"] != row.currency
                else "match"
                if existing["amount"] == row.amount
                else "difference"
            )
        report.append(
            {**row.model_dump(), "status": status, "platform_amount": existing["amount"] if existing else None}
        )
    report.extend(
        {
            "call_id": row["call_id"],
            "item": row["item"],
            "currency": row["currency"],
            "amount": None,
            "platform_amount": row["amount"],
            "status": "provider_missing",
        }
        for row in known.values()
    )
    return jsonable_encoder(report, custom_encoder={Decimal: str})


@router.post("/bills/preview")
async def preview(body: Bill, request: Request):
    user = await identity(request)
    return {
        "items": await compare_bill(request.app.state.db, user["id"], body),
        "scope": ("指定模型/币种/调用发生期（不按费用补录时间）"
                  if body.period_start else "提交调用ID范围，不代表完整供应商账期"),
        "period_start": body.period_start,
        "period_end": body.period_end,
        "note": "预览不修改账本或价格",
    }


@router.post("/bills/import", status_code=201)
async def import_bill(body: Bill, request: Request):
    user = await identity(request)
    normalized = [
        {**row.model_dump(mode="json"), "amount": format(row.amount.normalize(), "f")}
        for row in sorted(body.rows, key=lambda r: (str(r.call_id), r.item))
    ]
    scope = {
        "rows": normalized,
        "period_start": body.period_start.isoformat() if body.period_start else None,
        "period_end": body.period_end.isoformat() if body.period_end else None,
        "model_ids": sorted(set(body.model_ids)),
        "currency": body.currency,
    }
    digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    report = await compare_bill(request.app.state.db, user["id"], body)
    return (
        await request.app.state.db.fetch(
            "INSERT INTO extra_cost_bill_imports(id,owner_id,digest,name,report,scope) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(owner_id,digest) DO UPDATE SET digest=EXCLUDED.digest RETURNING *",
            (uuid4(), user["id"], digest, body.name, Jsonb(report), Jsonb(scope)),
        )
    )[0]


@router.get("/bills")
async def bills(request: Request, before: str | None = None):
    user = await identity(request)
    timestamp, identifier = decode_cursor(before, user["id"], "extra-bills")
    rows = await request.app.state.db.fetch(
        "SELECT id,name,created_at FROM extra_cost_bill_imports WHERE owner_id=%s "
        "AND (%s::timestamptz IS NULL OR (created_at,id)<(%s,%s)) ORDER BY created_at DESC,id DESC LIMIT 101",
        (user["id"], timestamp, timestamp, identifier),
    )
    return page(rows, 100, user["id"], "extra-bills")


class Reconcile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    quantity: Decimal = Field(ge=0, le=10**12, max_digits=24, decimal_places=9, allow_inf_nan=False)
    note: str = Field(min_length=1, max_length=500)


@router.post("/usage/{identifier}/reconcile")
async def reconcile(identifier: UUID, body: Reconcile, request: Request):
    user = await administrator(request)
    async with request.app.state.db.pool.connection() as c:
        rows = await (
            await c.execute("SELECT * FROM extra_cost_usage WHERE id=%s AND owner_id=%s", (identifier, user["id"]))
        ).fetchone()
        if not rows:
            fail(404, "EXTRA_USAGE_NOT_FOUND", "费用记录不存在或无权核对")
        await c.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(user["id"]),))
        price = await (await c.execute("SELECT * FROM extra_cost_prices WHERE id=%s", (rows["price_id"],))).fetchone()
        amount = (body.quantity * price["unit_price"]).quantize(Decimal(".000000001"), rounding=ROUND_UP)
        updated = await (
            await c.execute(
                "UPDATE extra_cost_usage SET quantity=%s,amount=%s,confirmed_by=%s,"
                "confirmed_at=now(),confirmation_note=%s "
                "WHERE id=%s AND quantity IS NULL AND confirmed_at IS NULL RETURNING *",
                (body.quantity, amount, user["id"], body.note, identifier),
            )
        ).fetchone()
        if not updated:
            fail(409, "EXTRA_RECONCILE_CONFLICT", "费用已上报或已核对，不能再次修改")
        await c.execute(
            "UPDATE extra_cost_holds SET actual=%s WHERE call_id=%s AND item=%s",
            (amount, rows["call_id"], rows["item"]),
        )
        return updated


@router.get("/bills/{identifier}/download")
async def download(identifier: UUID, request: Request):
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT report FROM extra_cost_bill_imports WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "BILL_NOT_FOUND", "账单不存在或无权访问")
    output = io.StringIO()
    fields = ["call_id", "item", "currency", "amount", "platform_amount", "status"]
    writer = csv.writer(output)
    writer.writerow(fields)
    for row in rows[0]["report"]:
        values = [str(row.get(field) or "") for field in fields]
        writer.writerow(["'" + v if v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="bill-comparison.csv"'},
    )


@router.post("/bills/parse")
async def parse_bill(
    request: Request,
    file: UploadFile,
    mapping: str = Form("{}", max_length=64000),
    request_ids: str = Form("{}", max_length=64000),
):
    await identity(request)
    data = await file.read(2 * 1024 * 1024 + 1)
    if not data or len(data) > 2 * 1024 * 1024:
        fail(413, "BILL_SIZE_INVALID", "账单不能为空且不能超过2MiB")
    try:
        text = data.decode("utf-8-sig")
        if text.lstrip().startswith("["):
            rows = json.loads(text)
            if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
                raise ValueError("invalid rows")
            columns = list(rows[0])
        else:
            reader = csv.DictReader(io.StringIO(text))
            columns = reader.fieldnames or []
            if len(columns) != len(set(columns)):
                raise ValueError("duplicate headers")
            rows = list(reader)
        if not rows or len(rows) > 1000 or len(columns) > 30 or any(not 1 <= len(c) <= 120 for c in columns):
            raise ValueError("row limit")
        fields = json.loads(mapping)
        identifiers = json.loads(request_ids)
        if not isinstance(fields, dict) or not isinstance(identifiers, dict) or len(identifiers) > 1000:
            raise ValueError("invalid mapping")
        required = {"call_id", "item", "currency", "amount"}
        if not fields:
            if not required.issubset(columns):
                return {"rows": [], "columns": columns, "needs_mapping": True, "note": "请映射账单字段后再预览"}
            fields = {key: key for key in required}
            if "billed_at" in columns:
                fields["billed_at"] = "billed_at"
        if (
            not required.issubset(fields)
            or set(fields) - required - {"billed_at"}
            or any(not isinstance(value, str) or value not in columns for value in fields.values())
        ):
            raise ValueError("invalid mapping")
        mapped = []
        for row in rows:
            values = {key: row.get(column) for key, column in fields.items()}
            value = str(values["call_id"])
            values["call_id"] = identifiers.get(value, value)
            mapped.append(values)
        bill = Bill(rows=mapped)
    except (ValueError, TypeError, UnicodeError, csv.Error):
        fail(422, "BILL_FORMAT_INVALID", "请选择标准字段call_id/item/currency/amount的UTF-8 CSV或JSON数组")
    return {"rows": bill.rows, "columns": columns, "needs_mapping": False, "note": "已校验字段映射；尚未导入或改写账本"}
