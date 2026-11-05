"""TOTP enrollment and single-use challenges; never issue a session before MFA."""

import asyncio
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.account_api import throttle
from app.errors import fail
from app.models import CredentialVault
from app.permissions import account
from app.security import cookie_name, identity, token_digest, verify_password

router = APIRouter(prefix="/api/v1/auth/security", tags=["个人安全"])


class Proof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(default=SecretStr(""), max_length=256)
    code: str = Field(default="", max_length=128)


class Challenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=6, max_length=128)


class CancelChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: str = Field(default="", max_length=200)


def vault(request):
    return CredentialVault(request.app.state.settings.data_dir / "model.key", create=False)


def totp(secret, seconds=None, digits=6):
    key = base64.b32decode(secret + "=" * ((-len(secret)) % 8))
    step = int(time.time() if seconds is None else seconds) // 30
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)).zfill(digits)


def match_step(secret, code, last_step, now=None):
    step = int(time.time() if now is None else now) // 30
    for candidate in (step, step - 1, step + 1):
        if candidate > last_step and hmac.compare_digest(totp(secret, candidate * 30), code):
            return candidate
    return None


async def password_proof(request, user, password):
    await throttle(request, "security-proof", str(user["id"]), 10, 300)
    if not password.get_secret_value():
        recent = await request.app.state.db.fetch(
            "SELECT u.password_hash FROM auth_sessions s JOIN users u ON u.id=s.owner_id "
            "JOIN identity_provider_sessions p ON p.token_hash=s.token_hash "
            "JOIN identity_providers i ON i.id=p.provider_id WHERE s.token_hash=%s AND s.owner_id=%s "
            "AND s.created_at>now()-interval '5 minutes' AND s.expires_at>now() AND i.enabled",
            (token_digest(request.cookies.get(cookie_name(request), "")), user["id"]),
        )
        if recent:
            return token_digest(recent[0]["password_hash"])
    row = (await request.app.state.db.fetch("SELECT password_hash FROM users WHERE id=%s", (user["id"],)))[0]
    if not await asyncio.to_thread(verify_password, password.get_secret_value(), row["password_hash"]):
        fail(401, "INVALID_CREDENTIALS", "当前密码不正确")
    return token_digest(row["password_hash"])


async def login_challenge(connection, request, user, provider=None):
    row = await (
        await connection.execute("SELECT enabled FROM identity_mfa WHERE owner_id=%s", (user["id"],))
    ).fetchone()
    if not row or not row["enabled"]:
        return None
    token = secrets.token_urlsafe(32)
    credentials = await (
        await connection.execute("SELECT password_hash FROM users WHERE id=%s", (user["id"],))
    ).fetchone()
    await connection.execute("DELETE FROM identity_challenges WHERE expires_at<=now()")
    await connection.execute(
        "INSERT INTO identity_challenges(token_hash,owner_id,expires_at,password_version) "
        "VALUES(%s,%s,now()+interval '5 minutes',%s)",
        (token_digest(token), user["id"], token_digest(credentials["password_hash"])),
    )
    if provider:
        await connection.execute(
            "UPDATE identity_challenges SET provider_id=%s,provider_revision=%s WHERE token_hash=%s",
            (provider["id"], provider["revision"], token_digest(token)),
        )
    return JSONResponse({"mfa_required": True, "challenge": token}, status_code=202)


async def consume_code(connection, request, owner, code):
    row = await (
        await connection.execute("SELECT * FROM identity_mfa WHERE owner_id=%s AND enabled FOR UPDATE", (owner,))
    ).fetchone()
    if not row:
        fail(401, "MFA_INVALID", "验证凭据已失效")
    step = match_step(vault(request).decrypt(row["secret_ciphertext"]).get_secret_value(), code, row["last_step"])
    if step is not None:
        await connection.execute("UPDATE identity_mfa SET last_step=%s WHERE owner_id=%s", (step, owner))
        return
    digest = token_digest(code)
    remaining = row["recovery_hashes"]
    selected = next((value for value in remaining if hmac.compare_digest(value, digest)), None)
    if not selected:
        fail(401, "MFA_INVALID", "验证码错误、已使用或已过期")
    await connection.execute(
        "UPDATE identity_mfa SET recovery_hashes=%s WHERE owner_id=%s",
        (Jsonb([value for value in remaining if value != selected]), owner),
    )


async def issue_session(connection, request, user, provider_id=None):
    token = secrets.token_urlsafe(32)
    await connection.execute(
        "INSERT INTO auth_sessions(token_hash,owner_id,expires_at) VALUES(%s,%s,%s)",
        (token_digest(token), user["id"], datetime.now(UTC) + timedelta(hours=12)),
    )
    request.state.actor = user
    if provider_id:
        await connection.execute(
            "INSERT INTO identity_provider_sessions VALUES(%s,%s)", (token_digest(token), provider_id)
        )
    response = JSONResponse(jsonable_encoder(user))
    response.set_cookie(
        cookie_name(request), token,
        httponly=True, secure=request.app.state.settings.cookie_secure, samesite="strict", max_age=43200
    )
    response.delete_cookie(cookie_name(request, "challenge"))
    return response


@router.get("/pending")
async def pending(request: Request):
    token = request.cookies.get(cookie_name(request, "challenge"), "")
    rows = (
        await request.app.state.db.fetch(
            "SELECT t.password_version,u.password_hash FROM identity_challenges t JOIN users u ON u.id=t.owner_id "
            "JOIN identity_mfa m ON m.owner_id=u.id LEFT JOIN identity_providers p ON p.id=t.provider_id "
            "WHERE t.token_hash=%s AND t.expires_at>now() AND u.enabled AND u.approval_status='approved' AND m.enabled "
            "AND (t.provider_id IS NULL OR (p.enabled AND p.revision=t.provider_revision))",
            (token_digest(token),),
        )
        if token
        else []
    )
    valid = bool(rows and token_digest(rows[0]["password_hash"]) == rows[0]["password_version"])
    response = JSONResponse(
        {"challenge": token if valid else "", "mfa_required": valid}, headers={"Cache-Control": "no-store"}
    )
    if not valid:
        response.delete_cookie(cookie_name(request, "challenge"))
    return response


@router.post("/pending/cancel")
async def cancel_pending(body: CancelChallenge, request: Request):
    tokens = {body.challenge, request.cookies.get(cookie_name(request, "challenge"), "")} - {""}
    async with request.app.state.db.pool.connection() as connection:
        for token in tokens:
            await connection.execute("DELETE FROM identity_challenges WHERE token_hash=%s", (token_digest(token),))
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(cookie_name(request, "challenge"))
    return response


@router.get("")
async def status(request: Request):
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT enabled,jsonb_array_length(recovery_hashes) AS recovery_remaining FROM identity_mfa WHERE owner_id=%s",
        (user["id"],),
    )
    return rows[0] if rows else {"enabled": False, "recovery_remaining": 0}


@router.post("/mfa/enroll")
async def enroll(body: Proof, request: Request):
    user = await identity(request)
    await password_proof(request, user, body.password)
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        rows = await (
            await connection.execute("SELECT enabled FROM identity_mfa WHERE owner_id=%s FOR UPDATE", (user["id"],))
        ).fetchone()
        if rows and rows["enabled"]:
            fail(409, "MFA_ALREADY_ENABLED", "请先验证并关闭已有验证器")
        await connection.execute(
            "INSERT INTO identity_mfa(owner_id,secret_ciphertext) VALUES(%s,%s) ON "
            "CONFLICT(owner_id) DO UPDATE SET "
            "secret_ciphertext=excluded.secret_ciphertext,last_step=-1",
            (user["id"], vault(request).encrypt(secret)),
        )
    return {
        "secret": secret,
        "otpauth_uri": (
            f"otpauth://totp/{quote('知行:' + user['username'])}?secret={secret}"
            f"&issuer={quote('知行')}&digits=6&period=30"
        ),
    }


@router.post("/mfa/confirm")
async def confirm(body: Proof, request: Request):
    user = await identity(request)
    await password_proof(request, user, body.password)
    codes = [secrets.token_hex(8) for _ in range(8)]
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        row = await (
            await connection.execute(
                "SELECT * FROM identity_mfa WHERE owner_id=%s AND NOT enabled FOR UPDATE", (user["id"],)
            )
        ).fetchone()
        step = (
            match_step(vault(request).decrypt(row["secret_ciphertext"]).get_secret_value(), body.code, -1)
            if row
            else None
        )
        if step is None:
            fail(401, "MFA_INVALID", "请填写验证器当前验证码")
        await connection.execute(
            "UPDATE identity_mfa SET enabled=true,last_step=%s,recovery_hashes=%s WHERE owner_id=%s",
            (step, Jsonb([token_digest(code) for code in codes]), user["id"]),
        )
        await connection.execute(
            "DELETE FROM auth_sessions WHERE owner_id=%s AND token_hash<>%s",
            (user["id"], token_digest(request.cookies.get(cookie_name(request), ""))),
        )
    return {"recovery_codes": codes, "note": "仅显示一次，请离线妥善保存"}


@router.post("/mfa/disable")
async def disable(body: Proof, request: Request):
    user = await identity(request)
    await password_proof(request, user, body.password)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        await consume_code(connection, request, user["id"], body.code)
        await connection.execute("DELETE FROM identity_mfa WHERE owner_id=%s", (user["id"],))
        await connection.execute("DELETE FROM identity_challenges WHERE owner_id=%s", (user["id"],))
        await connection.execute(
            "DELETE FROM auth_sessions WHERE owner_id=%s AND token_hash<>%s",
            (user["id"], token_digest(request.cookies.get(cookie_name(request), ""))),
        )
    return {"ok": True}


@router.post("/challenge")
async def challenge(body: Challenge, request: Request):
    peer = request.client.host if request.client else "unknown"
    await throttle(request, "mfa-ip", peer, 30, 300)
    await throttle(request, "mfa-challenge", token_digest(body.challenge), 8, 300)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        row = await (
            await connection.execute(
                "SELECT * FROM identity_challenges WHERE token_hash=%s AND expires_at>now() FOR UPDATE",
                (token_digest(body.challenge),),
            )
        ).fetchone()
        if not row:
            fail(401, "MFA_INVALID", "验证请求已失效，请重新登录")
        user = await account(request.app.state.db, row["owner_id"])
        credentials = await (
            await connection.execute("SELECT password_hash FROM users WHERE id=%s", (user["id"],))
        ).fetchone()
        if token_digest(credentials["password_hash"]) != row["password_version"]:
            fail(401, "MFA_INVALID", "密码已变化，请重新登录")
        if row["provider_id"]:
            provider = await (
                await connection.execute(
                    "SELECT id FROM identity_providers WHERE id=%s AND revision=%s AND enabled",
                    (row["provider_id"], row["provider_revision"]),
                )
            ).fetchone()
            if not provider:
                fail(401, "OIDC_UNAVAILABLE", "身份来源配置已失效")
        await consume_code(connection, request, user["id"], body.code)
        await connection.execute("DELETE FROM identity_challenges WHERE token_hash=%s", (token_digest(body.challenge),))
        return await issue_session(connection, request, user, row["provider_id"])
