"""Administrator endpoints for persistent model settings and bounded connection probes."""

import asyncio

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation

from app.errors import fail
from app.knowledge import embed_texts, rerank_texts
from app.model_catalog import KINDS, PROTOCOLS, PROVIDERS, probe_hint
from app.model_compatibility import matrix
from app.models import ModelInput
from app.security import administrator

router = APIRouter(prefix="/api/v1/admin/models", tags=["模型管理"])


@router.get("/catalog")
async def catalog(request: Request) -> dict:
    """Supply supported purposes, providers and protocols without any credentials."""
    await administrator(request)
    return {"kinds": KINDS, "providers": PROVIDERS, "protocols": PROTOCOLS, "compatibility": matrix()}


@router.get("")
async def listing(request: Request) -> dict:
    """Return editable metadata and credential-presence flags to administrators."""
    await administrator(request)
    return {"items": await request.app.state.models.list()}


@router.post("", status_code=201)
async def create(body: ModelInput, request: Request) -> dict:
    """Persist a configured model without exposing its provider credential."""
    await administrator(request)
    try:
        identifier = await request.app.state.models.save(body)
    except UniqueViolation:
        fail(409, "MODEL_NAME_CONFLICT", "显示名称已存在，请使用其他名称")
    return {"id": identifier}


@router.put("/{identifier}")
async def update(identifier: str, body: ModelInput, request: Request) -> dict:
    """Edit a model while preserving a stored key when the password field is blank."""
    await administrator(request)
    try:
        await request.app.state.models.save(body, identifier)
    except UniqueViolation:
        fail(409, "MODEL_NAME_CONFLICT", "显示名称已存在，请使用其他名称")
    return {"id": identifier}


@router.post("/{identifier}/test")
async def probe(identifier: str, request: Request) -> dict:
    """Send one small, time-bounded test using the server-stored credential."""
    await administrator(request)
    registry = request.app.state.models
    rows = await registry.database.fetch("SELECT revision FROM model_configs WHERE id=%s", (identifier,))
    if not rows:
        fail(404, "NOT_FOUND", "模型不存在")
    config = await registry.get(identifier, enabled_only=False)
    unsupported = probe_hint(config.kind, config.protocol)
    if unsupported:
        fail(422, "MODEL_PROBE_UNSUPPORTED", unsupported)
    try:
        async with asyncio.timeout(20):
            if config.kind == "embedding":
                success = bool(await embed_texts(config, ["模型连接测试"]))
            elif config.kind == "rerank":
                success = bool(await rerank_texts(config, "产品保修多久", ["产品保修三年", "今日天气晴朗"]))
            else:
                model = request.app.state.runtime.model_factory(config.model_copy(update={"max_output_tokens": 16}))
                response = await model.ainvoke("请只回复 OK，这是模型连接测试。")
                success = bool(response.content)
    except Exception:
        success = False
    status = "available" if success else "unavailable"
    changed = await registry.database.fetch(
        "UPDATE model_configs SET connection_status=%s,checked_at=now() WHERE id=%s AND revision=%s RETURNING id",
        (status, identifier, rows[0]["revision"]),
    )
    if not changed:
        fail(409, "MODEL_REVISION_CONFLICT", "测试期间配置已更改，请重新测试")
    return {
        "success": success,
        "message": (
            "连接成功，已生成文本向量"
            if config.kind == "embedding"
            else "连接成功，已收到重排结果"
            if config.kind == "rerank"
            else "连接成功，已收到文本回答"
        )
        if success
        else "连接失败，请检查服务地址、模型名称、密钥和网络；配置已保留",
    }


@router.delete("/{identifier}")
async def delete(identifier: str, request: Request) -> dict:
    """Remove future availability without touching historical model references."""
    await administrator(request)
    changed = await request.app.state.db.fetch("DELETE FROM model_configs WHERE id=%s RETURNING id", (identifier,))
    if not changed:
        fail(404, "NOT_FOUND", "模型不存在")
    return {"ok": True}
