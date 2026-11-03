"""Private repository resources and read-only connection checks."""

import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.errors import fail
from app.list_pagination import decode_cursor, page
from app.repository_provider import provider, source_path
from app.security import identity

router = APIRouter(prefix="/api/v1/repositories", tags=["代码仓库"])
PUBLIC = "id,name,slug,ref,subdirectory,enabled,revision,created_at,updated_at,(token_ciphertext<>'') AS has_token"


class RepositoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=3, max_length=200)
    ref: str = Field(default="main", min_length=1, max_length=200)
    subdirectory: str = Field(default="", max_length=400)
    token: SecretStr | None = Field(default=None, max_length=8192)
    clear_token: bool = False
    enabled: bool = True
    revision: int | None = Field(default=None, ge=1)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9_.-]{1,100}", value) or value.endswith(".git"):
            raise ValueError("请填写GitHub仓库标识，例如组织名/仓库名，不填写网址或.git后缀")
        return value

    @field_validator("ref")
    @classmethod
    def valid_ref(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.-]*", value) or ".." in value or "//" in value:
            raise ValueError("请填写有效分支、标签或完整提交SHA")
        return value

    @field_validator("subdirectory")
    @classmethod
    def valid_directory(cls, value: str) -> str:
        return source_path(value) if value else ""

    @model_validator(mode="after")
    def secret_intent(self):
        if self.clear_token and self.token and self.token.get_secret_value():
            raise ValueError("不能同时设置和清除令牌")
        return self


async def owned(request: Request, identifier: UUID, *, enabled: bool = False) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch("SELECT * FROM repositories WHERE id=%s AND owner_id=%s",
                                          (identifier, user["id"]))
    if not rows:
        fail(404, "NOT_FOUND", "仓库不存在或无权访问")
    if enabled and not rows[0]["enabled"]:
        fail(409, "REPOSITORY_DISABLED", "仓库已停用")
    return rows[0]


@router.get("")
async def listing(request: Request, before: str | None = Query(default=None, max_length=1000),
                  q: str = Query(default="", max_length=200)) -> dict:
    user = await identity(request)
    stamp, cursor = decode_cursor(before, user["id"], "repositories")
    rows = await request.app.state.db.fetch(
        "SELECT " + PUBLIC + " FROM repositories WHERE owner_id=%s "
        "AND (%s='' OR strpos(lower(name),lower(%s))>0 OR strpos(lower(slug),lower(%s))>0) "
        "AND (%s::timestamptz IS NULL OR (updated_at,id)<(%s,%s::uuid)) "
        "ORDER BY updated_at DESC,id DESC LIMIT 101", (user["id"],q,q,q,stamp,stamp,cursor))
    return page(rows, 100, user["id"], "repositories", time_key="updated_at")


@router.get("/options")
async def options(request: Request, before: str | None = Query(default=None, max_length=1000)) -> dict:
    user = await identity(request)
    stamp, cursor = decode_cursor(before, user["id"], "repository-options")
    rows = await request.app.state.db.fetch(
        "SELECT id,name,slug,updated_at FROM repositories WHERE owner_id=%s AND enabled "
        "AND (%s::timestamptz IS NULL OR (updated_at,id)<(%s,%s::uuid)) "
        "ORDER BY updated_at DESC,id DESC LIMIT 101", (user["id"],stamp,stamp,cursor))
    return page(rows, 100, user["id"], "repository-options", time_key="updated_at")


async def save(request: Request, body: RepositoryInput, identifier: UUID | None = None) -> dict:
    user = await identity(request)
    vault = request.app.state.models.vault
    secret = body.token.get_secret_value() if body.token else ""
    token = vault.encrypt(secret) if secret else "" if body.clear_token or identifier is None else None
    db = request.app.state.db
    if identifier is None:
        identifier = uuid4()
        await db.execute("INSERT INTO repositories(id,owner_id,name,slug,ref,subdirectory,token_ciphertext,enabled) "
                         "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                         (identifier, user["id"], body.name, body.slug, body.ref,
                          body.subdirectory, token, body.enabled))
    else:
        rows = await db.fetch("UPDATE repositories SET name=%s,slug=%s,ref=%s,subdirectory=%s,enabled=%s,"
            "token_ciphertext=COALESCE(%s,token_ciphertext),revision=revision+1,updated_at=now() "
            "WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id",
            (body.name, body.slug, body.ref, body.subdirectory, body.enabled,
             token, identifier, user["id"], body.revision))
        if not rows:
            fail(409, "REPOSITORY_CONFLICT", "仓库不存在或配置已修改，请刷新")
    return {"id": identifier}


@router.post("", status_code=201)
async def create(body: RepositoryInput, request: Request) -> dict:
    return await save(request, body)


@router.put("/{identifier}")
async def update(identifier: UUID, body: RepositoryInput, request: Request) -> dict:
    return await save(request, body, identifier)


@router.delete("/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    row = await owned(request, identifier)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT id FROM repositories WHERE id=%s FOR UPDATE", (identifier,))
        if await (await connection.execute("SELECT 1 FROM work_run_repositories WHERE repository_id=%s LIMIT 1",
                                          (identifier,))).fetchone():
            fail(409, "REPOSITORY_HAS_RUNS", "仓库已有任务记录，请停用以保留来源与审阅记录")
        await connection.execute("DELETE FROM repositories WHERE id=%s AND owner_id=%s", (identifier, row["owner_id"]))
    return {"ok": True}


@router.post("/{identifier}/test")
async def probe(identifier: UUID, request: Request) -> dict:
    row = await owned(request, identifier, enabled=True)
    result = await provider(request, row).snapshot()
    current = await owned(request, identifier, enabled=True)
    if current["revision"] != row["revision"]:
        fail(409, "REPOSITORY_CONFLICT", "检测期间配置已更改，请重试")
    return {"success": True, "sha": result["sha"], "files": len(result["files"]),
            "message": "只读检出检查通过；没有写入远端仓库"}
