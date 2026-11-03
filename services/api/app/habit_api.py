"""Private habit controls; all manual writes use optimistic revisions."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.habit_skills import OPTIONS, evidence_count, generate, lock, revoke, validate_value
from app.permissions import require_account
from app.security import identity
from app.skill_library import parse_skill

META_ASSET = Path(__file__).resolve().parents[3] / "assets/skills/habit-skill-builder/SKILL.md"

router = APIRouter(prefix="/api/v1/capabilities/habits", tags=["习惯技能"])


class Enabled(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class Correction(Revision):
    value: str = Field(max_length=40)


class Toggle(Revision):
    enabled: bool


async def owned(connection, owner, identifier, revision=None):
    row = await (await connection.execute(
        "SELECT * FROM habit_preferences WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, owner),
    )).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "习惯不存在或无权访问")
    if revision is not None and row["revision"] != revision:
        fail(409, "HABIT_CONFLICT", "习惯已发生变化，请刷新后操作")
    return row


@router.get("")
async def listing(request: Request):
    owner = (await identity(request))["id"]
    db = request.app.state.db
    settings = await db.fetch(
        "SELECT h.*,s.enabled AS meta_skill_enabled FROM habit_settings h LEFT JOIN skills s ON s.id=h.meta_skill_id "
        "WHERE h.owner_id=%s", (owner,),
    )
    settings = settings[0] if settings else {}
    binding = await db.fetch("SELECT skill_ids FROM capability_bindings WHERE owner_id=%s", (owner,))
    async with db.pool.connection() as connection:
        rows = await (await connection.execute(
            "SELECT p.*,s.enabled AS skill_enabled FROM habit_preferences p LEFT JOIN skills s ON s.id=p.skill_id "
            "WHERE p.owner_id=%s ORDER BY p.updated_at DESC,p.id", (owner,),
        )).fetchall()
        for row in rows:
            row["evidence_count"] = await evidence_count(connection, row)
    return {"enabled": settings.get("enabled", False), "meta_skill_id": settings.get("meta_skill_id"),
            "meta_skill_enabled": settings.get("meta_skill_enabled"),
            "meta_skill_bound": bool(binding and str(settings.get("meta_skill_id")) in binding[0]["skill_ids"]),
            "preferences": rows, "options": {key: [{"value": value, "label": label} for value, label in items.items()]
                                               for key, items in OPTIONS.items()}, "threshold": 3, "scope": "global"}


@router.put("")
async def setting(body: Enabled, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        await lock(connection, owner)
        await connection.execute(
            "INSERT INTO habit_settings(owner_id,enabled) VALUES(%s,%s) ON CONFLICT(owner_id) DO UPDATE "
            "SET enabled=excluded.enabled,learning_after=CASE WHEN NOT habit_settings.enabled AND excluded.enabled "
            "THEN now() ELSE habit_settings.learning_after END,updated_at=now()", (owner, body.enabled),
        )
    return {"enabled": body.enabled}


@router.post("/install")
async def install(request: Request):
    owner = (await identity(request))["id"]
    await require_account(request.app.state.db, owner, "chat.manage")
    name, description, files = parse_skill("SKILL.md", await asyncio.to_thread(META_ASSET.read_bytes))
    async with request.app.state.db.pool.connection() as connection:
        await lock(connection, owner)
        existing = await (await connection.execute(
            "SELECT * FROM skills WHERE owner_id=%s AND name=%s FOR UPDATE", (owner, name),
        )).fetchone()
        if existing and existing["files"] != files:
            fail(409, "HABIT_META_NAME_CONFLICT", "已有同名手工技能，未覆盖；请先重命名该技能")
        identifier, version = (existing["id"], existing["version"]) if existing else (uuid4(), 1)
        if existing and not existing["enabled"]:
            fail(409, "HABIT_META_DISABLED", "元Skill已停用，请先明确启用后再绑定")
        if not existing:
            await connection.execute(
                "INSERT INTO skills(id,owner_id,name,description,files) VALUES(%s,%s,%s,%s,%s)",
                (identifier, owner, name, description, Jsonb(files)),
            )
        await connection.execute(
            "INSERT INTO habit_settings(owner_id,meta_skill_id) VALUES(%s,%s) ON CONFLICT(owner_id) "
            "DO UPDATE SET meta_skill_id=excluded.meta_skill_id,updated_at=now()", (owner, identifier),
        )
        await connection.execute(
            "INSERT INTO capability_bindings(owner_id) VALUES(%s) ON CONFLICT(owner_id) DO NOTHING", (owner,),
        )
        row = await (await connection.execute(
            "SELECT skill_ids FROM capability_bindings WHERE owner_id=%s FOR UPDATE", (owner,),
        )).fetchone()
        selected = row["skill_ids"]
        if str(identifier) not in selected:
            if len(selected) >= 20:
                fail(409, "SKILL_BINDING_LIMIT", "通用助手已选20项技能，请先取消一项再安装")
            await connection.execute("UPDATE capability_bindings SET skill_ids=%s,updated_at=now() WHERE owner_id=%s",
                                     (Jsonb([*selected, str(identifier)]), owner))
    return {"skill_id": identifier, "version": version, "bound": True}


@router.get("/preferences/{identifier}/evidence")
async def evidence(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    await require_account(request.app.state.db, owner, "chat.read")
    async with request.app.state.db.pool.connection() as connection:
        await owned(connection, owner, identifier)
        rows = await (await connection.execute(
            "SELECT m.id AS message_id,m.conversation_id,m.created_at,true AS available FROM habit_evidence e "
            "JOIN conversation_messages m ON m.id=e.message_id JOIN conversations c ON c.id=m.conversation_id "
            "WHERE e.preference_id=%s AND e.owner_id=%s AND c.owner_id=%s ORDER BY m.created_at,m.id",
            (identifier, owner, owner),
        )).fetchall()
    return {"items": rows}


@router.post("/preferences/{identifier}/correct")
async def correct(identifier: UUID, body: Correction, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        await lock(connection, owner)
        row = await owned(connection, owner, identifier, body.revision)
        validate_value(row["kind"], body.value)
        await revoke(connection, row, erase=True)
        await connection.execute("DELETE FROM habit_evidence WHERE preference_id=%s", (identifier,))
        await connection.execute(
            "UPDATE habit_preferences SET value=%s,manual_confirmed=true,skill_id=NULL,skill_version=NULL,"
            "learning_after=now(),updated_at=now() WHERE id=%s", (body.value, identifier),
        )
        row.update(value=body.value)
        await generate(connection, row)
    return {"ok": True}


@router.delete("/preferences/{identifier}")
async def forget(identifier: UUID, request: Request, revision: int):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        await lock(connection, owner)
        row = await owned(connection, owner, identifier, revision)
        await revoke(connection, row, erase=True)
        await connection.execute("DELETE FROM habit_evidence WHERE preference_id=%s", (identifier,))
        await connection.execute(
            "UPDATE habit_preferences SET status='forgotten',value='',manual_confirmed=false,skill_id=NULL,"
            "skill_version=NULL,learning_after=now(),revision=revision+1,updated_at=now() WHERE id=%s", (identifier,),
        )
    return {"ok": True}


@router.patch("/preferences/{identifier}")
async def toggle(identifier: UUID, body: Toggle, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        await lock(connection, owner)
        row = await owned(connection, owner, identifier, body.revision)
        if row["status"] == "forgotten":
            fail(409, "HABIT_FORGOTTEN", "已遗忘的习惯需明确重新学习")
        candidate = not row["skill_id"] and not row["manual_confirmed"]
        if body.enabled and not candidate and not row["manual_confirmed"] and await evidence_count(connection, row) < 3:
            fail(409, "HABIT_EVIDENCE_INSUFFICIENT", "有效来源不足三条，请重新学习")
        if body.enabled and row["manual_confirmed"] and not row["skill_id"]:
            fail(409, "HABIT_SKILL_MISSING", "已确认的派生技能被删除，请修正或重新学习")
        if row["skill_id"]:
            await connection.execute("UPDATE skills SET enabled=%s WHERE id=%s AND owner_id=%s",
                                     (body.enabled, row["skill_id"], owner))
        await connection.execute(
            "UPDATE habit_preferences SET status=%s,revision=revision+1,updated_at=now(),learning_after="
            "CASE WHEN status='disabled' AND %s THEN now() ELSE learning_after END WHERE id=%s",
            ("learning" if candidate and body.enabled else "active" if body.enabled else "disabled",
             body.enabled, identifier),
        )
    return {"ok": True}


@router.post("/preferences/{identifier}/relearn")
async def relearn(identifier: UUID, body: Revision, request: Request):
    owner = (await identity(request))["id"]
    async with request.app.state.db.pool.connection() as connection:
        await lock(connection, owner)
        row = await owned(connection, owner, identifier, body.revision)
        await revoke(connection, row, erase=True)
        await connection.execute("DELETE FROM habit_evidence WHERE preference_id=%s", (identifier,))
        await connection.execute(
            "UPDATE habit_preferences SET status='learning',value='',manual_confirmed=false,skill_id=NULL,"
            "skill_version=NULL,learning_after=now(),revision=revision+1,updated_at=now() WHERE id=%s", (identifier,),
        )
    return {"ok": True}
