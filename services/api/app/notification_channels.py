"""Private encrypted channel configuration and fenced delivery attempts."""

import asyncio
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.errors import APIError, fail
from app.notification_transport import deliver, endpoint
from app.permissions import require_account
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["通知通道"])


class ChannelInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["webhook", "feishu", "wecom", "slack"]
    endpoint: SecretStr | None = Field(default=None, max_length=4000)
    secret: SecretStr | None = Field(default=None, max_length=1024)
    clear_secret: bool = False
    allow_private: bool = False
    enabled: bool = True
    revision: int | None = None


@router.get("/notification-channels")
async def listing(request: Request):
    owner = (await identity(request))["id"]
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,name,kind,host_hint,enabled,allow_private,revision,(secret_ciphertext<>'') AS has_secret "
            "FROM notification_channels WHERE owner_id=%s ORDER BY created_at DESC",
            (owner,),
        )
    }


async def save(request, body: ChannelInput, identifier: UUID | None = None):
    user = await identity(request)
    vault, db = request.app.state.models.vault, request.app.state.db
    if body.allow_private and not user["is_admin"]:
        fail(403, "ADMIN_REQUIRED", "仅管理员可明确授权内网通知")
    previous = None
    if identifier:
        rows = await db.fetch(
            "SELECT * FROM notification_channels WHERE id=%s AND owner_id=%s", (identifier, user["id"])
        )
        if not rows:
            fail(404, "NOT_FOUND", "通知通道不存在或无权访问")
        previous = rows[0]
        if previous["kind"] != body.kind:
            fail(422, "CHANNEL_KIND_IMMUTABLE", "请新建另一类通道")
    url = body.endpoint.get_secret_value() if body.endpoint else ""
    if not url and previous:
        url = vault.decrypt(previous["endpoint_ciphertext"]).get_secret_value()
    try:
        host = (await asyncio.to_thread(endpoint, url, body.allow_private))[0]
    except (ValueError, OSError):
        fail(422, "CHANNEL_ADDRESS_REJECTED", "通知地址无效或访问范围未授权")
    if body.kind != "webhook" and not body.allow_private:
        expected = {
            "slack": {"hooks.slack.com", "hooks.slack-gov.com"},
            "feishu": {"open.feishu.cn", "open.larksuite.com"},
            "wecom": {"qyapi.weixin.qq.com"},
        }
        if host not in expected[body.kind] or urlsplit(url).port not in {None, 443}:
            fail(422, "CHANNEL_PROVIDER_MISMATCH", "机器人地址与所选服务商不匹配")
    secret = body.secret.get_secret_value() if body.secret else ""
    if secret and body.clear_secret:
        fail(422, "SECRET_CONFLICT", "不能同时清除和设置签名密钥")
    ciphertext = (
        vault.encrypt(secret)
        if secret
        else ("" if body.clear_secret or not previous else previous["secret_ciphertext"])
    )
    if body.kind == "webhook" and not ciphertext:
        fail(422, "SIGNING_SECRET_REQUIRED", "自定义 Webhook 必须配置签名密钥")
    if identifier:
        rows = await db.fetch(
            "UPDATE notification_channels SET name=%s,endpoint_ciphertext=%s,secret_ciphertext=%s,host_hint=%s,"
            "allow_private=%s,enabled=%s,revision=revision+1 WHERE id=%s AND owner_id=%s "
            "AND revision=%s RETURNING id,revision",
            (
                body.name,
                vault.encrypt(url),
                ciphertext,
                host,
                body.allow_private,
                body.enabled,
                identifier,
                user["id"],
                body.revision,
            ),
        )
        if not rows:
            fail(409, "CHANNEL_CONFLICT", "通知通道已变化，请刷新")
        return rows[0]
    identifier = uuid4()
    await db.execute(
        "INSERT INTO notification_channels"
        "(id,owner_id,name,kind,endpoint_ciphertext,secret_ciphertext,host_hint,allow_private,enabled) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identifier,
            user["id"],
            body.name,
            body.kind,
            vault.encrypt(url),
            ciphertext,
            host,
            body.allow_private,
            body.enabled,
        ),
    )
    return {"id": identifier, "revision": 1}


@router.post("/notification-channels", status_code=201)
async def create(body: ChannelInput, request: Request):
    return await save(request, body)


@router.put("/notification-channels/{identifier}")
async def edit(identifier: UUID, body: ChannelInput, request: Request):
    return await save(request, body, identifier)


@router.delete("/notification-channels/{identifier}")
async def disable(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    rows = await request.app.state.db.fetch(
        "UPDATE notification_channels SET enabled=false,revision=revision+1 WHERE id=%s AND owner_id=%s RETURNING id",
        (identifier, owner),
    )
    if not rows:
        fail(404, "NOT_FOUND", "通知通道不存在或无权访问")
    return {"enabled": False}


@router.get("/notifications/deliveries")
async def deliveries(request: Request):
    owner = (await identity(request))["id"]
    return {
        "items": await request.app.state.db.fetch(
            "SELECT d.id,d.notification_id,d.channel_id,c.name AS channel_name,"
            "d.status,d.attempts,d.error_code,d.created_at "
            "FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
            "LEFT JOIN notification_channels c ON c.id=d.channel_id WHERE n.owner_id=%s "
            "ORDER BY d.created_at DESC LIMIT 100",
            (owner,),
        )
    }


class Retry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_duplicate: bool = False


@router.post("/notifications/deliveries/{identifier}/retry")
async def retry(identifier: UUID, body: Retry, request: Request):
    owner = (await identity(request))["id"]
    if not body.acknowledge_duplicate:
        fail(422, "DUPLICATE_ACK_REQUIRED", "重新投递可能重复，请明确确认")
    rows = await request.app.state.db.fetch(
        "UPDATE notification_deliveries d SET status='PENDING',channel_revision=c.revision,"
        "lease_token=NULL,lease_until=NULL,"
        "error_code=NULL,finished_at=NULL FROM notifications n,notification_channels c "
        "WHERE d.id=%s AND d.notification_id=n.id AND n.owner_id=%s AND c.owner_id=%s "
        "AND c.id=d.channel_id AND c.enabled "
        "AND d.status IN ('FAILED','UNKNOWN') RETURNING d.id",
        (identifier, owner, owner),
    )
    if not rows:
        fail(409, "DELIVERY_UNAVAILABLE", "投递不存在、仍在进行或通道已停用")
    return {"queued": True}


async def dispatch(state):
    await state.db.execute(
        "UPDATE notification_deliveries SET status='UNKNOWN',error_code='DELIVERY_LEASE_EXPIRED',finished_at=now() "
        "WHERE status='SENDING' AND lease_until<=now()",
    )
    token = uuid4()
    rows = await state.db.admitted(
        "WITH next AS (SELECT id FROM notification_deliveries WHERE status='PENDING' ORDER BY created_at "
        "FOR UPDATE SKIP LOCKED LIMIT 1) UPDATE notification_deliveries d SET status='SENDING',lease_token=%s,"
        "lease_until=now()+interval '60 seconds',attempts=attempts+1 FROM next WHERE d.id=next.id RETURNING d.*",
        (token,),
    )
    if not rows:
        return
    row, status, code = rows[0], "FAILED", "CHANNEL_UNAVAILABLE"
    channels = await state.db.fetch("SELECT * FROM notification_channels WHERE id=%s AND enabled", (row["channel_id"],))
    notices = await state.db.fetch("SELECT * FROM notifications WHERE id=%s", (row["notification_id"],))
    if channels and notices and channels[0]["owner_id"] == notices[0]["owner_id"]:
        channel, notice = channels[0], notices[0]
        try:
            user = await require_account(state.db, notice["owner_id"], "notifications.manage")
            if channel["revision"] != row["channel_revision"]:
                code = "CHANNEL_CHANGED_REVIEW_REQUIRED"
            elif channel["allow_private"] and not user["is_admin"]:
                code = "CHANNEL_PRIVATE_PERMISSION_REVOKED"
            else:
                status, code = await asyncio.to_thread(deliver, channel, notice, state.models.vault, str(row["id"]))
        except APIError:
            code = "NOTIFICATION_PERMISSION_REVOKED"
    await state.db.execute(
        "UPDATE notification_deliveries SET status=%s,error_code=%s,finished_at=now(),"
        "lease_token=NULL,lease_until=NULL "
        "WHERE id=%s AND lease_token=%s AND status='SENDING' AND lease_until>now()",
        (status, code, row["id"], token),
    )
