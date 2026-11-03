"""Owner-reviewed answer feedback never changes prompts, memory or permissions."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.permissions import require_account
from app.security import identity

router = APIRouter(prefix="/api/v1/messages", tags=["回答反馈"])


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["helpful", "unhelpful"]
    citations_correct: bool | None = None
    refusal_correct: bool | None = None
    comment: str = Field(default="", max_length=2000)
    revision: int = Field(default=0, ge=0)


async def owned(identifier: UUID, request: Request, *, writing=False):
    owner = (await identity(request))["id"]
    await require_account(request.app.state.db, owner, "chat.manage" if writing else "chat.read")
    rows = await request.app.state.db.fetch(
        "SELECT m.id FROM conversation_messages m JOIN conversations c ON c.id=m.conversation_id "
        "WHERE m.id=%s AND c.owner_id=%s AND m.role='assistant' AND c.internal_purpose IS NULL",
        (identifier, owner),
    )
    if not rows:
        fail(404, "ANSWER_NOT_FOUND", "回答不存在、不是普通助手回答或无权访问")
    return owner


@router.get("/{identifier}/feedback")
async def read(identifier: UUID, request: Request):
    owner = await owned(identifier, request)
    rows = await request.app.state.db.fetch(
        "SELECT rating,citations_correct,refusal_correct,comment,revision FROM message_feedback "
        "WHERE message_id=%s AND owner_id=%s",
        (identifier, owner),
    )
    return (
        rows[0]
        if rows
        else {"rating": None, "citations_correct": None, "refusal_correct": None, "comment": "", "revision": 0}
    )


@router.put("/{identifier}/feedback")
async def save(identifier: UUID, body: Feedback, request: Request):
    owner = await owned(identifier, request, writing=True)
    if body.revision:
        rows = await request.app.state.db.fetch(
            "UPDATE message_feedback SET rating=%s,citations_correct=%s,refusal_correct=%s,comment=%s,"
            "revision=revision+1,updated_at=now() "
            "WHERE message_id=%s AND owner_id=%s AND revision=%s RETURNING revision",
            (body.rating, body.citations_correct, body.refusal_correct, body.comment, identifier, owner, body.revision),
        )
    else:
        rows = await request.app.state.db.fetch(
            "INSERT INTO message_feedback(message_id,owner_id,rating,citations_correct,refusal_correct,comment) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(message_id) DO NOTHING RETURNING revision",
            (identifier, owner, body.rating, body.citations_correct, body.refusal_correct, body.comment),
        )
    if not rows:
        fail(409, "FEEDBACK_CONFLICT", "反馈已变化，请重新打开后编辑")
    return rows[0]


@router.delete("/{identifier}/feedback")
async def delete(identifier: UUID, request: Request, revision: int):
    owner = await owned(identifier, request, writing=True)
    rows = await request.app.state.db.fetch(
        "DELETE FROM message_feedback WHERE message_id=%s AND owner_id=%s AND revision=%s RETURNING message_id",
        (identifier, owner, revision),
    )
    if not rows:
        fail(409, "FEEDBACK_CONFLICT", "反馈不存在或已变化，请重新打开")
    return {"ok": True}
