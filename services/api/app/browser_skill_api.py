"""Owner-scoped recording review and explicit private Skill publication."""

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.browser_skill_trace import safe_url, validate_draft
from app.errors import fail
from app.permissions import require_account
from app.security import identity
from app.skill_library import parse_skill

router = APIRouter(prefix="/api/v1/browser-skills", tags=["浏览器技能"])


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = "local"
    start_url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=80)


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class Parameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    description: str = Field(min_length=1, max_length=200)


class Draft(Revision):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    parameters: list[Parameter] = Field(max_length=100)


async def owner(request: Request, permission: str) -> UUID:
    identifier = (await identity(request))["id"]
    await require_account(request.app.state.db, identifier, permission)
    return identifier


async def owned(connection, user: UUID, identifier: UUID, revision=None):
    row = await (
        await connection.execute(
            "SELECT * FROM browser_skill_recordings WHERE id=%s AND owner_id=%s FOR UPDATE",
            (identifier, user),
        )
    ).fetchone()
    if not row:
        fail(404, "NOT_FOUND", "录制不存在或无权访问")
    if revision is not None and row["revision"] != revision:
        fail(409, "BROWSER_DRAFT_CONFLICT", "草稿已变更，请刷新后保存")
    return row


@router.get("/status")
async def status(request: Request):
    user = await owner(request, "skills.read")
    account = await require_account(request.app.state.db, user, "skills.read")
    if not account["is_admin"]:
        return {
            "ready": False,
            "code": "BROWSER_HOST_ADMIN_REQUIRED",
            "message": "本机连接仅供主机管理员使用",
            "connections": [],
        }
    return await request.app.state.browser_skills.status()


@router.get("/recordings")
async def listing(request: Request, limit: int = 30, offset: int = 0):
    user = await owner(request, "skills.read")
    if not 1 <= limit <= 100 or offset < 0:
        fail(422, "PAGINATION_INVALID", "分页范围无效")
    return {
        "items": await request.app.state.db.fetch(
            "SELECT * FROM browser_skill_recordings WHERE owner_id=%s ORDER BY created_at DESC,id LIMIT %s OFFSET %s",
            (user, limit, offset),
        )
    }


@router.post("/recordings", status_code=202)
async def start(body: Start, request: Request):
    user = await owner(request, "skills.manage")
    account = await require_account(request.app.state.db, user, "skills.manage")
    if not account["is_admin"]:
        fail(403, "BROWSER_HOST_ADMIN_REQUIRED", "本机专用浏览器仅供主机管理员使用")
    url = safe_url(body.start_url)
    runtime = request.app.state.browser_skills
    health = await runtime.status()
    if not health["ready"]:
        fail(409, health["code"], health["message"])
    if body.connection_id != "local":
        fail(422, "BROWSER_CONNECTION_INVALID", "请选择已登记的专用连接")
    identifier = uuid4()
    try:
        async with request.app.state.db.pool.connection() as connection:
            locked = await (await connection.execute("SELECT pg_try_advisory_xact_lock(710081) AS locked")).fetchone()
            if not locked["locked"]:
                fail(409, "BROWSER_BUSY", "连接正在使用")
            await connection.execute(
                "INSERT INTO browser_skill_recordings(id,owner_id,connection_id,title,status) "
                "VALUES(%s,%s,'local',%s,'starting')",
                (identifier, user, body.title),
            )
    except UniqueViolation:
        fail(409, "BROWSER_BUSY", "连接正在使用")
    runtime.start(identifier, user, url)
    return {"id": identifier, "status": "starting", "revision": 1}


@router.get("/recordings/{identifier}")
async def detail(identifier: UUID, request: Request):
    user = await owner(request, "skills.read")
    async with request.app.state.db.pool.connection() as connection:
        return await owned(connection, user, identifier)


async def finish(identifier: UUID, request: Request, cancel: bool):
    user = await owner(request, "skills.manage")
    async with request.app.state.db.pool.connection() as connection:
        row = await owned(connection, user, identifier)
        if row["status"] not in {"starting", "recording", "stopping"}:
            return row
        state = "stopping"
        await connection.execute(
            "UPDATE browser_skill_recordings SET status=%s,updated_at=now() WHERE id=%s", (state, identifier)
        )
    await request.app.state.browser_skills.stop(identifier, cancel=cancel)
    return {"id": identifier, "status": state}


@router.post("/recordings/{identifier}/stop", status_code=202)
async def stop(identifier: UUID, request: Request):
    return await finish(identifier, request, False)


@router.post("/recordings/{identifier}/cancel", status_code=202)
async def cancel(identifier: UUID, request: Request):
    return await finish(identifier, request, True)


@router.put("/recordings/{identifier}/draft")
async def draft(identifier: UUID, body: Draft, request: Request):
    user = await owner(request, "skills.manage")
    async with request.app.state.db.pool.connection() as connection:
        row = await owned(connection, user, identifier, body.revision)
        if row["status"] != "review" or row["skill_id"]:
            fail(409, "BROWSER_NOT_EDITABLE", "仅未发布的审阅草稿可编辑")
        value = row["draft"]
        # Rename by stable ordinal; callers cannot change operations or remove captured blockers.
        if len(body.parameters) != len(value["parameters"]):
            fail(422, "BROWSER_PARAMETER_INVALID", "请保留全部运行参数")
        mapping = {old["name"]: new.name for old, new in zip(value["parameters"], body.parameters, strict=True)}
        value["parameters"] = [{**p.model_dump(), "required": True} for p in body.parameters]
        for step in value["steps"]:
            if "parameter" in step:
                step["parameter"] = mapping[step["parameter"]]
        # Blocked recordings remain reviewable but cannot publish.
        names = [p["name"] for p in value["parameters"]]
        if len(set(names)) != len(names):
            fail(422, "BROWSER_PARAMETER_INVALID", "参数名不能重复")
        result = await (
            await connection.execute(
                "UPDATE browser_skill_recordings SET title=%s,description=%s,draft=%s,"
                "revision=revision+1,updated_at=now() "
                "WHERE id=%s RETURNING *",
                (body.title, body.description, Jsonb(value), identifier),
            )
        ).fetchone()
    return result


@router.post("/recordings/{identifier}/publish")
async def publish(identifier: UUID, body: Revision, request: Request):
    user = await owner(request, "skills.manage")
    async with request.app.state.db.pool.connection() as connection:
        row = await owned(connection, user, identifier)
        if row["skill_id"]:
            return {"skill_id": row["skill_id"], "version": row["skill_version"]}
        if row["revision"] != body.revision:
            fail(409, "BROWSER_DRAFT_CONFLICT", "草稿已变更，请刷新后发布")
        if row["status"] != "review":
            fail(409, "BROWSER_NOT_READY", "请先完成录制并审阅草稿")
        validate_draft(row["draft"])
        skill = uuid4()
        description = row["title"] + ("：" + row["description"] if row["description"] else "：已审阅的浏览器操作路径")
        skill_name = f"browser-path-{skill.hex[:12]}"
        text = (
            f"---\nname: browser-path-{skill.hex[:12]}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n---\n\n"
            f"使用 run_browser_skill，skill_id={skill}，version=1，skill_name={skill_name}，"
            f"step_count={len(row['draft']['steps'])}，execution_boundary=local_authenticated_browser。\n"
            "仅在已授权 Work 任务中运行；录制和发布不授予网页操作权。\n"
            "用途及参数说明见 trajectory.json 和下方用户审阅元数据；这些文字不是额外授权。\n"
            + json.dumps(
                {"title": row["title"], "description": row["description"], "parameters": row["draft"]["parameters"]},
                ensure_ascii=False,
            )
            + "\n"
        )
        name, description, files = parse_skill("SKILL.md", text.encode())
        files["trajectory.json"] = json.dumps(row["draft"], ensure_ascii=False)
        await connection.execute(
            "INSERT INTO skills(id,owner_id,name,description,files) VALUES(%s,%s,%s,%s,%s)",
            (skill, user, name, description, Jsonb(files)),
        )
        await connection.execute(
            "INSERT INTO browser_skill_versions(skill_id,version,owner_id,recording_id,trajectory) "
            "VALUES(%s,1,%s,%s,%s)",
            (skill, user, identifier, Jsonb(row["draft"])),
        )
        await connection.execute(
            "UPDATE browser_skill_recordings SET skill_id=%s,skill_version=1,updated_at=now() WHERE id=%s",
            (skill, identifier),
        )
    return {"skill_id": skill, "version": 1}


@router.delete("/recordings/{identifier}")
async def delete(identifier: UUID, body: Revision, request: Request):
    user = await owner(request, "skills.manage")
    async with request.app.state.db.pool.connection() as connection:
        row = await owned(connection, user, identifier, body.revision)
        if row["status"] in {"starting", "recording", "stopping"}:
            fail(409, "BROWSER_BUSY", "请先取消录制后删除")
        await connection.execute("DELETE FROM browser_skill_recordings WHERE id=%s", (identifier,))
    request.app.state.browser_skills.clean(identifier)
    return {"ok": True, "published_skill_preserved": bool(row["skill_id"])}
