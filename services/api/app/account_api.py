"""Local registration, approval, feature roles and session administration."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.errors import fail
from app.permissions import PERMISSIONS
from app.security import COOKIE, administrator, hash_password, identity, token_digest, verify_password

router = APIRouter(prefix="/api/v1", tags=["账号与权限"])


class UsernameInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")


class Registration(UsernameInput):
    # Password whitespace is intentional, so it must not inherit string stripping.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    password: SecretStr = Field(min_length=12, max_length=256)
    invitation: str = Field(default="", max_length=160)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=12, max_length=256)


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_mode: Literal["open", "approval", "closed"]


class UserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: str = Field(min_length=1, max_length=80)
    enabled: bool
    approval_status: Literal["pending", "approved", "rejected"]


class RoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    permissions: list[str] = Field(max_length=len(PERMISSIONS))


class InvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    role_id: str = Field(default="member", max_length=80)
    valid_days: int = Field(default=7, ge=1, le=30)


async def throttle(request: Request, purpose: str, scope: str, limit: int, seconds: int) -> None:
    key = token_digest(purpose + ":" + scope)
    rows = await request.app.state.db.fetch(
        "INSERT INTO auth_rate_windows(key,hits,expires_at) VALUES(%s,1,now()+%s*interval '1 second') "
        "ON CONFLICT(key) DO UPDATE SET hits=CASE WHEN auth_rate_windows.expires_at<=now() THEN 1 "
        "ELSE auth_rate_windows.hits+1 END,expires_at=CASE WHEN auth_rate_windows.expires_at<=now() "
        "THEN now()+%s*interval '1 second' ELSE auth_rate_windows.expires_at END RETURNING hits",
        (key, seconds, seconds),
    )
    if rows[0]["hits"] > limit:
        fail(429, "AUTH_RATE_LIMIT", "尝试次数过多，请稍后重试")


async def admin_lock(connection, actor: UUID) -> None:
    """Reauthorize inside a global account/role write lock, including concurrent self-demotion."""
    await connection.execute("SELECT pg_advisory_xact_lock(710018)")
    cursor = await connection.execute(
        "SELECT id FROM users WHERE id=%s AND is_admin AND enabled AND approval_status='approved'",
        (actor,),
    )
    if not await cursor.fetchone():
        fail(403, "ADMIN_REQUIRED", "平台管理员权限已撤销")


async def selected_role(connection, identifier: str) -> dict:
    cursor = await connection.execute("SELECT * FROM platform_roles WHERE id=%s AND enabled", (identifier,))
    role = await cursor.fetchone()
    if not role:
        fail(422, "ROLE_INVALID", "请选择有效角色")
    return role


@router.get("/auth/registration")
async def registration_settings(request: Request) -> dict:
    rows = await request.app.state.db.fetch("SELECT registration_mode FROM account_settings WHERE id=1")
    return {**rows[0], "minimum_password_length": 12, "invitation_supported": True}


@router.post("/auth/register", status_code=201)
async def register(body: Registration, request: Request) -> dict:
    peer = request.client.host if request.client else "unknown"
    await throttle(request, "registration", peer, 20, 3600)
    username = body.username.lower()
    password = await asyncio.to_thread(hash_password, body.password.get_secret_value())
    identifier = uuid4()
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        await connection.execute("SELECT pg_advisory_xact_lock(710019,hashtext(%s))", (username,))
        cursor = await connection.execute("SELECT 1 FROM users WHERE lower(username)=%s", (username,))
        if await cursor.fetchone():
            fail(409, "USERNAME_UNAVAILABLE", "账号名称不可用，请更换名称")
        mode = await (await connection.execute("SELECT registration_mode FROM account_settings WHERE id=1")).fetchone()
        role, status, invitation = "member", "pending", None
        if body.invitation.strip():
            cursor = await connection.execute(
                "SELECT i.id,i.role_id FROM account_invitations i JOIN platform_roles r ON r.id=i.role_id "
                "WHERE i.token_hash=%s AND i.used_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>now() "
                "AND r.enabled FOR UPDATE OF i",
                (token_digest(body.invitation.strip()),),
            )
            invitation = await cursor.fetchone()
            if not invitation:
                fail(422, "INVITATION_INVALID", "邀请码无效、已使用或已过期")
            role, status = invitation["role_id"], "approved"
        elif mode["registration_mode"] == "closed":
            fail(403, "REGISTRATION_CLOSED", "当前注册已关闭，请向管理员申请邀请码")
        elif mode["registration_mode"] == "open":
            status = "approved"
        await connection.execute(
            "INSERT INTO users(id,username,password_hash,role_id,is_admin,approval_status) VALUES(%s,%s,%s,%s,%s,%s)",
            (identifier, username, password, role, role == "admin", status),
        )
        if invitation:
            await connection.execute(
                "UPDATE account_invitations SET used_by=%s,used_at=now() WHERE id=%s", (identifier, invitation["id"])
            )
    request.state.actor = {"id": identifier}
    return {
        "username": username,
        "approval_status": status,
        "message": "注册成功，请等待管理员审核" if status == "pending" else "注册成功，可以登录",
    }


@router.post("/auth/password")
async def change_password(body: PasswordInput, request: Request) -> JSONResponse:
    user = await identity(request)
    if body.current_password == body.new_password:
        fail(422, "PASSWORD_UNCHANGED", "新密码应与原密码不同")
    encoded = await asyncio.to_thread(hash_password, body.new_password.get_secret_value())
    async with request.app.state.db.pool.connection() as connection:
        cursor = await connection.execute("SELECT password_hash FROM users WHERE id=%s FOR UPDATE", (user["id"],))
        current = await cursor.fetchone()
        if not await asyncio.to_thread(
            verify_password, body.current_password.get_secret_value(), current["password_hash"]
        ):
            fail(403, "PASSWORD_INVALID", "原密码不正确")
        await connection.execute(
            "UPDATE users SET password_hash=%s,must_change_password=false WHERE id=%s", (encoded, user["id"])
        )
        await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (user["id"],))
    response = JSONResponse({"ok": True, "message": "密码已修改，所有旧会话已退出，请重新登录"})
    response.delete_cookie(COOKIE)
    return response


@router.get("/auth/sessions")
async def sessions(request: Request) -> dict:
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,created_at,expires_at,token_hash=%s AS current FROM auth_sessions "
            "WHERE owner_id=%s AND expires_at>now() ORDER BY expires_at DESC",
            (token_digest(request.cookies.get(COOKIE, "")), user["id"]),
        )
    }


@router.delete("/auth/sessions/{identifier}")
async def revoke_session(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM auth_sessions WHERE id=%s AND owner_id=%s RETURNING id", (identifier, user["id"])
    )
    if not rows:
        fail(404, "SESSION_NOT_FOUND", "会话不存在或无权访问")
    return {"ok": True}


@router.get("/admin/accounts")
async def users(
    request: Request, q: str = Query(default="", max_length=80), page: int = Query(default=0, ge=0)
) -> dict:
    await administrator(request)
    rows = await request.app.state.db.fetch(
        "SELECT u.id,u.username,u.is_admin,u.enabled,u.approval_status,u.must_change_password,u.role_id,"
        "u.created_at,r.name AS role_name,count(*) OVER()::int AS total FROM users u "
        "JOIN platform_roles r ON r.id=u.role_id WHERE strpos(lower(u.username),lower(%s))>0 "
        "ORDER BY u.created_at DESC,u.id DESC LIMIT 50 OFFSET %s",
        (q.strip(), page * 50),
    )
    return {"items": rows, "page": page, "total": rows[0]["total"] if rows else 0}


@router.post("/admin/accounts", status_code=201)
async def create_account(body: UsernameInput, request: Request) -> dict:
    actor = await administrator(request)
    temporary = secrets.token_urlsafe(18)
    encoded = await asyncio.to_thread(hash_password, temporary)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        username = body.username.lower()
        await connection.execute("SELECT pg_advisory_xact_lock(710019,hashtext(%s))", (username,))
        cursor = await connection.execute("SELECT 1 FROM users WHERE lower(username)=%s", (username,))
        if await cursor.fetchone():
            fail(409, "USERNAME_UNAVAILABLE", "账号名称不可用")
        identifier = uuid4()
        await connection.execute(
            "INSERT INTO users(id,username,password_hash,must_change_password) VALUES(%s,%s,%s,true)",
            (identifier, username, encoded),
        )
    return {"id": identifier, "username": username, "temporary_password": temporary}


@router.put("/admin/accounts/{identifier}")
async def update_account(identifier: UUID, body: UserInput, request: Request) -> dict:
    actor = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        await selected_role(connection, body.role_id)
        cursor = await connection.execute("SELECT * FROM users WHERE id=%s FOR UPDATE", (identifier,))
        current = await cursor.fetchone()
        if not current:
            fail(404, "ACCOUNT_NOT_FOUND", "账号不存在")
        active_admin = body.role_id == "admin" and body.enabled and body.approval_status == "approved"
        if current["is_admin"] and current["enabled"] and current["approval_status"] == "approved" and not active_admin:
            cursor = await connection.execute(
                "SELECT count(*) AS n FROM users WHERE is_admin AND enabled AND approval_status='approved'"
            )
            if (await cursor.fetchone())["n"] <= 1:
                fail(409, "LAST_ADMIN", "必须保留至少一个可用的平台管理员")
        await connection.execute(
            "UPDATE users SET role_id=%s,is_admin=%s,enabled=%s,approval_status=%s WHERE id=%s",
            (body.role_id, body.role_id == "admin", body.enabled, body.approval_status, identifier),
        )
        await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (identifier,))
    return {"ok": True}


@router.post("/admin/accounts/{identifier}/reset-password")
async def reset_password(identifier: UUID, request: Request) -> dict:
    actor = await administrator(request)
    if identifier == actor["id"]:
        fail(422, "USE_OWN_PASSWORD", "请通过个人设置修改本人密码")
    temporary = secrets.token_urlsafe(18)
    encoded = await asyncio.to_thread(hash_password, temporary)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        cursor = await connection.execute(
            "UPDATE users SET password_hash=%s,must_change_password=true WHERE id=%s RETURNING id",
            (encoded, identifier),
        )
        if not await cursor.fetchone():
            fail(404, "ACCOUNT_NOT_FOUND", "账号不存在")
        await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (identifier,))
    return {"temporary_password": temporary, "message": "仅显示本次，登录后必须修改"}


@router.delete("/admin/accounts/{identifier}/sessions")
async def revoke_all(identifier: UUID, request: Request) -> dict:
    actor = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (identifier,))
    return {"ok": True}


@router.get("/admin/account-settings")
async def settings(request: Request) -> dict:
    await administrator(request)
    return (await request.app.state.db.fetch("SELECT registration_mode FROM account_settings WHERE id=1"))[0]


@router.put("/admin/account-settings")
async def save_settings(body: SettingsInput, request: Request) -> dict:
    actor = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        await connection.execute(
            "UPDATE account_settings SET registration_mode=%s WHERE id=1", (body.registration_mode,)
        )
    return {"ok": True}


@router.get("/admin/roles")
async def roles(request: Request) -> dict:
    await administrator(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT * FROM platform_roles WHERE enabled ORDER BY built_in DESC,created_at,id"
        ),
        "permissions": PERMISSIONS,
    }


async def save_role(body: RoleInput, request: Request, identifier: str | None = None) -> dict:
    actor = await administrator(request)
    if set(body.permissions) - set(PERMISSIONS):
        fail(422, "PERMISSION_INVALID", "包含未知或管理员专属权限")
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        if identifier:
            role = await selected_role(connection, identifier)
            if role["built_in"]:
                fail(409, "BUILT_IN_ROLE", "预设角色不可修改，请创建自定义角色")
            await connection.execute(
                "UPDATE platform_roles SET name=%s,permissions=%s WHERE id=%s",
                (body.name, Jsonb(sorted(set(body.permissions))), identifier),
            )
            await connection.execute(
                "DELETE FROM auth_sessions WHERE owner_id IN (SELECT id FROM users WHERE role_id=%s AND NOT is_admin)",
                (identifier,),
            )
        else:
            identifier = str(uuid4())
            await connection.execute(
                "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
                (identifier, body.name, Jsonb(sorted(set(body.permissions)))),
            )
    return {"id": identifier}


@router.post("/admin/roles", status_code=201)
async def create_role(body: RoleInput, request: Request) -> dict:
    return await save_role(body, request)


@router.put("/admin/roles/{identifier}")
async def edit_role(identifier: str, body: RoleInput, request: Request) -> dict:
    return await save_role(body, request, identifier)


@router.delete("/admin/roles/{identifier}")
async def delete_role(identifier: str, request: Request) -> dict:
    actor = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        role = await selected_role(connection, identifier)
        if role["built_in"]:
            fail(409, "BUILT_IN_ROLE", "预设角色不可删除")
        cursor = await connection.execute("SELECT 1 FROM users WHERE role_id=%s LIMIT 1", (identifier,))
        if await cursor.fetchone():
            fail(409, "ROLE_IN_USE", "请先将此角色的账号分配给其他角色")
        await connection.execute("UPDATE platform_roles SET enabled=false WHERE id=%s", (identifier,))
    return {"ok": True}


@router.get("/admin/invitations")
async def invitations(request: Request) -> dict:
    await administrator(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT i.id,i.name,i.token_hint,i.role_id,r.name AS role_name,"
            "i.created_at,i.expires_at,i.used_at,i.revoked_at "
            "FROM account_invitations i JOIN platform_roles r ON r.id=i.role_id ORDER BY i.created_at DESC LIMIT 100"
        )
    }


@router.post("/admin/invitations", status_code=201)
async def invite(body: InvitationInput, request: Request) -> dict:
    actor = await administrator(request)
    token, identifier = secrets.token_urlsafe(24), uuid4()
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        await selected_role(connection, body.role_id)
        await connection.execute(
            "INSERT INTO account_invitations(id,name,token_hash,token_hint,role_id,created_by,expires_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier,
                body.name,
                token_digest(token),
                token[-4:],
                body.role_id,
                actor["id"],
                datetime.now(UTC) + timedelta(days=body.valid_days),
            ),
        )
    return {"id": identifier, "invitation": token}


@router.delete("/admin/invitations/{identifier}")
async def revoke_invitation(identifier: UUID, request: Request) -> dict:
    actor = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        await connection.execute(
            "UPDATE account_invitations SET revoked_at=COALESCE(revoked_at,now()) WHERE id=%s", (identifier,)
        )
    return {"ok": True}
