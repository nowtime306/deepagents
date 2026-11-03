"""Owned memory scope validation and expiration; remembered text grants no authority."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from app.errors import fail


def digest(content: str) -> str:
    return hashlib.sha256(content.strip().encode()).hexdigest()


async def scope(connection, owner: UUID, kind: str, identifier: UUID | None):
    if kind == "global":
        if identifier is not None:
            fail(422, "MEMORY_SCOPE_INVALID", "全局记忆不能指定项目或智能体")
        return
    if identifier is None:
        fail(422, "MEMORY_SCOPE_INVALID", "请选择记忆所属的项目或智能体")
    table = "conversation_projects" if kind == "project" else "agents"
    enabled = " AND enabled" if kind == "agent" else ""
    row = await (
        await connection.execute(
            f"SELECT id FROM {table} WHERE id=%s AND owner_id=%s{enabled} FOR KEY SHARE",
            (identifier, owner),
        )
    ).fetchone()
    if not row:
        fail(403, "MEMORY_SCOPE_UNAVAILABLE", "项目或智能体不存在、停用或不属于本人")


def expiration(value: datetime | None):
    if value is None:
        return
    if value.tzinfo is None or value <= datetime.now(UTC):
        fail(422, "MEMORY_EXPIRATION_INVALID", "记忆到期时间必须为带时区的未来时刻")


async def duplicate(connection, owner, title, content, kind, identifier, *, excluding=None):
    # Serialize acceptance/manual edits in the same owner and scope without rewriting existing records.
    await connection.execute(
        "SELECT pg_advisory_xact_lock(710026,hashtext(%s))", (str(owner) + kind + str(identifier),)
    )
    rows = await (
        await connection.execute(
            "SELECT id,title,content FROM private_memories WHERE owner_id=%s AND scope_kind=%s "
            "AND scope_id IS NOT DISTINCT FROM %s::uuid AND (%s::uuid IS NULL OR id<>%s) "
            "AND (title=%s OR content_digest=%s) FOR KEY SHARE",
            (owner, kind, identifier, excluding, excluding, title, digest(content)),
        )
    ).fetchall()
    same = next((row for row in rows if row["content"] == content), None)
    conflict = next((row for row in rows if row["title"] == title and row["content"] != content), None)
    if conflict:
        fail(409, "MEMORY_TITLE_CONFLICT", "同范围已有同名不同内容的记忆，请改名或在记忆列表中明确编辑旧记忆")
    return same
