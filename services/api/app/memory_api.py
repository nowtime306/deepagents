"""Explicitly curated private context, separated from conversation and knowledge evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.memory_scope import digest, duplicate, expiration, scope
from app.security import identity

router = APIRouter(prefix="/api/v1/memories", tags=["长期记忆"])


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    enabled: bool = True
    revision: int | None = Field(default=None, ge=1)
    scope_kind: Literal["global", "project", "agent"] | None = None
    scope_id: UUID | None = None
    expires_at: datetime | None = None


async def search(
    database, owner: UUID, query: str, *, project_id: UUID | None = None, agent_id: UUID | None = None
) -> list[dict]:
    """Search enabled owner-scoped memories with literal substring matching."""
    if not query.strip() or len(query) > 500:
        fail(422, "MEMORY_QUERY_INVALID", "记忆查询应为 1–500 个字符")
    return await database.fetch(
        "SELECT id,title,content,scope_kind,expires_at FROM private_memories WHERE owner_id=%s AND enabled "
        "AND (expires_at IS NULL OR expires_at>now()) AND (scope_kind='global' OR "
        "(scope_kind='project' AND scope_id=%s AND EXISTS(SELECT 1 FROM conversation_projects p "
        "WHERE p.id=scope_id AND p.owner_id=private_memories.owner_id)) OR "
        "(scope_kind='agent' AND scope_id=%s AND EXISTS(SELECT 1 FROM agents a "
        "WHERE a.id=scope_id AND a.owner_id=private_memories.owner_id AND a.enabled))) "
        "AND (position(lower(%s) in lower(title))>0 OR position(lower(%s) in lower(content))>0) "
        "ORDER BY updated_at DESC,id LIMIT 5",
        (owner, project_id, agent_id, query.strip(), query.strip()),
    )


@router.get("")
async def listing(request: Request, query: str = "", offset: int = 0) -> dict:
    user = await identity(request)
    if len(query) > 500 or offset < 0 or offset > 10000:
        fail(422, "MEMORY_QUERY_INVALID", "查询或页码超出范围")
    rows = await request.app.state.db.fetch(
        "SELECT id,title,content,enabled,revision,updated_at,scope_kind,scope_id,expires_at,candidate_id,origin "
        "FROM private_memories WHERE owner_id=%s "
        "AND (position(lower(%s) in lower(title))>0 OR position(lower(%s) in lower(content))>0) "
        "ORDER BY updated_at DESC,id LIMIT 51 OFFSET %s",
        (user["id"], query, query, offset),
    )
    return {"items": rows[:50], "next_offset": offset + 50 if len(rows) > 50 else None}


@router.post("", status_code=201)
async def create(body: MemoryInput, request: Request) -> dict:
    user = await identity(request)
    expiration(body.expires_at)
    async with request.app.state.db.pool.connection() as connection:
        kind = body.scope_kind or "global"
        await scope(connection, user["id"], kind, body.scope_id)
        same = await duplicate(connection, user["id"], body.title, body.content, kind, body.scope_id)
        if same:
            return {"id": same["id"], "duplicate": True}
        return await (
            await connection.execute(
                "INSERT INTO private_memories"
                "(id,owner_id,title,content,enabled,scope_kind,scope_id,expires_at,content_digest) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    uuid4(),
                    user["id"],
                    body.title,
                    body.content,
                    body.enabled,
                    kind,
                    body.scope_id,
                    body.expires_at,
                    digest(body.content),
                ),
            )
        ).fetchone()


@router.put("/{identifier}")
async def update(identifier: UUID, body: MemoryInput, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT * FROM private_memories WHERE id=%s AND owner_id=%s AND revision=%s",
                (identifier, user["id"], body.revision),
            )
        ).fetchone()
        if not row:
            fail(409, "MEMORY_CONFLICT", "记忆不存在或已被修改，请刷新后重试")
        kind = (body.scope_kind or "global") if "scope_kind" in body.model_fields_set else row["scope_kind"]
        scope_id = body.scope_id if "scope_id" in body.model_fields_set else row["scope_id"]
        expires = body.expires_at if "expires_at" in body.model_fields_set else row["expires_at"]
        if "expires_at" in body.model_fields_set and expires != row["expires_at"]:
            expiration(expires)
        await scope(connection, user["id"], kind, scope_id)
        same = await duplicate(connection, user["id"], body.title, body.content, kind, scope_id, excluding=identifier)
        if same:
            fail(409, "MEMORY_DUPLICATE", "此范围已有相同内容，请使用旧记忆")
        updated = await (
            await connection.execute(
                "UPDATE private_memories SET title=%s,content=%s,enabled=%s,scope_kind=%s,scope_id=%s,expires_at=%s,"
                "content_digest=%s,revision=revision+1,updated_at=now() "
                "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id",
                (
                    body.title,
                    body.content,
                    body.enabled,
                    kind,
                    scope_id,
                    expires,
                    digest(body.content),
                    identifier,
                    user["id"],
                    body.revision,
                ),
            )
        ).fetchone()
        if not updated:
            fail(409, "MEMORY_CONFLICT", "记忆已变化，请刷新后重试")
        return updated


@router.delete("/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM private_memories WHERE id=%s AND owner_id=%s RETURNING id",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "MEMORY_NOT_FOUND", "记忆不存在或无权访问")
    return {"ok": True}
