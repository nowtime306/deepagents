"""Per-account MCP authorization-code grants with PKCE and explicit resource binding."""

import base64
import hashlib
import ipaddress
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, SecretStr

from app.errors import APIError, fail
from app.mcp_service import validate_target
from app.security import administrator, identity, token_digest

router = APIRouter(prefix="/api/v1/tool-oauth", tags=["工具授权"])


def config_pin(server):
    return token_digest(
        server["url"] + "|" + server.get("transport", "streamable_http") + "|" + str(server["allow_private"])
    )


async def oauth_target(url, private):
    if not isinstance(url, str) or len(url) > 4096:
        fail(422, "OAUTH_ENDPOINT_INVALID", "授权服务端点格式无效")
    try:
        parsed = urlsplit(url)
    except ValueError:
        fail(422, "OAUTH_ENDPOINT_INVALID", "授权服务端点格式无效")
    try:
        loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme != "https" and not (private and parsed.scheme == "http" and loopback):
        fail(422, "OAUTH_HTTPS_REQUIRED", "OAuth必须使用HTTPS；仅明确允许的本机回环测试可用HTTP")
    await validate_target(url, private)


async def bounded_json(client, method, url, private, *, data=None, limit=131072, optional=False):
    await oauth_target(url, private)
    async with client.stream(method, url, data=data, headers={"Accept-Encoding": "identity"}) as response:
        if optional and response.status_code in {404, 405}:
            return None
        if not response.is_success:
            fail(502, "OAUTH_REMOTE_FAILED", "授权服务请求失败，请核对服务配置后重新授权")
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            fail(502, "OAUTH_RESPONSE_ENCODING", "授权服务必须返回未压缩JSON响应")
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=4096):
            if len(body) + len(chunk) > limit:
                fail(502, "OAUTH_RESPONSE_TOO_LARGE", "授权服务响应超过限制")
            body.extend(chunk)
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        fail(502, "OAUTH_JSON_INVALID", "授权服务未返回有效JSON对象")
    if not isinstance(value, dict):
        fail(502, "OAUTH_JSON_INVALID", "授权服务未返回有效JSON对象")
    return value


async def discover(server, client):
    parsed = urlsplit(server["url"])
    origin = f"{parsed.scheme}://{parsed.netloc}"
    private = server["allow_private"]
    await oauth_target(server["url"], private)
    # This unauthenticated probe never carries an existing token or follows redirects.
    async with client.stream("GET", server["url"]) as response:
        challenge = ",".join(response.headers.get_list("www-authenticate")) if response.status_code == 401 else ""
    match = re.search(r'\bresource_metadata\s*=\s*"([^"\\]+)"', challenge, re.IGNORECASE)
    urls = [match[1]] if match else list(dict.fromkeys([
        origin + "/.well-known/oauth-protected-resource" + parsed.path,
        origin + "/.well-known/oauth-protected-resource",
    ]))
    resource = None
    for url in urls:
        resource = await bounded_json(client, "GET", url, private, optional=True)
        if resource is not None:
            break
    if (
        not resource or resource.get("resource") != server["url"]
        or not isinstance(resource.get("authorization_servers"), list)
        or not resource["authorization_servers"]
    ):
        fail(422, "OAUTH_RESOURCE_INVALID", "授权元数据与MCP资源不匹配")
    issuer = resource["authorization_servers"][0]
    await oauth_target(issuer, private)
    issuer_url = urlsplit(issuer)
    issuer_origin = f"{issuer_url.scheme}://{issuer_url.netloc}"
    path = issuer_url.path.rstrip("/")
    candidates = [
        issuer_origin + "/.well-known/oauth-authorization-server" + path,
        issuer_origin + "/.well-known/openid-configuration" + path,
    ]
    if path:
        candidates.append(issuer.rstrip("/") + "/.well-known/openid-configuration")
    authorization = None
    for url in candidates:
        authorization = await bounded_json(client, "GET", url, private, optional=True)
        if authorization is not None:
            break
    if (
        not authorization or authorization.get("issuer") != issuer
        or not isinstance(authorization.get("code_challenge_methods_supported"), list)
        or "S256" not in authorization["code_challenge_methods_supported"]
    ):
        fail(422, "OAUTH_ISSUER_INVALID", "授权服务器issuer或PKCE支持不符合要求")
    for field in ("authorization_endpoint", "token_endpoint", "revocation_endpoint"):
        if field != "revocation_endpoint" and not authorization.get(field):
            fail(422, "OAUTH_METADATA_INVALID", "授权元数据缺少必需端点")
        if authorization.get(field):
            await oauth_target(authorization[field], private)
    return authorization


class ClientInput(BaseModel):
    client_id: str = Field(min_length=1, max_length=400)
    secret: SecretStr | None = None
    clear_secret: bool = False
    scopes: str = Field(default="", max_length=2000, pattern=r"^[\x20-\x7e]*$")


async def owned_server(request, identifier):
    rows = await request.app.state.db.fetch("SELECT * FROM mcp_servers WHERE id=%s AND enabled", (identifier,))
    if not rows or rows[0].get("transport") in {"stdio", "acp"}:
        fail(404, "OAUTH_SERVER_UNAVAILABLE", "HTTP工具连接不存在或已停用")
    return rows[0]


@router.get("/{identifier}/client")
async def client_config(identifier: UUID, request: Request):
    await administrator(request)
    await owned_server(request, identifier)
    rows = await request.app.state.db.fetch(
        "SELECT client_id,scopes,(secret_ciphertext<>'') AS secret_configured "
        "FROM mcp_oauth_clients WHERE server_id=%s",
        (identifier,),
    )
    return rows[0] if rows else {"client_id": "", "scopes": "", "secret_configured": False}


@router.put("/{identifier}/client")
async def configure(identifier: UUID, body: ClientInput, request: Request):
    await administrator(request)
    await owned_server(request, identifier)
    db, vault = request.app.state.db, request.app.state.models.vault
    old = await db.fetch("SELECT secret_ciphertext FROM mcp_oauth_clients WHERE server_id=%s", (identifier,))
    cipher = old[0]["secret_ciphertext"] if old else ""
    if body.clear_secret:
        cipher = ""
    elif body.secret and body.secret.get_secret_value():
        cipher = vault.encrypt(body.secret.get_secret_value())
    async with db.pool.connection() as c:
        await c.execute("UPDATE mcp_servers SET oauth_required=true WHERE id=%s", (identifier,))
        await c.execute(
            "INSERT INTO mcp_oauth_clients(server_id,client_id,secret_ciphertext,scopes) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(server_id) DO UPDATE SET client_id=excluded.client_id,"
            "secret_ciphertext=excluded.secret_ciphertext,"
            "scopes=excluded.scopes,revision=mcp_oauth_clients.revision+1",
            (identifier, body.client_id, cipher, body.scopes),
        )
        await c.execute("DELETE FROM mcp_oauth_grants WHERE server_id=%s", (identifier,))
        await c.execute("DELETE FROM mcp_oauth_flows WHERE server_id=%s", (identifier,))
    return {"configured": True}


@router.get("/{identifier}/status")
async def status(identifier: UUID, request: Request):
    user = await identity(request)
    server = await owned_server(request, identifier)
    rows = await request.app.state.db.fetch(
        "SELECT expires_at,scopes FROM mcp_oauth_grants WHERE owner_id=%s AND server_id=%s AND config_pin=%s",
        (user["id"], identifier, config_pin(server)),
    )
    return {"configured": server["oauth_required"], "grant": rows[0] if rows else None}


@router.post("/{identifier}/authorize")
async def authorize(identifier: UUID, request: Request):
    user = await identity(request)
    server = await owned_server(request, identifier)
    rows = await request.app.state.db.fetch("SELECT * FROM mcp_oauth_clients WHERE server_id=%s", (identifier,))
    if not rows:
        fail(422, "OAUTH_CLIENT_REQUIRED", "请管理员先配置预注册OAuth客户端")
    config = rows[0]
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        metadata = await discover(server, client)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    await request.app.state.db.execute(
        "INSERT INTO mcp_oauth_flows(state_hash,owner_id,server_id,config_pin,client_revision,"
        "verifier_ciphertext,metadata,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            token_digest(state),
            user["id"],
            identifier,
            config_pin(server),
            config["revision"],
            request.app.state.models.vault.encrypt(verifier),
            Jsonb(metadata),
            datetime.now(UTC) + timedelta(minutes=5),
        ),
    )
    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "state": state,
        "redirect_uri": request.app.state.settings.web_origin + "/api/v1/tool-oauth/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": server["url"],
        "scope": config["scopes"],
    }
    return {"authorization_url": metadata["authorization_endpoint"] + "?" + urlencode(params)}


@router.get("/callback")
async def callback(request: Request, state: str = "", code: str = ""):
    user = await identity(request)
    db, vault = request.app.state.db, request.app.state.models.vault
    flows = await db.fetch(
        "UPDATE mcp_oauth_flows SET consumed_at=now() WHERE state_hash=%s AND owner_id=%s "
        "AND consumed_at IS NULL AND expires_at>now() RETURNING *",
        (token_digest(state), user["id"]),
    )
    if not flows or not code or len(code) > 4096:
        fail(403, "OAUTH_STATE_INVALID", "授权已过期或已使用，请重新授权")
    flow = flows[0]
    server = await owned_server(request, flow["server_id"])
    config = (await db.fetch("SELECT * FROM mcp_oauth_clients WHERE server_id=%s", (server["id"],)))[0]
    if flow["config_pin"] != config_pin(server) or config["revision"] != flow["client_revision"]:
        fail(409, "OAUTH_CONFIG_CHANGED", "连接配置已变化，请重新授权")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config["client_id"],
        "redirect_uri": request.app.state.settings.web_origin + "/api/v1/tool-oauth/callback",
        "code_verifier": vault.decrypt(flow["verifier_ciphertext"]).get_secret_value(),
        "resource": server["url"],
    }
    if config["secret_ciphertext"]:
        data["client_secret"] = vault.decrypt(config["secret_ciphertext"]).get_secret_value()
    async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
        token = await bounded_json(
            client, "POST", flow["metadata"]["token_endpoint"], server["allow_private"], data=data, limit=32768
        )
    async with db.pool.connection() as c:
        await c.execute("SELECT pg_advisory_xact_lock(710041,hashtext(%s))", (str(user["id"]) + str(server["id"]),))
        current_server = await (await c.execute(
            "SELECT * FROM mcp_servers WHERE id=%s AND enabled FOR SHARE", (server["id"],)
        )).fetchone()
        current_config = await (await c.execute(
            "SELECT * FROM mcp_oauth_clients WHERE server_id=%s FOR SHARE", (server["id"],)
        )).fetchone()
        valid = await (await c.execute(
            "SELECT state_hash FROM mcp_oauth_flows WHERE state_hash=%s AND owner_id=%s "
            "AND consumed_at IS NOT NULL AND expires_at>now() FOR UPDATE",
            (token_digest(state), user["id"]),
        )).fetchone()
        if not valid:
            fail(403, "OAUTH_STATE_REVOKED", "此授权已撤销或过期，请重新授权")
        if (
            not current_server or not current_config
            or config_pin(current_server) != flow["config_pin"]
            or current_config["revision"] != flow["client_revision"]
        ):
            fail(409, "OAUTH_CONFIG_CHANGED", "连接配置已变化，请重新授权")
        await store_grant(c, vault, user["id"], current_server, current_config, flow["metadata"], token)
        await c.execute("DELETE FROM mcp_oauth_flows WHERE state_hash=%s", (token_digest(state),))
    return RedirectResponse(request.app.state.settings.web_origin, status_code=303)


async def store_grant(db, vault, owner, server, config, metadata, token):
    if (
        not isinstance(token.get("token_type"), str) or token["token_type"].lower() != "bearer"
        or not isinstance(token.get("access_token"), str) or not 1 <= len(token["access_token"]) <= 16384
        or ("refresh_token" in token and (
            not isinstance(token["refresh_token"], str) or len(token["refresh_token"]) > 16384
        ))
    ):
        fail(502, "OAUTH_TOKEN_INVALID", "授权服务器未返回有效Bearer令牌")
    scopes = token.get("scope", config["scopes"])
    if not isinstance(scopes, str) or len(scopes) > 2000 or not set(scopes.split()) <= set(config["scopes"].split()):
        fail(502, "OAUTH_SCOPE_INVALID", "授权范围超出请求范围")
    expires = token.get("expires_in", 300)
    if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not 1 <= expires <= 31536000:
        fail(502, "OAUTH_TOKEN_INVALID", "授权有效期无效")
    await db.execute(
        "INSERT INTO mcp_oauth_grants(owner_id,server_id,config_pin,client_revision,issuer,token_endpoint,"
        "revoke_endpoint,access_ciphertext,refresh_ciphertext,scopes,expires_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(owner_id,server_id) DO UPDATE SET config_pin=excluded.config_pin,"
        "client_revision=excluded.client_revision,"
        "issuer=excluded.issuer,token_endpoint=excluded.token_endpoint,revoke_endpoint=excluded.revoke_endpoint,"
        "access_ciphertext=excluded.access_ciphertext,refresh_ciphertext=excluded.refresh_ciphertext,"
        "scopes=excluded.scopes,expires_at=excluded.expires_at,revision=mcp_oauth_grants.revision+1",
        (
            owner,
            server["id"],
            config_pin(server),
            config["revision"],
            metadata["issuer"],
            metadata["token_endpoint"],
            metadata.get("revocation_endpoint"),
            vault.encrypt(token["access_token"]),
            vault.encrypt(token["refresh_token"]) if token.get("refresh_token") else "",
            scopes,
            datetime.now(UTC) + timedelta(seconds=expires),
        ),
    )


async def access_token(db, vault, owner, server):
    if not server.get("oauth_required"):
        return None
    await oauth_target(server["url"], server["allow_private"])
    async with db.pool.connection() as c:
        await c.execute("SELECT pg_advisory_xact_lock(710041,hashtext(%s))", (str(owner) + str(server["id"]),))
        grant = await (
            await c.execute(
                "SELECT g.*,a.client_id,a.secret_ciphertext,a.scopes AS requested_scopes "
                "FROM mcp_oauth_grants g JOIN mcp_oauth_clients a "
                "ON a.server_id=g.server_id AND a.revision=g.client_revision WHERE g.owner_id=%s AND g.server_id=%s "
                "AND g.config_pin=%s FOR UPDATE OF g",
                (owner, server["id"], config_pin(server)),
            )
        ).fetchone()
        if not grant:
            fail(403, "MCP_OAUTH_REQUIRED", "授权未完成或已过期，请重新授权")
        if grant["expires_at"] <= datetime.now(UTC):
            if not grant["refresh_ciphertext"]:
                fail(403, "MCP_OAUTH_REQUIRED", "授权已过期，请重新授权")
            data = {
                "grant_type": "refresh_token",
                "client_id": grant["client_id"],
                "resource": server["url"],
                "refresh_token": vault.decrypt(grant["refresh_ciphertext"]).get_secret_value(),
            }
            if grant["secret_ciphertext"]:
                data["client_secret"] = vault.decrypt(grant["secret_ciphertext"]).get_secret_value()
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
                token = await bounded_json(
                    client, "POST", grant["token_endpoint"], server["allow_private"], data=data, limit=32768
                )
            token.setdefault("refresh_token", data["refresh_token"])
            config = {"revision": grant["client_revision"], "scopes": grant["requested_scopes"]}
            await store_grant(c, vault, owner, server, config, grant, token)
            return token["access_token"]
        return vault.decrypt(grant["access_ciphertext"]).get_secret_value()


@router.delete("/{identifier}/grant")
async def revoke(identifier: UUID, request: Request):
    user = await identity(request)
    db = request.app.state.db
    async with db.pool.connection() as c:
        await c.execute("SELECT pg_advisory_xact_lock(710041,hashtext(%s))", (str(user["id"]) + str(identifier),))
        grants = await (await c.execute(
            "DELETE FROM mcp_oauth_grants WHERE owner_id=%s AND server_id=%s RETURNING *", (user["id"], identifier)
        )).fetchall()
        await c.execute(
            "DELETE FROM mcp_oauth_flows WHERE owner_id=%s AND server_id=%s", (user["id"], identifier)
        )
    remote = "not_advertised"
    if grants and grants[0]["revoke_endpoint"]:
        # Disabled connections must still permit removal of local authorization.
        server = (await db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (identifier,)))[0]
        config = (await db.fetch("SELECT * FROM mcp_oauth_clients WHERE server_id=%s", (identifier,)))[0]
        vault = request.app.state.models.vault
        data = {
            "token": vault.decrypt(
                grants[0]["refresh_ciphertext"] or grants[0]["access_ciphertext"]
            ).get_secret_value(),
            "client_id": config["client_id"],
        }
        if config["secret_ciphertext"]:
            data["client_secret"] = vault.decrypt(config["secret_ciphertext"]).get_secret_value()
        try:
            await oauth_target(grants[0]["revoke_endpoint"], server["allow_private"])
            async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
                async with client.stream("POST", grants[0]["revoke_endpoint"], data=data) as response:
                    remote = "confirmed" if response.is_success else "unconfirmed"
        except (httpx.HTTPError, APIError):
            remote = "unconfirmed"
    return {"revoked": True, "remote_status": remote, "note": "本机授权已撤销；未确认的远端授权请在服务商核对"}
