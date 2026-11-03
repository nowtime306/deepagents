"""Verified contacts and single-use recovery without silently disabling MFA."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.account_api import admin_lock, throttle
from app.errors import fail
from app.identity_api import consume_code, password_proof, vault
from app.identity_oidc import endpoint
from app.security import administrator, hash_password, identity, token_digest

router = APIRouter(prefix="/api/v1", tags=["验证联系方式与找回"])


class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    configuration: SecretStr | None = None
    enabled: bool = False
    clear_credential: bool = False


class Contact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["email", "sms"]
    address: str = Field(min_length=3, max_length=254)
    password: SecretStr


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    kind: Literal["email", "sms"]


class Token(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)


class Reset(Token):
    password: SecretStr = Field(min_length=12, max_length=256)
    mfa_code: str = Field(default="", max_length=128)


def validate_configuration(kind, config, *, enabled=True):
    if kind == "sms":
        if not isinstance(config.get("url"), str) or not isinstance(config.get("secret", ""), str):
            raise ValueError("SMS_CONFIG_INVALID")
        endpoint(config["url"])
        if enabled and not config.get("secret"):
            raise ValueError("SMS_SIGNATURE_REQUIRED")
    else:
        if (
            not isinstance(config.get("host"), str)
            or not config["host"]
            or not isinstance(config.get("sender"), str)
            or not config["sender"]
            or not isinstance(config.get("username", ""), str)
            or not isinstance(config.get("password", ""), str)
            or not isinstance(config.get("starttls", True), bool)
        ):
            raise ValueError("SMTP_CONFIG_REQUIRED")
        if not 1 <= int(config.get("port", 587)) <= 65535:
            raise ValueError("SMTP_PORT_INVALID")
        if not config.get("starttls", True) and config["host"] not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("SMTP_TLS_REQUIRED")
        if enabled and config.get("username") and not config.get("password"):
            raise ValueError("SMTP_PASSWORD_REQUIRED")


def email_send(config, address, token, purpose):
    message = EmailMessage()
    message["From"] = config["sender"]
    message["To"] = address
    message["Subject"] = "知行安全验证"
    message.set_content(
        f"{'验证联系方式' if purpose == 'verify' else '找回密码'}验证码：{token}\n10分钟内有效，仅限本人使用。"
    )
    with smtplib.SMTP(config["host"], int(config.get("port", 587)), timeout=10) as client:
        if config.get("starttls", True):
            client.starttls(context=ssl.create_default_context())
        if config.get("username"):
            client.login(config["username"], config.get("password", ""))
        client.send_message(message)


async def send(request, kind, address, token, purpose):
    rows = await request.app.state.db.fetch("SELECT * FROM identity_channels WHERE kind=%s AND enabled", (kind,))
    if not rows:
        fail(503, "RECOVERY_CHANNEL_UNAVAILABLE", "该找回渠道未开通，请使用其他恢复方式或联系管理员")
    config = json.loads(vault(request).decrypt(rows[0]["config_ciphertext"]).get_secret_value())
    if kind == "email":
        operation = asyncio.create_task(asyncio.to_thread(email_send, config, address, token, purpose))
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            # SMTP's synchronous socket may already be sending. Retain the caller's maintenance
            # gate until its bounded I/O has finished; never replay an uncertain delivery.
            with contextlib.suppress(Exception):
                await operation
            raise
    else:
        payload = json.dumps(
            {"recipient": address, "token": token, "purpose": purpose}, separators=(",", ":"), ensure_ascii=False
        ).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(config["secret"].encode(), stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                endpoint(config["url"]),
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Identity-Timestamp": stamp,
                    "X-Identity-Signature": signature,
                },
            )
            response.raise_for_status()


@router.get("/admin/identity/channels")
async def channels(request: Request):
    await administrator(request)
    return {"items": await request.app.state.db.fetch("SELECT kind,name,enabled FROM identity_channels ORDER BY kind")}


@router.put("/admin/identity/channels/{kind}")
async def save_channel(kind: Literal["email", "sms"], body: Channel, request: Request):
    actor = await administrator(request)
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        old = await (
            await connection.execute("SELECT config_ciphertext FROM identity_channels WHERE kind=%s", (kind,))
        ).fetchall()
        encrypted = old[0]["config_ciphertext"] if old else ""
        config = json.loads(vault(request).decrypt(encrypted).get_secret_value()) if encrypted else {}
        credential = "secret" if kind == "sms" else "password"
        if body.configuration and body.configuration.get_secret_value():
            try:
                supplied = json.loads(body.configuration.get_secret_value())
                if not isinstance(supplied, dict):
                    raise ValueError("CHANNEL_OBJECT_REQUIRED")
                if not supplied.get(credential):
                    supplied.pop(credential, None)
                config.update(supplied)
            except (ValueError, KeyError, TypeError):
                fail(422, "CHANNEL_CONFIG_INVALID", "请检查邮件或签名短信渠道配置")
        if body.clear_credential:
            config.pop(credential, None)
        if not config:
            fail(422, "CHANNEL_CONFIG_REQUIRED", "首次配置需要填写渠道参数")
        try:
            validate_configuration(kind, config, enabled=body.enabled)
        except (ValueError, KeyError, TypeError):
            fail(422, "CHANNEL_CONFIG_INVALID", "请检查渠道参数；启用前必须填写所需凭证")
        encrypted = vault(request).encrypt(json.dumps(config))
        await connection.execute(
            "INSERT INTO identity_channels VALUES(%s,%s,%s,%s) ON CONFLICT(kind) DO "
            "UPDATE SET "
            "name=excluded.name,config_ciphertext=excluded.config_ciphertext,enabled=excluded.enabled",
            (kind, body.name, encrypted, body.enabled),
        )
        return {"ok": True}


@router.get("/auth/security/contacts")
async def contacts(request: Request):
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT kind,verified FROM identity_contacts WHERE owner_id=%s", (user["id"],)
        )
    }


@router.post("/auth/security/contacts")
async def add_contact(body: Contact, request: Request):
    user = await identity(request)
    await password_proof(request, user, body.password)
    await throttle(request, "contact-send", str(user["id"]), 3, 300)
    if any(c in body.address for c in "\r\n") or (body.kind == "email" and "@" not in body.address):
        fail(422, "CONTACT_INVALID", "联系方式格式不正确")
    token = secrets.token_urlsafe(24)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        exists = await (
            await connection.execute(
                "SELECT owner_id FROM identity_contacts WHERE kind=%s AND address_hash=%s",
                (body.kind, token_digest(body.address.lower())),
            )
        ).fetchone()
        if exists and exists["owner_id"] != user["id"]:
            fail(409, "CONTACT_UNAVAILABLE", "此联系方式不可用于当前账号")
        await connection.execute(
            "INSERT INTO identity_pending_contacts VALUES(%s,%s,%s,%s) ON "
            "CONFLICT(owner_id,kind) DO UPDATE SET "
            "address_ciphertext=excluded.address_ciphertext,address_hash=excluded.address_hash",
            (user["id"], body.kind, vault(request).encrypt(body.address), token_digest(body.address.lower())),
        )
        await connection.execute(
            "DELETE FROM identity_recovery_tokens WHERE owner_id=%s AND kind=%s", (user["id"], body.kind)
        )
        await connection.execute(
            "INSERT INTO identity_recovery_tokens(token_hash,owner_id,kind,purpose,expires_at) "
            "VALUES(%s,%s,%s,'verify',now()+interval '10 minutes')",
            (token_digest(token), user["id"], body.kind),
        )
    await send(request, body.kind, body.address, token, "verify")
    return {"accepted": True}


@router.post("/auth/security/contacts/verify")
async def verify_contact(body: Token, request: Request):
    user = await identity(request)
    await throttle(request, "contact-verify", str(user["id"]), 10, 300)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        row = await (
            await connection.execute(
                "DELETE FROM identity_recovery_tokens WHERE token_hash=%s AND owner_id=%s AND "
                "purpose='verify' AND expires_at>now() RETURNING kind",
                (token_digest(body.token), user["id"]),
            )
        ).fetchone()
        if not row:
            fail(401, "TOKEN_INVALID", "验证凭据错误、过期或已使用")
        pending = await (
            await connection.execute(
                "SELECT * FROM identity_pending_contacts WHERE owner_id=%s AND kind=%s FOR UPDATE",
                (user["id"], row["kind"]),
            )
        ).fetchone()
        if not pending:
            fail(401, "TOKEN_INVALID", "待验证联系方式已变化")
        other = await (
            await connection.execute(
                "SELECT owner_id FROM identity_contacts WHERE kind=%s AND address_hash=%s",
                (row["kind"], pending["address_hash"]),
            )
        ).fetchone()
        if other and other["owner_id"] != user["id"]:
            fail(409, "CONTACT_UNAVAILABLE", "此联系方式不可用于当前账号")
        await connection.execute(
            "INSERT INTO identity_contacts VALUES(%s,%s,%s,%s,true) ON CONFLICT(owner_id,kind) DO UPDATE "
            "SET address_ciphertext=excluded.address_ciphertext,address_hash=excluded.address_hash,verified=true",
            (user["id"], row["kind"], pending["address_ciphertext"], pending["address_hash"]),
        )
        await connection.execute(
            "DELETE FROM identity_pending_contacts WHERE owner_id=%s AND kind=%s", (user["id"], row["kind"])
        )
        await connection.execute(
            "DELETE FROM identity_recovery_tokens WHERE owner_id=%s AND kind=%s AND purpose='reset'",
            (user["id"], row["kind"]),
        )
    return {"ok": True}


@router.post("/auth/identity/recovery/request")
async def request_reset(body: RecoveryRequest, request: Request):
    await throttle(request, "reset-ip", request.client.host if request.client else "unknown", 10, 300)
    await throttle(request, "reset-account", body.username.lower(), 3, 300)
    db = request.app.state.db
    if not await db.fetch("SELECT 1 FROM identity_channels WHERE kind=%s AND enabled", (body.kind,)):
        fail(503, "RECOVERY_CHANNEL_UNAVAILABLE", "该找回渠道未开通，请使用恢复码或联系管理员")
    # Both known and unknown accounts complete only database submission, never provider I/O.
    async with db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        row = await (
            await connection.execute(
                "SELECT u.id,c.address_ciphertext,c.address_hash FROM users u JOIN identity_contacts c ON "
                "c.owner_id=u.id WHERE lower(u.username)=%s AND c.kind=%s AND c.verified AND "
                "u.enabled AND u.approval_status='approved' FOR UPDATE OF u,c",
                (body.username.lower(), body.kind),
            )
        ).fetchone()
        if row:
            token = secrets.token_urlsafe(32)
            digest = token_digest(token)
            await connection.execute(
                "DELETE FROM identity_recovery_tokens WHERE owner_id=%s AND purpose='reset'", (row["id"],)
            )
            await connection.execute(
                "INSERT INTO identity_recovery_tokens(token_hash,owner_id,kind,purpose,expires_at,address_hash) "
                "VALUES(%s,%s,%s,'reset',now()+interval '10 minutes',%s)",
                (digest, row["id"], body.kind, row["address_hash"]),
            )
            payload = json.dumps(
                {
                    "kind": body.kind,
                    "address": vault(request).decrypt(row["address_ciphertext"]).get_secret_value(),
                    "token": token,
                }
            )
            await connection.execute(
                "INSERT INTO identity_deliveries(id,token_hash,payload_ciphertext) VALUES(%s,%s,%s)",
                (uuid4(), digest, vault(request).encrypt(payload)),
            )
    return {"accepted": True, "message": "如果账号存在且已验证该联系方式，将发送找回凭据"}


@router.post("/auth/identity/recovery/reset")
async def reset(body: Reset, request: Request):
    await throttle(request, "reset-token", token_digest(body.token), 8, 300)
    await throttle(request, "reset-confirm-ip", request.client.host if request.client else "unknown", 20, 300)
    encoded = await asyncio.to_thread(hash_password, body.password.get_secret_value())
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        row = await (
            await connection.execute(
                "SELECT * FROM identity_recovery_tokens WHERE token_hash=%s AND "
                "purpose='reset' AND expires_at>now() FOR UPDATE",
                (token_digest(body.token),),
            )
        ).fetchone()
        if not row:
            fail(401, "TOKEN_INVALID", "找回凭据错误、过期或已使用")
        current = await (
            await connection.execute(
                "SELECT 1 FROM identity_contacts c JOIN users u ON u.id=c.owner_id "
                "WHERE c.owner_id=%s AND c.kind=%s AND c.address_hash=%s AND c.verified "
                "AND u.enabled AND u.approval_status='approved'",
                (row["owner_id"], row["kind"], row["address_hash"]),
            )
        ).fetchone()
        if not current:
            fail(401, "TOKEN_INVALID", "找回联系方式或账号状态已变化，请重新申请")
        if await (
            await connection.execute("SELECT 1 FROM identity_mfa WHERE owner_id=%s AND enabled", (row["owner_id"],))
        ).fetchone():
            await consume_code(connection, request, row["owner_id"], body.mfa_code)
        await connection.execute(
            "UPDATE users SET password_hash=%s,must_change_password=false WHERE id=%s", (encoded, row["owner_id"])
        )
        await connection.execute("DELETE FROM auth_sessions WHERE owner_id=%s", (row["owner_id"],))
        await connection.execute("DELETE FROM identity_challenges WHERE owner_id=%s", (row["owner_id"],))
        await connection.execute("DELETE FROM identity_recovery_tokens WHERE owner_id=%s", (row["owner_id"],))
        await connection.execute(
            "INSERT INTO audit_events(actor_id,method,resource_path,status_code) "
            "VALUES(%s,'POST','/api/v1/auth/identity/recovery/reset',200)",
            (row["owner_id"],),
        )
    return {"ok": True, "message": "密码已更新，请重新登录并完成多因素验证"}
