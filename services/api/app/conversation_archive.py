"""Owner-scoped conversation archiving without deleting history or checkpoints."""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.errors import fail
from app.security import identity

router = APIRouter(prefix="/api/v1/conversations", tags=["对话归档"])


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archived: bool


@router.post("/{identifier}/archive")
async def archive(identifier: UUID, body: ArchiveInput, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        cursor = await connection.execute(
            "SELECT id FROM conversations WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, user["id"])
        )
        if not await cursor.fetchone():
            fail(404, "NOT_FOUND", "对话不存在或无权访问")
        cursor = await connection.execute(
            "SELECT 1 FROM task_runs WHERE conversation_id=%s AND status IN ('PENDING','RUNNING')",
            (identifier,),
        )
        if await cursor.fetchone():
            fail(409, "RUN_STATE_CONFLICT", "请先停止生成再归档或恢复对话")
        await connection.execute(
            "UPDATE conversations SET archived=%s WHERE id=%s AND owner_id=%s", (body.archived, identifier, user["id"])
        )
    return {"id": identifier, "archived": body.archived}
