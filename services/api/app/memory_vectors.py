"""Private scoped vector recall with fixed model revisions and metered requests."""

import math
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.embedding_compatibility import fingerprint
from app.errors import APIError, fail
from app.knowledge import embed_texts
from app.memory_api import search as keyword_search
from app.memory_scope import scope
from app.model_catalog import supports_embedding
from app.permissions import require_account
from app.resource_runtime import resource_run
from app.resource_usage import EmbeddingMeter, finish_operation, operation, parent_authority
from app.security import identity
from app.usage_api import summaries

router = APIRouter(prefix="/api/v1/memories", tags=["语义记忆"])


def valid_vectors(vectors: list, expected: int) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected or not vectors:
        fail(502, "INVALID_EMBEDDING", "向量数量与输入不一致")
    dimension = len(vectors[0]) if isinstance(vectors[0], list) else 0
    if not 1 <= dimension <= 8192:
        fail(502, "INVALID_EMBEDDING", "向量维度不正确")
    if any(
        not isinstance(v, list)
        or len(v) != dimension
        or any(
            isinstance(n, bool) or not isinstance(n, (float, int)) or abs(n) > 1e308 or not math.isfinite(n) for n in v
        )
        or not any(v)
        for v in vectors
    ):
        fail(502, "INVALID_EMBEDDING", "向量含无效数字、空向量或不同维度")
    normalized = []
    for vector in vectors:
        scale = max(abs(value) for value in vector)
        scaled = [value / scale for value in vector]
        norm = math.sqrt(sum(value * value for value in scaled))
        normalized.append([value / norm for value in scaled])
    return normalized


def cosine(first, second):
    if len(first) != len(second):
        return None
    return sum(a * b for a, b in zip(first, second, strict=True)) / (
        math.sqrt(sum(a * a for a in first)) * math.sqrt(sum(b * b for b in second))
    )


class MemoryRecall:
    def __init__(self, database, registry):
        self.db, self.registry = database, registry
        self.embedder = embed_texts

    async def config(self, owner):
        rows = await self.db.fetch("SELECT * FROM memory_settings WHERE owner_id=%s", (owner,))
        if not rows or not rows[0]["enabled"]:
            return None, None
        from app.retrieval_policy import memory_policy

        settings = await memory_policy(self.registry.vault, owner, rows[0])
        try:
            config = await self.registry.get(settings["embedding_model_id"])
        except APIError as error:
            if error.code.startswith("RESOURCE_"):
                raise
            return None, "语义模型已停用或移除，当前使用关键词"
        if not supports_embedding(config.kind, config.protocol):
            return None, "语义模型配置已变化，请确认配置并重新索引；当前使用关键词"
        return config, None

    async def vectors(self, owner, config, texts, context):
        try:
            values = await self.embedder(config, texts, meter=EmbeddingMeter(self.db, owner, config, context))
            return valid_vectors(values, len(texts))
        except httpx.HTTPError as error:
            raise APIError(
                502, "EMBEDDING_RESULT_UNKNOWN", "向量调用中断或返回异常，结果与消耗需核对；未自动重试"
            ) from error
        except (ValueError, KeyError, TypeError) as error:
            raise APIError(502, "INVALID_EMBEDDING", "向量服务返回的数据格式无效，请核对用量后修正配置") from error

    async def recall(self, owner, query, *, project_id=None, agent_id=None, context=None):
        await require_account(self.db, owner, "memory.read")
        fallback = await keyword_search(self.db, owner, query, project_id=project_id, agent_id=agent_id)
        config, warning = await self.config(owner)
        if config is None:
            return {"items": fallback, "mode": "keyword", "warning": warning}
        rows = await self.eligible(owner, config, project_id, agent_id)
        if len(rows) > 1000:
            return {
                "items": fallback,
                "mode": "keyword",
                "warning": "此范围超过1000条已索引记忆，请缩小范围；本次仅用关键词",
            }
        if not rows:
            return {"items": fallback, "mode": "keyword",
                    "warning": "向量兼容证据缺失或模型身份、维度、预处理已变化；当前使用关键词，请核对或重新索引"}
        if context is None:
            return {"items": fallback, "mode": "keyword", "warning": "此执行入口尚未提供语义调用的预算上下文"}
        vector = (await self.vectors(owner, config, [query], context))[0]
        await parent_authority(self.db, owner, context)
        current, _ = await self.config(owner)
        if current is None or current.id != config.id or fingerprint(current) != fingerprint(config):
            fail(409, "MEMORY_MODEL_CHANGED", "语义配置已变化，本次不返回旧配置的结果")
        await require_account(self.db, owner, "memory.read")
        rows = await self.eligible(owner, config, project_id, agent_id)
        fallback = await keyword_search(self.db, owner, query, project_id=project_id, agent_id=agent_id)
        if any(row.get("vector_dimensions") != len(vector) or len(row["vector"]) != len(vector) for row in rows):
            return {"items": fallback, "mode": "keyword",
                    "warning": "查询向量维度与已保存记忆不兼容，本次仅用关键词；请核对向量模型输出并重新索引"}
        by_id = {str(row["id"]): row for row in [*rows, *fallback]}
        scored = []
        for row in rows:
            cached = valid_vectors([row["vector"]], 1)[0]
            score = cosine(vector, cached)
            if score is not None and score >= 0.2:
                scored.append((score, str(row["id"])))
        ranked = sorted(scored, reverse=True)
        vector_scores = {identifier: score for score, identifier in ranked}
        fused = {identifier: 1 / (60 + index) for index, (_, identifier) in enumerate(ranked, 1)}
        for index, row in enumerate(fallback, 1):
            identifier = str(row["id"])
            fused[identifier] = fused.get(identifier, 0) + 1 / (60 + index)
        result = []
        for identifier, fused_score in sorted(fused.items(), key=lambda item: item[1], reverse=True)[:5]:
            row = {key: value for key, value in by_id[identifier].items() if key != "vector"}
            result.append({**row, "vector_score": vector_scores.get(identifier), "fusion_score": fused_score})
        return {"items": result, "mode": "hybrid", "warning": None}

    async def eligible(self, owner, config, project_id, agent_id):
        return await self.db.fetch(
            "SELECT m.id,m.title,m.content,m.scope_kind,m.expires_at,m.vector,m.vector_dimensions "
            "FROM private_memories m "
            "WHERE m.owner_id=%s AND m.enabled AND (m.expires_at IS NULL OR m.expires_at>now()) "
            "AND m.vector_status='READY' AND m.vector_fingerprint=%s "
            "AND m.vector_digest=m.content_digest AND (m.scope_kind='global' OR "
            "(m.scope_kind='project' AND m.scope_id=%s AND EXISTS(SELECT 1 FROM conversation_projects p "
            "WHERE p.id=m.scope_id AND p.owner_id=m.owner_id)) OR "
            "(m.scope_kind='agent' AND m.scope_id=%s AND EXISTS(SELECT 1 FROM agents a "
            "WHERE a.id=m.scope_id AND a.owner_id=m.owner_id AND a.enabled))) "
            "ORDER BY m.updated_at DESC,m.id LIMIT 1001",
            (owner, fingerprint(config), project_id, agent_id),
        )


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embedding_model_id: str | None = Field(default=None, max_length=120)
    enabled: bool = False
    revision: int = Field(default=0, ge=0)
    model_revision: int | None = Field(default=None, ge=1)


@router.get("/settings")
async def settings(request: Request):
    owner = (await identity(request))["id"]
    rows = await request.app.state.db.fetch("SELECT * FROM memory_settings WHERE owner_id=%s", (owner,))
    return rows[0] if rows else {"embedding_model_id": None, "enabled": False, "revision": 0, "model_revision": None}


@router.get("/embedding-options")
async def options(request: Request):
    await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,name,revision FROM model_configs WHERE enabled "
            "AND kind='embedding' AND protocol='openai' ORDER BY name",
        )
    }


@router.put("/settings")
async def save(body: SettingsInput, request: Request):
    owner = (await identity(request))["id"]
    config = None
    if body.embedding_model_id:
        config = await request.app.state.models.get(body.embedding_model_id)
        if not supports_embedding(config.kind, config.protocol):
            fail(422, "MODEL_KIND_INVALID", "请选择当前已适配的文本向量模型")
    if body.enabled and config is None:
        fail(422, "MODEL_REQUIRED", "启用语义召回前请选择向量模型")
    rows = await request.app.state.db.fetch(
        "INSERT INTO memory_settings(owner_id,embedding_model_id,model_revision,enabled) SELECT %s,%s,%s,%s WHERE %s=0 "
        "ON CONFLICT(owner_id) DO UPDATE SET embedding_model_id=excluded.embedding_model_id,"
        "model_revision=excluded.model_revision,enabled=excluded.enabled,revision=memory_settings.revision+1 "
        "WHERE memory_settings.revision=%s RETURNING revision",
        (
            owner,
            body.embedding_model_id,
            config.revision if config else None,
            body.enabled,
            body.revision,
            body.revision,
        ),
    )
    if not rows and body.revision:
        rows = await request.app.state.db.fetch(
            "UPDATE memory_settings SET embedding_model_id=%s,model_revision=%s,enabled=%s,revision=revision+1 "
            "WHERE owner_id=%s AND revision=%s RETURNING revision",
            (body.embedding_model_id, config.revision if config else None, body.enabled, owner, body.revision),
        )
    if not rows:
        fail(409, "MEMORY_SETTINGS_CHANGED", "配置已变化，请刷新后再保存")
    return rows[0]


class IndexInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifiers: list[UUID] = Field(default_factory=list, max_length=16)
    acknowledge_duplicate: bool = False


@router.post("/index")
async def index(body: IndexInput, request: Request):
    owner = (await identity(request))["id"]
    async with resource_run(request.app.state.db, owner, "resource", uuid4()):
        return await _index(body, request)


async def _index(body, request):
    owner = (await identity(request))["id"]
    db, service = request.app.state.db, MemoryRecall(request.app.state.db, request.app.state.models)
    config, warning = await service.config(owner)
    if config is None:
        fail(409, "MEMORY_VECTOR_NOT_READY", warning or "请先配置并启用语义记忆模型")
    previous = await db.fetch(
        "SELECT 1 FROM resource_operations r WHERE r.owner_id=%s AND r.resource_kind='memory' AND r.action='index' "
        "AND r.status='UNKNOWN' AND r.created_at>coalesce((SELECT max(s.created_at) FROM resource_operations s "
        "WHERE s.owner_id=r.owner_id AND s.resource_kind='memory' AND s.action='index' AND s.status='SUCCEEDED'),"
        "'-infinity'::timestamptz) LIMIT 1",
        (owner,),
    )
    if previous and not body.acknowledge_duplicate:
        fail(409, "INDEX_REVIEW_REQUIRED", "曾有结果未知的索引，请核对消耗后明确确认重新索引可能重复计费")
    rows = await db.fetch(
        "SELECT id,title,content,content_digest FROM private_memories WHERE owner_id=%s AND enabled "
        "AND (expires_at IS NULL OR expires_at>now()) AND (%s OR id=ANY(%s::uuid[])) "
        "AND (NOT %s OR vector_status<>'READY' OR vector_fingerprint IS DISTINCT FROM %s "
        "OR vector_digest IS DISTINCT FROM content_digest) "
        "ORDER BY updated_at DESC,id LIMIT 16",
        (owner, not body.identifiers, body.identifiers, not body.identifiers, fingerprint(config)),
    )
    if body.identifiers and {r["id"] for r in rows} != set(body.identifiers):
        fail(403, "MEMORY_INDEX_UNAVAILABLE", "只能索引本人启用且未过期的记忆")
    if not rows:
        return {"indexed": 0, "message": "没有需要索引的记忆"}
    texts = [r["title"] + "\n" + r["content"] for r in rows]
    if sum(len(text.encode()) for text in texts) + 128 > 64000:
        fail(422, "INDEX_BATCH_TOO_LARGE", "此批内容超过保守 Token 预占上限，请选择更少记忆")
    context = await operation(db, owner, config, "memory", "index")
    try:
        vectors = await service.vectors(owner, config, texts, context)
        await require_account(db, owner, "memory.manage")
        async with db.pool.connection() as connection:
            active = await (
                await connection.execute(
                    "SELECT o.id FROM resource_operations o JOIN memory_settings s ON s.owner_id=o.owner_id "
                    "JOIN model_configs m ON m.id=o.model_id WHERE o.id=%s AND o.lease_token=%s "
                    "AND o.status='RUNNING' AND o.lease_until>now() AND s.enabled "
                    "AND m.enabled AND m.kind='embedding' AND m.protocol='openai' FOR UPDATE OF o",
                    (context["id"], context["lease_token"]),
                )
            ).fetchone()
            if not active:
                fail(409, "MEMORY_INDEX_REVOKED", "索引租约或模型配置已变化，本次未保存向量")
            indexed = 0
            for row, vector in zip(rows, vectors, strict=True):
                changed = await (
                    await connection.execute(
                        "UPDATE private_memories SET vector=%s,vector_status='READY',vector_model_id=%s,"
                        "vector_model_revision=%s,vector_fingerprint=%s,vector_dimensions=%s,vector_digest=%s "
                        "WHERE id=%s AND owner_id=%s AND enabled "
                        "AND content_digest=%s AND (expires_at IS NULL OR expires_at>now()) RETURNING id",
                        (
                            Jsonb(vector),
                            config.id,
                            config.revision,
                            fingerprint(config),
                            len(vector),
                            row["content_digest"],
                            row["id"],
                            owner,
                            row["content_digest"],
                        ),
                    )
                ).fetchone()
                indexed += bool(changed)
            await connection.execute(
                "UPDATE resource_operations SET status='SUCCEEDED',finished_at=now() WHERE id=%s",
                (context["id"],),
            )
        return {"indexed": indexed, "skipped_changed": len(rows) - indexed, "operation_id": context["id"]}
    except BaseException as error:
        ambiguous = await db.fetch(
            "SELECT 1 FROM model_usage WHERE run_id=%s AND status IN ('PENDING','UNKNOWN') LIMIT 1",
            (context["id"],),
        )
        await finish_operation(
            db,
            context,
            "UNKNOWN" if ambiguous else "FAILED",
            error.code if isinstance(error, APIError) else "MEMORY_INDEX_INTERRUPTED",
        )
        raise


class RecallInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    project_id: UUID | None = None
    agent_id: UUID | None = None


@router.post("/recall")
async def recall(body: RecallInput, request: Request):
    owner = (await identity(request))["id"]
    async with resource_run(request.app.state.db, owner, "resource", uuid4()):
        return await _recall(body, request)


async def _recall(body, request):
    owner = (await identity(request))["id"]
    db = request.app.state.db
    async with db.pool.connection() as connection:
        if body.project_id:
            await scope(connection, owner, "project", body.project_id)
        if body.agent_id:
            await scope(connection, owner, "agent", body.agent_id)
    service = MemoryRecall(db, request.app.state.models)
    config, _ = await service.config(owner)
    context = await operation(db, owner, config, "memory", "query") if config else None
    try:
        result = await service.recall(
            owner, body.query, project_id=body.project_id, agent_id=body.agent_id, context=context
        )
        if context:
            if not await finish_operation(db, context, "SUCCEEDED"):
                fail(409, "RESOURCE_LEASE_LOST", "资源操作已失效，不返回旧结果")
            result["operation_id"] = context["id"]
        return result
    except BaseException as error:
        if context:
            ambiguous = await db.fetch(
                "SELECT 1 FROM model_usage WHERE run_id=%s AND status IN ('PENDING','UNKNOWN') LIMIT 1",
                (context["id"],),
            )
            await finish_operation(
                db,
                context,
                "UNKNOWN" if ambiguous else "FAILED",
                error.code if isinstance(error, APIError) else "MEMORY_QUERY_INTERRUPTED",
            )
        raise


@router.get("/resource-operations")
async def operations(request: Request):
    owner = (await identity(request))["id"]
    db = request.app.state.db
    await db.execute(
        "UPDATE resource_operations SET status='UNKNOWN',error_code='RESOURCE_RESULT_UNKNOWN',finished_at=now() "
        "WHERE owner_id=%s AND status='RUNNING' AND lease_until<=now()",
        (owner,),
    )
    rows = await db.fetch(
        "SELECT id,action,model_id,model_revision,status,error_code,created_at FROM resource_operations "
        "WHERE owner_id=%s AND resource_kind='memory' ORDER BY created_at DESC LIMIT 50",
        (owner,),
    )
    usage = {r["run_id"]: r for r in await summaries(db, owner, [r["id"] for r in rows])}
    return {"items": [{**row, "usage": usage.get(row["id"])} for row in rows]}
