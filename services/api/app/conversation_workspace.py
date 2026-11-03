"""Private conversation projects, pinning and literal title/message search."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["会话工作空间"])


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID | None = None
    pinned: bool = False


class TitleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)


async def lock_project(connection, identifier: UUID | None, owner_id: UUID) -> None:
    """Authorize a project and prevent deletion until its assignment commits."""
    if identifier is None:
        return
    cursor = await connection.execute(
        "SELECT id FROM conversation_projects WHERE id=%s AND owner_id=%s FOR KEY SHARE", (identifier, owner_id)
    )
    if not await cursor.fetchone():
        fail(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问")


@router.get("/conversation-projects")
async def projects(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT p.*,count(c.id)::int AS conversations FROM conversation_projects p "
        "LEFT JOIN conversations c ON c.project_id=p.id AND c.owner_id=p.owner_id "
        "WHERE p.owner_id=%s GROUP BY p.id ORDER BY p.created_at DESC,p.id DESC",
        (user["id"],),
    )
    return {"items": rows}


@router.post("/conversation-projects", status_code=201)
async def create(body: ProjectInput, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user["id"],))
        cursor = await connection.execute(
            "SELECT count(*) AS total FROM conversation_projects WHERE owner_id=%s", (user["id"],)
        )
        if (await cursor.fetchone())["total"] >= 100:
            fail(422, "PROJECT_LIMIT", "每个账号最多创建 100 个项目")
        cursor = await connection.execute(
            "INSERT INTO conversation_projects(id,owner_id,name) VALUES (%s,%s,%s) RETURNING *",
            (uuid4(), user["id"], body.name),
        )
        return await cursor.fetchone()


@router.patch("/conversation-projects/{identifier}")
async def rename(identifier: UUID, body: ProjectInput, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "UPDATE conversation_projects SET name=%s WHERE id=%s AND owner_id=%s RETURNING *",
        (body.name, identifier, user["id"]),
    )
    if not rows:
        fail(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问")
    return rows[0]


@router.delete("/conversation-projects/{identifier}")
async def remove(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM conversation_projects WHERE id=%s AND owner_id=%s RETURNING id", (identifier, user["id"])
    )
    if not rows:
        fail(404, "PROJECT_NOT_FOUND", "项目不存在或无权访问")
    return {"ok": True}


@router.patch("/conversations/{identifier}/workspace")
async def update(identifier: UUID, body: WorkspaceInput, request: Request) -> dict:
    user = await identity(request)
    if not body.model_fields_set:
        fail(422, "INVALID_INPUT", "请选择置顶状态或所属项目")
    async with request.app.state.db.pool.connection() as connection:
        # Projects are always locked before conversations, matching the FK deletion order.
        await lock_project(connection, body.project_id, user["id"])
        cursor = await connection.execute(
            "UPDATE conversations SET pinned=CASE WHEN %s THEN %s ELSE pinned END,"
            "project_id=CASE WHEN %s THEN %s::uuid ELSE project_id END "
            "WHERE id=%s AND owner_id=%s RETURNING *",
            (
                "pinned" in body.model_fields_set,
                body.pinned,
                "project_id" in body.model_fields_set,
                body.project_id,
                identifier,
                user["id"],
            ),
        )
        row = await cursor.fetchone()
        if not row:
            fail(404, "NOT_FOUND", "对话不存在或无权访问")
    return row


async def listing(
    request: Request, before: UUID | None, archived: bool, query: str, project_id: UUID | None, unassigned: bool
) -> dict:
    """Filter before keyset pagination; search text literally, never SQL wildcard syntax."""
    user = await identity(request)
    if project_id and unassigned:
        fail(422, "INVALID_INPUT", "不能同时筛选项目和未分组对话")
    async with request.app.state.db.pool.connection() as connection:
        await lock_project(connection, project_id, user["id"])
        cursor = await connection.execute(
            "SELECT c.* FROM conversations c WHERE c.owner_id=%s AND c.archived=%s AND c.internal_purpose IS NULL "
            "AND (%s::uuid IS NULL OR c.project_id=%s) AND (NOT %s OR c.project_id IS NULL) "
            "AND (%s='' OR strpos(lower(c.title),lower(%s))>0 OR EXISTS ("
            "SELECT 1 FROM conversation_messages m WHERE m.conversation_id=c.id "
            "AND m.role IN ('user','assistant') AND strpos(lower(m.content->>'text'),lower(%s))>0)) "
            "AND (%s::uuid IS NULL OR (c.pinned,c.created_at,c.id)<(SELECT pinned,created_at,id "
            "FROM conversations WHERE id=%s AND owner_id=%s)) "
            "ORDER BY c.pinned DESC,c.created_at DESC,c.id DESC LIMIT 51",
            (user["id"], archived, project_id, project_id, unassigned, query, query, query, before, before, user["id"]),
        )
        rows = await cursor.fetchall()
    return {"items": rows[:50], "next_cursor": str(rows[49]["id"]) if len(rows) > 50 else None}
