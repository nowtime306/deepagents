"""Password hashing and opaque, server-stored sessions."""

import hashlib
import hmac
import secrets

from fastapi import Request

from app.errors import fail
from app.permissions import account, route_permission

COOKIE = "owner_agent_session"


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Hash a password using a unique salt and scrypt."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    """Compare credentials without storing or returning the plaintext."""
    _, salt, _ = encoded.split("$")
    return hmac.compare_digest(hash_password(password, salt=salt), encoded)


def token_digest(token: str) -> str:
    """Store only a digest of an opaque session token."""
    return hashlib.sha256(token.encode()).hexdigest()


async def identity(request: Request) -> dict:
    """Resolve identity and role from the server session on every request."""
    token = request.cookies.get(COOKIE, "")
    rows = await request.app.state.db.fetch(
        "SELECT u.id FROM auth_sessions s JOIN users u ON u.id=s.owner_id "
        "WHERE s.token_hash=%s AND s.expires_at>now()",
        (token_digest(token),),
    )
    if not rows:
        fail(401, "AUTH_REQUIRED", "请先登录")
    user = await account(request.app.state.db, rows[0]["id"])
    request.state.actor = user
    own_account = request.url.path.startswith("/api/v1/auth/")
    if user["must_change_password"] and not own_account:
        fail(403, "PASSWORD_CHANGE_REQUIRED", "请先修改临时密码")
    permission = route_permission(request.url.path, request.method)
    if permission and not user["is_admin"] and permission not in user["permissions"]:
        fail(403, "PERMISSION_REQUIRED", "当前角色没有此功能的权限")
    return user


async def administrator(request: Request) -> dict:
    """Require a current administrator role for provider credentials and settings."""
    user = await identity(request)
    if not user["is_admin"]:
        fail(403, "ADMIN_REQUIRED", "仅平台管理员可以执行此操作")
    return user
