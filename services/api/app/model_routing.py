"""Explicit recipient consent and pre-request selection; no retry of a sent request."""

import hashlib
import json
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import fail
from app.model_capabilities import image_input, native_search_mode
from app.model_catalog import supports_chat
from app.security import administrator, identity

router = APIRouter(prefix="/api/v1/model-routes", tags=["模型路由"])


class RouteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    primary_model: str = Field(min_length=1, max_length=120)
    candidates: list[str] = Field(min_length=1, max_length=6)
    failures: int = Field(default=3, ge=1, le=20)
    cooldown: int = Field(default=60, ge=10, le=3600)
    require_images: bool = False
    require_tools: bool = False
    require_native: bool = False
    enabled: bool = True
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def order(self):
        if len(set(self.candidates)) != len(self.candidates) or self.candidates[0] != self.primary_model:
            raise ValueError("候选不能重复，且第一个必须是主模型")
        return self


class Consent(BaseModel):
    digest: str = Field(min_length=64, max_length=64)


def fingerprint(route, candidates):
    fields = {
        key: route[key]
        for key in (
            "id",
            "revision",
            "candidates",
            "enabled",
            "require_images",
            "require_tools",
            "require_native",
            "failures",
            "cooldown",
        )
    }
    fields["receivers"] = [
        {
            key: row[key]
            for key in (
                "id",
                "revision",
                "base_url",
                "model",
                "kind",
                "protocol",
                "provider",
                "supports_images",
                "supports_tools",
            )
        }
        for row in candidates
    ]
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


async def description(connection, primary: str, *, allow_removed: bool = False):
    row = await (await connection.execute("SELECT * FROM model_routes WHERE primary_model=%s", (primary,))).fetchone()
    if not row:
        fail(422, "ROUTE_UNAVAILABLE", "此主模型尚未配置路由")
    rows = await (
        await connection.execute(
            "SELECT m.id,m.name,m.revision,m.model,m.base_url,m.kind,m.protocol,m.provider,m.supports_images,m.enabled,"
            "c.supports_tools FROM model_configs m LEFT JOIN model_capability_declarations c ON c.model_id=m.id "
            "WHERE m.id=ANY(%s)",
            (row["candidates"],),
        )
    ).fetchall()
    indexed = {item["id"]: item for item in rows}
    if not allow_removed and set(indexed) != set(row["candidates"]):
        fail(409, "ROUTE_MODEL_REMOVED", "路由候选已删除，请管理员更新候选")
    for identifier in set(row["candidates"]) - set(indexed):
        indexed[identifier] = {
            "id": identifier,
            "revision": 0,
            "name": "模型已删除",
            "model": "",
            "base_url": "",
            "kind": "chat",
            "protocol": "openai",
            "provider": "custom",
            "supports_images": None,
            "supports_tools": None,
            "enabled": False,
            "removed": True,
        }
    candidates = [indexed[identifier] for identifier in row["candidates"]]
    return row, candidates, fingerprint(row, candidates)


async def pin(database, owner: UUID, primary: str) -> dict:
    async with database.pool.connection() as connection:
        route, candidates, digest = await description(connection, primary)
        consent = await (
            await connection.execute(
                "SELECT 1 FROM model_route_consents WHERE owner_id=%s AND route_id=%s AND digest=%s",
                (owner, route["id"], digest),
            )
        ).fetchone()
        if not route["enabled"] or not consent:
            fail(403, "ROUTE_CONSENT_REQUIRED", "请先在模型路由页面确认当前候选服务与数据接收方")
        return {"id": str(route["id"]), "digest": digest}


async def freeze(database, owner, selection):
    if not selection.get("model_routing"):
        return selection
    current = await pin(database, owner, selection["model_id"])
    if selection.get("route_pin", current) != current:
        fail(409, "ROUTE_CHANGED", "路由或候选配置已变化，请重新确认并发布智能体")
    return {**selection, "route_pin": current}


async def healthy(connection, model, route, selection):
    # Compare start times: a late response from an older call cannot erase newer failures.
    health = await (
        await connection.execute(
            "WITH latest AS (SELECT max(created_at) AS success FROM model_usage WHERE model_id=%s "
            "AND model_revision=%s AND status='KNOWN') "
            "SELECT count(*) AS n,max(u.finished_at) AS failed,"
            "max(u.finished_at)+make_interval(secs=>%s)>now() AS cooling FROM model_usage u,latest l "
            "WHERE u.model_id=%s AND u.model_revision=%s AND u.status='UNKNOWN' AND u.failure_code IN "
            "('MODEL_REQUEST_FAILED','MODEL_REQUEST_TIMEOUT','USAGE_NOT_REPORTED') "
            "AND u.created_at>coalesce(l.success,'-infinity'::timestamptz)",
            (model["id"], model["revision"], route["cooldown"], model["id"], model["revision"]),
        )
    ).fetchone()
    if health["n"] < route["failures"]:
        return True, "正常"
    if health["cooling"]:
        return False, "故障冷却中"
    probe = await (
        await connection.execute(
            "INSERT INTO model_route_probes(model_id,model_revision,selection_id,until_at) "
            "VALUES(%s,%s,%s,now()+interval '3 minutes') ON CONFLICT(model_id,model_revision) "
            "DO UPDATE SET selection_id=excluded.selection_id,until_at=excluded.until_at "
            "WHERE model_route_probes.until_at<now() RETURNING selection_id",
            (model["id"], model["revision"], selection),
        )
    ).fetchone()
    return bool(probe), "限流恢复探测" if probe else "其他运行正在恢复探测"


async def resolve(
    models,
    owner,
    run,
    primary,
    *,
    enabled=False,
    scope="lead",
    expected=None,
    images=False,
    allow_vision=False,
    tools=False,
    native=False,
    openai_native=False,
    connection=None,
):
    if not enabled:
        return await models.get(primary)
    if connection is None:
        async with models.database.pool.connection() as current:
            return await resolve(
                models,
                owner,
                run,
                primary,
                enabled=True,
                scope=scope,
                expected=expected,
                images=images,
                allow_vision=allow_vision,
                tools=tools,
                native=native,
                openai_native=openai_native,
                connection=current,
            )
    await connection.execute("SELECT pg_advisory_xact_lock(710031,hashtext(%s))", (str(run) + scope,))
    route, candidates, digest = await description(connection, primary)
    consent = await (
        await connection.execute(
            "SELECT 1 FROM model_route_consents WHERE owner_id=%s AND route_id=%s AND digest=%s",
            (owner, route["id"], digest),
        )
    ).fetchone()
    if not route["enabled"] or not consent:
        fail(403, "ROUTE_CONSENT_REQUIRED", "尚未同意当前候选模型接收上下文，请先确认路由")
    if expected and expected != {"id": str(route["id"]), "digest": digest}:
        fail(409, "ROUTE_CHANGED", "固定的路由配置已变化，未发送模型请求")
    existing = await (
        await connection.execute(
            "SELECT * FROM model_route_selections WHERE owner_id=%s AND run_id=%s AND scope=%s", (owner, run, scope)
        )
    ).fetchone()
    if existing:
        if existing["digest"] != digest:
            fail(409, "ROUTE_CHANGED", "运行已固定的路由发生变化，请创建新运行")
        config = await models.get(existing["model_id"])
        return config.model_copy(update={"route_selection_id": existing["id"]})
    selected, reasons, identifier = None, [], uuid4()
    for candidate in candidates:
        config = SimpleNamespace(**candidate)
        reason = None
        if not candidate["enabled"] or not (
            supports_chat(config.kind, config.protocol)
            or (allow_vision and config.kind == "vision" and config.protocol == "openai")
        ):
            reason = "模型停用或聊天协议不可用"
        elif (images or route["require_images"]) and not image_input(config):
            reason = "不支持图片输入"
        elif (tools or route["require_tools"]) and not candidate["supports_tools"]:
            reason = "尚未明确声明工具调用能力"
        elif (native or route["require_native"]) and not native_search_mode(config):
            reason = "未适配原生联网"
        elif openai_native and (config.provider != "openai" or config.protocol != "openai"):
            reason = "不满足智能体的OpenAI原生联网授权"
        else:
            valid, state = await healthy(connection, candidate, route, identifier)
            if valid:
                selected = candidate
                reasons.append({"model": candidate["name"], "reason": state})
                break
            reason = state
        reasons.append({"model": candidate["name"], "reason": reason})
    if selected is None:
        fail(422, "ROUTE_NO_CANDIDATE", "没有符合能力和冷却条件的已同意模型，未发送请求")
    await connection.execute(
        "INSERT INTO model_route_selections(id,owner_id,run_id,scope,route_id,digest,model_id,model_revision,reason) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (identifier, owner, run, scope, route["id"], digest, selected["id"], selected["revision"], Jsonb(reasons)),
    )
    config = await models.get(selected["id"])
    if config.revision != selected["revision"]:
        fail(409, "ROUTE_CHANGED", "选择期间模型配置发生变化，请重试新运行")
    return config.model_copy(update={"route_selection_id": identifier})


async def authorize_selection(connection, owner, run, identifier, model, revision):
    if not identifier:
        return
    row = await (
        await connection.execute(
            "SELECT s.*,r.primary_model FROM model_route_selections s JOIN model_routes r ON r.id=s.route_id "
            "WHERE s.id=%s AND s.owner_id=%s AND s.run_id=%s AND s.model_id=%s AND s.model_revision=%s",
            (identifier, owner, run, model, revision),
        )
    ).fetchone()
    if not row:
        fail(403, "ROUTE_SELECTION_INVALID", "实际模型与运行路由不匹配")
    route, _, digest = await description(connection, row["primary_model"])
    consent = await (
        await connection.execute(
            "SELECT 1 FROM model_route_consents WHERE route_id=%s AND owner_id=%s AND digest=%s",
            (route["id"], owner, digest),
        )
    ).fetchone()
    if digest != row["digest"] or not route["enabled"] or not consent:
        fail(409, "ROUTE_CHANGED", "候选或同意已变化，本次运行停止，不自动重新选模型")


@router.get("")
async def listing(request: Request):
    user = await identity(request)
    db = request.app.state.db
    rows = await db.fetch("SELECT primary_model FROM model_routes ORDER BY name,id")
    items = []
    async with db.pool.connection() as connection:
        for row in rows:
            route, candidates, digest = await description(connection, row["primary_model"], allow_removed=True)
            consent = await (
                await connection.execute(
                    "SELECT digest FROM model_route_consents WHERE owner_id=%s AND route_id=%s",
                    (user["id"], route["id"]),
                )
            ).fetchone()
            # Only sanitized receiver origin is exposed, never path, query or credentials.
            receivers = [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "revision": c["revision"],
                    "receiver": "已删除，无可用服务地址"
                    if c.get("removed")
                    else f"{urlsplit(c['base_url']).scheme}://{urlsplit(c['base_url']).netloc}",
                }
                for c in candidates
            ]
            items.append(
                {
                    **route,
                    "unavailable": any(c.get("removed") for c in candidates),
                    "receivers": receivers,
                    "digest": digest,
                    "consented": bool(consent and consent["digest"] == digest),
                }
            )
    return {"items": items}


@router.put("/{identifier}")
async def save(identifier: UUID, body: RouteInput, request: Request):
    await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710015)")
        models = await (
            await connection.execute("SELECT id FROM model_configs WHERE id=ANY(%s)", (body.candidates,))
        ).fetchall()
        if len(models) != len(body.candidates):
            fail(422, "ROUTE_MODEL_UNAVAILABLE", "请选择存在的候选模型")
        current = await (
            await connection.execute("SELECT revision FROM model_routes WHERE id=%s FOR UPDATE", (identifier,))
        ).fetchone()
        if current and current["revision"] != body.revision:
            fail(409, "ROUTE_CONFLICT", "路由已修改，请刷新")
        conflict = await (
            await connection.execute(
                "SELECT id FROM model_routes WHERE primary_model=%s AND id<>%s", (body.primary_model, identifier)
            )
        ).fetchone()
        if conflict:
            fail(409, "ROUTE_PRIMARY_CONFLICT", "一个主模型只能配置一条路由")
        await connection.execute(
            "INSERT INTO model_routes(id,name,primary_model,candidates,failures,cooldown,require_images,"
            "require_tools,require_native,enabled) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,primary_model=excluded.primary_model,"
            "candidates=excluded.candidates,failures=excluded.failures,cooldown=excluded.cooldown,"
            "require_images=excluded.require_images,require_tools=excluded.require_tools,"
            "require_native=excluded.require_native,enabled=excluded.enabled,revision=model_routes.revision+1,"
            "updated_at=now()",
            (
                identifier,
                body.name,
                body.primary_model,
                Jsonb(body.candidates),
                body.failures,
                body.cooldown,
                body.require_images,
                body.require_tools,
                body.require_native,
                body.enabled,
            ),
        )
    return {"id": identifier}


@router.post("/{identifier}/consent")
async def consent(identifier: UUID, body: Consent, request: Request):
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        rows = await (
            await connection.execute("SELECT primary_model FROM model_routes WHERE id=%s FOR SHARE", (identifier,))
        ).fetchone()
        if not rows:
            fail(404, "NOT_FOUND", "路由不存在")
        route, _, digest = await description(connection, rows["primary_model"])
        if digest != body.digest or not route["enabled"]:
            fail(409, "ROUTE_CHANGED", "路由已变化，请刷新接收方后重新同意")
        await connection.execute(
            "INSERT INTO model_route_consents(owner_id,route_id,digest) VALUES(%s,%s,%s) "
            "ON CONFLICT(owner_id,route_id) DO UPDATE SET digest=excluded.digest,accepted_at=now()",
            (user["id"], identifier, digest),
        )
    return {"ok": True}


@router.delete("/{identifier}/consent")
async def revoke(identifier: UUID, request: Request):
    user = await identity(request)
    await request.app.state.db.execute(
        "DELETE FROM model_route_consents WHERE owner_id=%s AND route_id=%s", (user["id"], identifier)
    )
    return {"ok": True}


@router.get("/runs/{identifier}")
async def selections(identifier: UUID, request: Request):
    user = await identity(request)
    from app.usage_api import work_run_ids

    identifiers = await work_run_ids(request.app.state.db, user["id"], identifier)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT s.id,s.scope,s.model_id,s.model_revision,s.reason,s.created_at,"
            "coalesce(m.name,s.reason->-1->>'model') AS name "
            "FROM model_route_selections s "
            "LEFT JOIN model_configs m ON m.id=s.model_id WHERE s.owner_id=%s "
            "AND s.run_id=ANY(%s::uuid[]) ORDER BY s.created_at",
            (user["id"], identifiers),
        )
    }
