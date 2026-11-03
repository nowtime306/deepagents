"""Version-scoped bearer credentials and bounded, idempotent durable admission."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.security import identity, token_digest
from app.workflow_definition import Definition
from app.workflow_runtime import digest

router = APIRouter(tags=["工作流发布 API"])


class KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    version_id: UUID
    valid_days: int = Field(default=30, ge=1, le=365)
    rpm: int = Field(default=10, ge=1, le=120)
    concurrency: int = Field(default=1, ge=1, le=4)


class InvokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)


@router.get("/api/v1/api-keys")
async def keys(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT k.id,k.name,k.token_hint,k.expires_at,k.revoked_at,k.rpm,k.concurrency,k.version_id,"
        "COALESCE(v.version,0) AS version,COALESCE(v.name,'已删除工作流') AS workflow_name "
        "FROM api_credentials k LEFT JOIN workflow_versions v ON v.id=k.version_id "
        "WHERE k.owner_id=%s AND NOT k.internal_only ORDER BY k.created_at DESC LIMIT 200",
        (user["id"],),
    )
    return {"items": rows}


@router.post("/api/v1/api-keys", status_code=201)
async def create_key(body: KeyInput, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT v.id,COALESCE(v.definition->>'execution','readonly') AS execution "
        "FROM workflow_versions v JOIN workflows w ON w.id=v.workflow_id "
        "WHERE v.id=%s AND w.owner_id=%s AND v.active AND v.digest<>''",
        (body.version_id, user["id"]),
    )
    if not rows:
        fail(403, "VERSION_UNAVAILABLE", "只能授权自己的已测试且上线的发布版本")
    if rows[0]["execution"] != "readonly":
        fail(422, "WORKFLOW_WORK_REQUIRED", "持久任务流程需要本人审批，不能通过只读API Key运行")
    token = "oa_" + secrets.token_urlsafe(32)
    identifier = uuid4()
    await request.app.state.db.execute(
        "INSERT INTO api_credentials(id,owner_id,name,version_id,token_hash,token_hint,expires_at,rpm,concurrency) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identifier,
            user["id"],
            body.name,
            body.version_id,
            token_digest(token),
            token[-6:],
            datetime.now(UTC) + timedelta(days=body.valid_days),
            body.rpm,
            body.concurrency,
        ),
    )
    return {"id": identifier, "key": token}


@router.delete("/api/v1/api-keys/{identifier}")
async def revoke(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "UPDATE api_credentials SET revoked_at=COALESCE(revoked_at,now()) WHERE id=%s AND owner_id=%s RETURNING id",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "KEY_NOT_FOUND", "Key 不存在或无权访问")
    return {"ok": True}


async def credential(request: Request) -> dict:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 200:
        fail(401, "KEY_REQUIRED", "需要有效的 Bearer Key")
    rows = await request.app.state.db.fetch(
        "SELECT * FROM api_credentials WHERE token_hash=%s AND NOT internal_only AND revoked_at IS NULL "
        "AND expires_at>now()",
        (token_digest(authorization[7:]),),
    )
    if not rows:
        fail(401, "KEY_INVALID", "Key 无效、已到期或已撤销")
    from app.permissions import require_account

    await require_account(request.app.state.db, rows[0]["owner_id"], "workflows.execute")
    return rows[0]


async def published(request: Request, key: dict) -> dict:
    rows = await request.app.state.db.fetch(
        "SELECT v.*,w.owner_id FROM workflow_versions v JOIN workflows w ON w.id=v.workflow_id "
        "WHERE v.id=%s AND w.owner_id=%s AND v.active",
        (key["version_id"], key["owner_id"]),
    )
    if not rows:
        fail(409, "VERSION_UNAVAILABLE", "授权版本已下线或不存在")
    return rows[0]


@router.post("/api/public/v1/workflow-runs", status_code=202)
async def invoke(body: InvokeInput, request: Request) -> dict:
    return await admit(body, request, await credential(request), request.headers.get("idempotency-key", ""))


async def admit(body: InvokeInput, request: Request, key: dict, idempotency: str) -> dict:
    """Share bounded admission with explicitly authorized organizational callers."""
    if not idempotency or len(idempotency) > 120 or not idempotency.isascii() or any(ord(c) < 33 for c in idempotency):
        fail(422, "IDEMPOTENCY_REQUIRED", "请提供 1–120 个可见 ASCII 字符的 Idempotency-Key")
    source = await published(request, key)
    from app.workflow_schema import validate_text

    validate_text(body.text, Definition.model_validate(source["definition"]).input_schema)
    # Import here to share the exact resource/version checks with internal execution.
    from app.workflow_api import runtime

    pins = await runtime(request).pins(
        Definition.model_validate(source["definition"]), key["owner_id"], source["model_id"]
    )
    fingerprint = digest({"text": body.text, "version_id": str(key["version_id"])})
    async with request.app.state.db.pool.connection() as connection:
        current = await (
            await connection.execute(
                "SELECT * FROM api_credentials WHERE id=%s AND revoked_at IS NULL AND expires_at>now() FOR UPDATE",
                (key["id"],),
            )
        ).fetchone()
        if not current:
            fail(401, "KEY_INVALID", "Key 已撤销或到期")
        previous = await (
            await connection.execute(
                "SELECT run_id,request_hash FROM external_workflow_jobs WHERE credential_id=%s AND idempotency_key=%s",
                (key["id"], idempotency),
            )
        ).fetchone()
        if previous:
            if previous["request_hash"] != fingerprint:
                fail(409, "IDEMPOTENCY_CONFLICT", "同一幂等键对应的输入不同")
            return {"id": previous["run_id"], "replayed": True}
        count = await (
            await connection.execute(
                "SELECT count(*) AS n FROM api_call_logs"
                " WHERE credential_id=%s AND created_at>now()-interval '1 minute'",
                (key["id"],),
            )
        ).fetchone()
        if count["n"] >= current["rpm"]:
            fail(429, "RATE_LIMITED", "已达到每分钟调用上限，请稍后重试")
        active = await (
            await connection.execute(
                "SELECT count(*) AS n FROM external_workflow_jobs j JOIN workflow_runs r ON r.id=j.run_id "
                "WHERE j.credential_id=%s AND r.status IN ('PENDING','RUNNING')",
                (key["id"],),
            )
        ).fetchone()
        if active["n"] >= current["concurrency"]:
            fail(429, "CONCURRENCY_LIMITED", "此 Key 已达到排队与运行并发上限")
        identifier = uuid4()
        snapshot = {
            "definition": source["definition"],
            "resource_pins": pins,
            "name": source["name"],
            "model_id": source["model_id"],
            "instructions": source["instructions"],
            "published_version": source["version"],
            "model_revision": source["model_revision"],
        }
        await connection.execute(
            "INSERT INTO workflow_runs(id,workflow_id,status,input,snapshot) VALUES(%s,%s,'PENDING',%s,%s)",
            (identifier, source["workflow_id"], body.text, Jsonb(snapshot)),
        )
        await connection.execute(
            "INSERT INTO external_workflow_jobs(run_id,credential_id,idempotency_key,request_hash) VALUES(%s,%s,%s,%s)",
            (identifier, key["id"], idempotency, fingerprint),
        )
        await connection.execute(
            "INSERT INTO api_call_logs(credential_id,run_id,outcome) VALUES(%s,%s,'accepted')", (key["id"], identifier)
        )
    return {"id": identifier, "replayed": False}


@router.get("/api/public/v1/workflow-runs/{identifier}")
async def get_run(identifier: UUID, request: Request) -> dict:
    key = await credential(request)
    rows = await request.app.state.db.fetch(
        "SELECT r.id,r.status,r.output,r.error_node,r.created_at,r.finished_at FROM workflow_runs r "
        "JOIN external_workflow_jobs j ON j.run_id=r.id WHERE r.id=%s AND j.credential_id=%s",
        (identifier, key["id"]),
    )
    if not rows:
        fail(404, "RUN_NOT_FOUND", "运行不存在或不属于此 Key")
    return rows[0]


@router.get("/api/v1/api-call-logs")
async def logs(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT l.id,l.run_id,l.outcome,l.created_at,k.name AS key_name,"
        "COALESCE(r.status,l.outcome) AS status,r.error_node,"
        "extract(epoch FROM(r.finished_at-r.created_at))*1000 AS duration_ms FROM api_call_logs l "
        "JOIN api_credentials k ON k.id=l.credential_id LEFT JOIN workflow_runs r ON r.id=l.run_id "
        "WHERE k.owner_id=%s ORDER BY l.id DESC LIMIT 200",
        (user["id"],),
    )
    return {"items": rows}
