"""Restricted standard OIDC code flow with browser binding and no implicit email merge."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.account_api import admin_lock, throttle
from app.errors import fail
from app.identity_api import Proof, consume_code, issue_session, login_challenge, password_proof, vault
from app.maintenance import request_slot
from app.permissions import account
from app.security import COOKIE, administrator, hash_password, identity, token_digest

router = APIRouter(prefix="/api/v1", tags=["企业身份"])


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    issuer: str = Field(min_length=8, max_length=1500)
    client_id: str = Field(min_length=1, max_length=300)
    secret: SecretStr | None = None
    clear_secret: bool = False
    enabled: bool = False
    allow_registration: bool = False
    allowed_domains: list[str] = Field(default_factory=list, max_length=30)


class Begin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bind: bool = False
    password: SecretStr | None = None
    code: str = Field(default="", max_length=128)


def endpoint(value, issuer=None):
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        fail(422, "OIDC_URL_INVALID", "身份服务地址不正确")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        fail(422, "OIDC_HTTPS_REQUIRED", "身份服务须使用HTTPS；仅本机验收允许HTTP")
    if issuer and (parsed.scheme, parsed.netloc) != (urlsplit(issuer).scheme, urlsplit(issuer).netloc):
        fail(422, "OIDC_ORIGIN_INVALID", "发现的端点必须与已配置issuer同源")
    return value


async def discover(provider):
    issuer = endpoint(provider["issuer"])
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        response = await client.get(issuer.rstrip("/") + "/.well-known/openid-configuration")
        response.raise_for_status()
        data = response.json()
    if data.get("issuer") != issuer or "S256" not in data.get("code_challenge_methods_supported", []):
        fail(422, "OIDC_DISCOVERY_INVALID", "issuer或PKCE S256能力与配置不符")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint(data[key], issuer)
    return data


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * ((-len(value)) % 4))


def verify_token(token, keys, provider, nonce, now=None):
    try:
        head, body, signature = token.split(".")
        header = json.loads(decode(head))
        claims = json.loads(decode(body))
        if header.get("alg") != "RS256":
            raise ValueError("algorithm")
        key = next(
            k
            for k in keys["keys"]
            if k.get("kid") == header.get("kid")
            and k.get("kty") == "RSA"
            and k.get("use", "sig") == "sig"
            and k.get("alg", "RS256") == "RS256"
        )
        public = rsa.RSAPublicNumbers(int.from_bytes(decode(key["e"])), int.from_bytes(decode(key["n"]))).public_key()
        public.verify(decode(signature), (head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        current = time.time() if now is None else now
        audiences = claims.get("aud", [])
        audiences = [audiences] if isinstance(audiences, str) else audiences
        if (
            claims.get("iss") != provider["issuer"]
            or provider["client_id"] not in audiences
            or (len(audiences) > 1 and claims.get("azp") != provider["client_id"])
            or claims.get("exp", 0) <= current
            or not isinstance(claims.get("iat"), (int, float))
            or claims["iat"] > current + 60
            or claims.get("nbf", 0) > current + 60
            or claims.get("nonce") != nonce
            or not isinstance(claims.get("sub"), str)
            or not 0 < len(claims["sub"]) <= 500
        ):
            raise ValueError("claims")
        return claims
    except Exception:
        fail(401, "OIDC_TOKEN_INVALID", "身份凭据签名、来源或有效期验证失败")


@router.get("/auth/identity/providers")
async def providers(request: Request):
    return {
        "items": await request.app.state.db.fetch("SELECT id,name FROM identity_providers WHERE enabled ORDER BY name")
    }


@router.get("/admin/identity/providers")
async def admin_providers(request: Request):
    await administrator(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT "
            "id,name,issuer,client_id,enabled,allow_registration,allowed_domains,revision,secret_ci"
            "phertext<>'' AS has_secret FROM identity_providers ORDER BY name"
        )
    }


@router.put("/admin/identity/providers/{identifier}")
async def save_provider(identifier: UUID, body: Provider, request: Request):
    actor = await administrator(request)
    endpoint(body.issuer)
    if urlsplit(body.issuer).query:
        fail(422, "OIDC_URL_INVALID", "issuer不可包含查询参数")
    async with request.app.state.db.pool.connection() as connection:
        await admin_lock(connection, actor["id"])
        old = await (await connection.execute("SELECT * FROM identity_providers WHERE id=%s", (identifier,))).fetchall()
        if old and old[0]["issuer"] != body.issuer:
            fail(409, "OIDC_ISSUER_IMMUTABLE", "身份来源创建后不可更改issuer；请新建来源并重新绑定")
        secret = old[0]["secret_ciphertext"] if old else ""
        if body.clear_secret:
            secret = ""
        if body.secret and body.secret.get_secret_value():
            secret = vault(request).encrypt(body.secret.get_secret_value())
        await connection.execute(
            "INSERT INTO "
            "identity_providers(id,name,issuer,client_id,secret_ciphertext,enabled,allow_registrati"
            "on,allowed_domains) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name,issuer=excluded.issuer,client_id=excluded.client_id,secret_cipherte"
            "xt=excluded.secret_ciphertext,enabled=excluded.enabled,allow_registration=excluded.all"
            "ow_registration,allowed_domains=excluded.allowed_domains,revision=identity_providers.r"
            "evision+1",
            (
                identifier,
                body.name,
                body.issuer,
                body.client_id,
                secret,
                body.enabled,
                body.allow_registration,
                [d.lower() for d in body.allowed_domains],
            ),
        )
        if old:
            await connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash IN "
                "(SELECT token_hash FROM identity_provider_sessions WHERE provider_id=%s)",
                (identifier,),
            )
        return {"ok": True}


@router.post("/auth/identity/{identifier}/begin")
async def begin(identifier: UUID, body: Begin, request: Request):
    await throttle(request, "oidc-begin", request.client.host if request.client else "unknown", 20, 300)
    rows = await request.app.state.db.fetch("SELECT * FROM identity_providers WHERE id=%s AND enabled", (identifier,))
    if not rows:
        fail(404, "OIDC_UNAVAILABLE", "身份来源未启用")
    provider = rows[0]
    owner = None
    session_hash = password_version = None
    if body.bind:
        user = await identity(request)
        if body.password is None:
            fail(422, "PASSWORD_REQUIRED", "绑定前请验证当前密码或近期企业身份")
        password_version = await password_proof(request, user, body.password)
        session_hash = token_digest(request.cookies.get(COOKIE, ""))
        async with request.app.state.db.pool.connection() as connection:
            await connection.execute("SELECT pg_advisory_xact_lock(710018)")
            current = await (
                await connection.execute(
                    "SELECT u.password_hash FROM auth_sessions s JOIN users u ON u.id=s.owner_id "
                    "WHERE s.token_hash=%s AND s.owner_id=%s AND s.expires_at>now() FOR UPDATE OF s,u",
                    (session_hash, user["id"]),
                )
            ).fetchone()
            if not current or token_digest(current["password_hash"]) != password_version:
                fail(401, "OIDC_BINDING_EXPIRED", "绑定授权已失效，请重新验证身份")
            if await (
                await connection.execute("SELECT 1 FROM identity_mfa WHERE owner_id=%s AND enabled", (user["id"],))
            ).fetchone():
                await consume_code(connection, request, user["id"], body.code)
        owner = user["id"]
    config = await discover(provider)
    state, nonce, verifier, browser = (secrets.token_urlsafe(32) for _ in range(4))
    await request.app.state.db.execute("DELETE FROM identity_oidc_states WHERE expires_at<=now()")
    await request.app.state.db.execute(
        "INSERT INTO identity_oidc_states(token_hash,provider_id,provider_revision,nonce,verifier_ciphertext,"
        "owner_id,browser_hash,expires_at,binding_session_hash,binding_password_version) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,now()+interval '5 minutes',%s,%s)",
        (
            token_digest(state),
            identifier,
            provider["revision"],
            nonce,
            vault(request).encrypt(verifier),
            owner,
            token_digest(browser),
            session_hash,
            password_version,
        ),
    )
    redirect = request.app.state.settings.web_origin + "/api/v1/auth/identity/callback"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    from fastapi.responses import JSONResponse

    response = JSONResponse(
        {
            "url": config["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "client_id": provider["client_id"],
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "scope": "openid email profile",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        }
    )
    response.set_cookie(
        "identity_browser",
        browser,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        max_age=300,
    )
    return response


@router.get("/auth/identity/callback")
async def callback(request: Request, state: str = "", code: str = ""):
    fence = getattr(request.app.state, "service_fence", None)
    if fence:
        await fence.check()
    async with request_slot(request.app.state.settings.database_url):
        if fence:
            await fence.check()
        return await callback_admitted(request, state, code)


async def callback_admitted(request: Request, state: str, code: str):
    db = request.app.state.db
    async with db.pool.connection() as connection:
        row = await (
            await connection.execute(
                "DELETE FROM identity_oidc_states WHERE token_hash=%s AND browser_hash=%s AND "
                "expires_at>now() RETURNING *",
                (token_digest(state), token_digest(request.cookies.get("identity_browser", ""))),
            )
        ).fetchone()
    if not row:
        fail(401, "OIDC_STATE_INVALID", "登录请求已过期或已使用")
    providers = await db.fetch(
        "SELECT * FROM identity_providers WHERE id=%s AND enabled AND revision=%s",
        (row["provider_id"], row["provider_revision"]),
    )
    if not providers:
        fail(401, "OIDC_UNAVAILABLE", "身份来源配置已变化")
    provider = providers[0]
    config = await discover(provider)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        result = await client.post(
            config["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": provider["client_id"],
                "client_secret": vault(request).decrypt(provider["secret_ciphertext"]).get_secret_value()
                if provider["secret_ciphertext"]
                else "",
                "code_verifier": vault(request).decrypt(row["verifier_ciphertext"]).get_secret_value(),
                "redirect_uri": request.app.state.settings.web_origin + "/api/v1/auth/identity/callback",
            },
        )
        result.raise_for_status()
        jwks = await client.get(config["jwks_uri"])
        jwks.raise_for_status()
    claims = verify_token(result.json()["id_token"], jwks.json(), provider, row["nonce"])
    if fence := getattr(request.app.state, "service_fence", None):
        await fence.check()
    async with db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        provider_now = await (
            await connection.execute(
                "SELECT id FROM identity_providers WHERE id=%s AND enabled AND revision=%s FOR UPDATE",
                (provider["id"], provider["revision"]),
            )
        ).fetchone()
        if not provider_now:
            fail(401, "OIDC_UNAVAILABLE", "身份来源已停用")
        binding = await (
            await connection.execute(
                "SELECT owner_id FROM identity_bindings WHERE provider_id=%s AND subject=%s",
                (provider["id"], claims["sub"]),
            )
        ).fetchone()
        if row["owner_id"]:
            authorized = await (
                await connection.execute(
                    "SELECT u.password_hash FROM auth_sessions s JOIN users u ON u.id=s.owner_id "
                    "WHERE s.token_hash=%s AND s.owner_id=%s AND s.expires_at>now() FOR UPDATE OF s,u",
                    (row["binding_session_hash"], row["owner_id"]),
                )
            ).fetchone()
            if not authorized or token_digest(authorized["password_hash"]) != row["binding_password_version"]:
                fail(401, "OIDC_BINDING_EXPIRED", "绑定授权已撤销，请重新登录并验证身份")
            user = await account(db, row["owner_id"])
            previous = await (
                await connection.execute(
                    "SELECT subject FROM identity_bindings WHERE provider_id=%s AND owner_id=%s",
                    (provider["id"], user["id"]),
                )
            ).fetchone()
            if previous and previous["subject"] != claims["sub"]:
                fail(409, "OIDC_BINDING_CONFLICT", "此来源已绑定另一企业身份，请先验证并解绑")
            if binding and binding["owner_id"] != user["id"]:
                fail(409, "OIDC_BINDING_CONFLICT", "此身份已绑定其他账号")
            await connection.execute(
                "INSERT INTO identity_bindings VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (provider["id"], claims["sub"], user["id"]),
            )
            await connection.execute(
                "INSERT INTO audit_events(actor_id,method,resource_path,status_code) VALUES(%s,'POST',%s,201)",
                (user["id"], f"/api/v1/auth/security/bindings/{provider['id']}"),
            )
            return RedirectResponse(request.app.state.settings.web_origin, status_code=303)
        if binding:
            user = await account(db, binding["owner_id"])
        else:
            domain = claims.get("email", "").rsplit("@", 1)[-1].lower()
            if not provider["allow_registration"] or (
                provider["allowed_domains"]
                and (claims.get("email_verified") is not True or domain not in provider["allowed_domains"])
            ):
                fail(403, "OIDC_BINDING_REQUIRED", "请先由本地账号显式绑定该企业身份")
            owner = uuid4()
            username = "oidc-" + owner.hex[:24]
            await connection.execute(
                "INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)",
                (owner, username, hash_password(secrets.token_urlsafe(64))),
            )
            await connection.execute(
                "INSERT INTO identity_bindings VALUES(%s,%s,%s)", (provider["id"], claims["sub"], owner)
            )
            user = {"id": owner, "username": username, "is_admin": False}
        await connection.execute(
            "INSERT INTO audit_events(actor_id,method,resource_path,status_code) VALUES(%s,'POST',%s,200)",
            (user["id"], f"/api/v1/auth/identity/{provider['id']}/signin"),
        )
        pending = await login_challenge(connection, request, user, provider)
        if pending:
            response = RedirectResponse(request.app.state.settings.web_origin, status_code=303)
            response.set_cookie(
                "identity_challenge",
                json.loads(pending.body)["challenge"],
                httponly=True,
                secure=request.app.state.settings.cookie_secure,
                samesite="lax",
                max_age=300,
            )
            return response
        response = await issue_session(connection, request, user, provider["id"])
        response.status_code = 303
        response.headers["location"] = request.app.state.settings.web_origin
        return response


@router.get("/auth/security/bindings")
async def bindings(request: Request):
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT p.id,p.name,p.enabled FROM identity_bindings b JOIN identity_providers p ON p.id=b.provider_id "
            "WHERE b.owner_id=%s ORDER BY p.name",
            (user["id"],),
        )
    }


@router.post("/auth/security/bindings/{identifier}/remove")
async def unbind(identifier: UUID, body: Proof, request: Request):
    user = await identity(request)
    await password_proof(request, user, body.password)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710018)")
        if await (
            await connection.execute("SELECT 1 FROM identity_mfa WHERE owner_id=%s AND enabled", (user["id"],))
        ).fetchone():
            await consume_code(connection, request, user["id"], body.code)
        await connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash IN "
            "(SELECT token_hash FROM identity_provider_sessions WHERE provider_id=%s) AND owner_id=%s",
            (identifier, user["id"]),
        )
        await connection.execute(
            "DELETE FROM identity_bindings WHERE provider_id=%s AND owner_id=%s", (identifier, user["id"])
        )
    return {"ok": True}
