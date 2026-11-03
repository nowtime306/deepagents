"""Owner-scoped audit and usage views, with administrator-entered estimate rates."""

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.security import administrator, identity

router = APIRouter(prefix="/api/v1/operations", tags=["运营管理"])


class Rates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_per_million: Decimal = Field(ge=0, le=1000000, max_digits=18, decimal_places=6)
    output_per_million: Decimal = Field(ge=0, le=1000000, max_digits=18, decimal_places=6)
    currency: Literal["CNY", "USD"] = "CNY"


@router.get("/rates")
async def rates(request: Request) -> dict:
    await identity(request)
    return {"items": await request.app.state.db.fetch("SELECT * FROM model_cost_rates ORDER BY model_id")}


@router.put("/rates/{model_id}")
async def save_rate(model_id: str, body: Rates, request: Request) -> dict:
    await administrator(request)
    rows = await request.app.state.db.fetch("SELECT id FROM model_configs WHERE id=%s", (model_id,))
    if not rows:
        fail(404, "MODEL_NOT_FOUND", "模型不存在")
    await request.app.state.db.execute(
        "INSERT INTO model_cost_rates(model_id,input_per_million,output_per_million,currency) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT(model_id) DO UPDATE SET input_per_million=excluded.input_per_million,"
        "output_per_million=excluded.output_per_million,currency=excluded.currency,updated_at=now()",
        (model_id, body.input_per_million, body.output_per_million, body.currency),
    )
    return {"ok": True}


@router.get("/usage")
async def usage(request: Request) -> dict:
    user = await identity(request)
    items = await request.app.state.db.fetch(
        "SELECT call_id,run_id,mode,model_id,model_revision,status,input_tokens,output_tokens,"
        "estimated_cost,currency,created_at,finished_at,cache_read_tokens,cache_creation_tokens,reasoning_tokens,"
        "estimated_input_tokens,output_token_reserve,failure_code,price_version_id,cost_status,"
        "estimate_method,route_selection_id "
        "FROM model_usage WHERE owner_id=%s ORDER BY created_at DESC,call_id LIMIT 200",
        (user["id"],),
    )
    summary = await request.app.state.db.fetch(
        "SELECT currency,sum(estimated_cost) AS estimated_cost,sum(input_tokens) AS input_tokens,"
        "sum(output_tokens) AS output_tokens,count(*) AS calls,"
        "count(*) FILTER(WHERE status<>'KNOWN') AS unknown_calls,"
        "count(*) FILTER(WHERE estimated_cost IS NULL) AS unpriced_calls "
        "FROM model_usage WHERE owner_id=%s GROUP BY currency",
        (user["id"],),
    )
    return {"items": items, "summary": summary}


@router.get("/audit")
async def audit(request: Request, before: int | None = None) -> dict:
    user = await identity(request)
    items = await request.app.state.db.fetch(
        "SELECT id,method,resource_path,status_code,created_at FROM audit_events "
        "WHERE actor_id=%s AND (%s::bigint IS NULL OR id<%s) ORDER BY id DESC LIMIT 101",
        (user["id"], before, before),
    )
    return {"items": items[:100], "next_before": items[99]["id"] if len(items) > 100 else None}
