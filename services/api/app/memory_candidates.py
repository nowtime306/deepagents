"""Review and explicitly accept candidate text; never silently overwrite a memory."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.memory_scope import digest, duplicate, expiration, scope
from app.security import identity

router = APIRouter(prefix="/api/v1/memories", tags=["记忆审阅"])


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    decision: Literal["accept", "reject"]
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    scope_kind: Literal["global", "project", "agent"] = "global"
    scope_id: UUID | None = None
    expires_at: datetime | None = None


@router.get("/scope-options")
async def scopes(request: Request):
    owner = (await identity(request))["id"]
    db = request.app.state.db
    return {
        "projects": await db.fetch(
            "SELECT id,name FROM conversation_projects WHERE owner_id=%s ORDER BY name", (owner,)
        ),
        "agents": await db.fetch("SELECT id,name FROM agents WHERE owner_id=%s AND enabled ORDER BY name", (owner,)),
    }


@router.get("/candidates")
async def listing(request: Request):
    owner = (await identity(request))["id"]
    return {
        "items": await request.app.state.db.fetch(
            "SELECT m.id,m.title,m.content,m.status,m.revision,m.source_message_ids,m.memory_id,m.created_at,"
            "j.source_conversation_id,c.title AS source_title,(SELECT jsonb_agg(jsonb_build_object('id',s.id,"
            "'text',left(s.content->>'text',2000))) FROM conversation_messages s "
            "WHERE s.conversation_id=j.source_conversation_id "
            "AND m.source_message_ids ? s.id::text) AS source_messages "
            "FROM memory_candidates m JOIN memory_jobs j ON j.id=m.job_id "
            "JOIN conversations c ON c.id=j.source_conversation_id WHERE m.owner_id=%s "
            "ORDER BY m.created_at DESC,m.id DESC LIMIT 100",
            (owner,),
        )
    }


@router.post("/candidates/{identifier}/review")
async def review(identifier: UUID, body: Review, request: Request):
    owner = (await identity(request))["id"]
    db = request.app.state.db
    async with db.pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT * FROM memory_candidates WHERE id=%s AND owner_id=%s "
                "AND revision=%s AND status='PENDING' FOR UPDATE",
                (identifier, owner, body.revision),
            )
        ).fetchone()
        if not row:
            fail(409, "CANDIDATE_CONFLICT", "候选不存在、无权访问或已经审阅")
        memory, same = None, None
        title, content = body.title or row["title"], body.content or row["content"]
        if body.decision == "accept":
            expiration(body.expires_at)
            await scope(connection, owner, body.scope_kind, body.scope_id)
            same = await duplicate(connection, owner, title, content, body.scope_kind, body.scope_id)
            memory = same["id"] if same else uuid4()
            if not same:
                await connection.execute(
                    "INSERT INTO private_memories"
                    "(id,owner_id,title,content,scope_kind,scope_id,expires_at,content_digest,"
                    "candidate_id,origin) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'candidate')",
                    (
                        memory,
                        owner,
                        title,
                        content,
                        body.scope_kind,
                        body.scope_id,
                        body.expires_at,
                        digest(content),
                        identifier,
                    ),
                )
        await connection.execute(
            "UPDATE memory_candidates SET status=%s,memory_id=%s,title=%s,content=%s,"
            "revision=revision+1,reviewed_at=now() "
            "WHERE id=%s",
            ("ACCEPTED" if memory else "REJECTED", memory, title, content, identifier),
        )
    return {"memory_id": memory, "duplicate": bool(same), "status": "ACCEPTED" if memory else "REJECTED"}
