"""Organization roles and explicit sharing of immutable workflow versions."""

import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import fail
from app.external_workflows import InvokeInput, admit
from app.security import identity, token_digest

router = APIRouter(prefix="/api/v1/organizations", tags=["组织管理"])


class OrganizationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    role: Literal["admin", "editor", "viewer"] = "viewer"


class ShareInput(BaseModel):
    version_id: UUID


async def membership(request: Request, organization: UUID, user: UUID) -> dict:
    rows = await request.app.state.db.fetch(
        "SELECT m.role,o.owner_id,o.name FROM organization_members m JOIN organizations o ON o.id=m.organization_id "
        "WHERE m.organization_id=%s AND m.user_id=%s",
        (organization, user),
    )
    if not rows:
        fail(404, "ORGANIZATION_NOT_FOUND", "组织不存在或无权访问")
    return rows[0]


@router.get("")
async def listing(request: Request) -> dict:
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT o.id,o.name,m.role FROM organizations o JOIN organization_members m ON m.organization_id=o.id "
            "WHERE m.user_id=%s ORDER BY o.created_at",
            (user["id"],),
        )
    }


@router.post("", status_code=201)
async def create(body: OrganizationInput, request: Request) -> dict:
    user = await identity(request)
    identifier = uuid4()
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute(
            "INSERT INTO organizations(id,name,owner_id) VALUES(%s,%s,%s)", (identifier, body.name, user["id"])
        )
        await connection.execute("INSERT INTO organization_members VALUES(%s,%s,'owner')", (identifier, user["id"]))
    return {"id": identifier}


@router.get("/{identifier}/members")
async def members(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    await membership(request, identifier, user["id"])
    return {
        "items": await request.app.state.db.fetch(
            "SELECT m.user_id,u.username,m.role FROM organization_members m JOIN users u ON u.id=m.user_id "
            "WHERE m.organization_id=%s ORDER BY u.username",
            (identifier,),
        )
    }


@router.put("/{identifier}/members")
async def save_member(identifier: UUID, body: MemberInput, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        # Serialize role changes so a removed administrator cannot race another edit.
        await connection.execute("SELECT id FROM organizations WHERE id=%s FOR UPDATE", (identifier,))
        actor = await (
            await connection.execute(
                "SELECT role FROM organization_members WHERE organization_id=%s AND user_id=%s",
                (identifier, user["id"]),
            )
        ).fetchone()
        if not actor or actor["role"] not in {"owner", "admin"}:
            fail(403, "ROLE_REQUIRED", "只有所有者和管理员可以管理成员")
        target = await (
            await connection.execute(
                "SELECT u.id,m.role FROM users u LEFT JOIN organization_members m ON m.user_id=u.id AND "
                "m.organization_id=%s WHERE u.username=%s",
                (identifier, body.username),
            )
        ).fetchone()
        if not target:
            fail(404, "MEMBER_NOT_FOUND", "请先创建该本地账号，再添加组织成员")
        if (
            target["role"] == "owner"
            or actor["role"] == "admin"
            and (body.role == "admin" or target["role"] == "admin")
        ):
            fail(403, "ROLE_REQUIRED", "管理员不能修改所有者或其他管理员")
        await connection.execute(
            "INSERT INTO organization_members VALUES(%s,%s,%s) ON CONFLICT(organization_id,user_id) DO "
            "UPDATE SET role=excluded.role",
            (identifier, target["id"], body.role),
        )
    return {"ok": True}


@router.delete("/{identifier}/members/{member}")
async def remove_member(identifier: UUID, member: UUID, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT id FROM organizations WHERE id=%s FOR UPDATE", (identifier,))
        rows = await (
            await connection.execute(
                "SELECT user_id,role FROM organization_members WHERE organization_id=%s", (identifier,)
            )
        ).fetchall()
        roles = {str(row["user_id"]): row["role"] for row in rows}
        actor, target = roles.get(str(user["id"])), roles.get(str(member))
        if not target or target == "owner" or actor not in {"owner", "admin"} or actor == "admin" and target == "admin":
            fail(403, "ROLE_REQUIRED", "无权移除此成员，所有者不能被移除")
        await connection.execute(
            "DELETE FROM organization_members WHERE organization_id=%s AND user_id=%s", (identifier, member)
        )
    return {"ok": True}


@router.get("/{identifier}/workflows")
async def shared(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    await membership(request, identifier, user["id"])
    return {
        "items": await request.app.state.db.fetch(
            "SELECT v.id,v.name,v.version,v.active,s.shared_by FROM organization_workflows s "
            "JOIN workflow_versions v ON v.id=s.version_id WHERE s.organization_id=%s ORDER BY v.name,v.version DESC",
            (identifier,),
        )
    }


@router.post("/{identifier}/workflows", status_code=201)
async def share(identifier: UUID, body: ShareInput, request: Request) -> dict:
    user = await identity(request)
    member = await membership(request, identifier, user["id"])
    if member["role"] == "viewer":
        fail(403, "ROLE_REQUIRED", "查看者不能共享流程")
    rows = await request.app.state.db.fetch(
        "SELECT v.id FROM workflow_versions v JOIN workflows w ON w.id=v.workflow_id "
        "WHERE v.id=%s AND w.owner_id=%s AND v.active AND v.digest<>''",
        (body.version_id, user["id"]),
    )
    if not rows:
        fail(403, "VERSION_UNAVAILABLE", "只能共享自己的已测试上线版本")
    await request.app.state.db.execute(
        "INSERT INTO organization_workflows VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
        (identifier, body.version_id, user["id"]),
    )
    return {"ok": True}


@router.delete("/{identifier}/workflows/{version}")
async def unshare(identifier: UUID, version: UUID, request: Request) -> dict:
    user = await identity(request)
    member = await membership(request, identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "DELETE FROM organization_workflows WHERE organization_id=%s AND version_id=%s "
        "AND (shared_by=%s OR %s) RETURNING version_id",
        (identifier, version, user["id"], member["role"] in {"owner", "admin"}),
    )
    if not rows:
        fail(403, "SHARE_UNAVAILABLE", "无权撤销此共享")
    return {"ok": True}


@router.post("/{identifier}/workflows/{version}/runs", status_code=202)
async def invoke(identifier: UUID, version: UUID, body: InvokeInput, request: Request) -> dict:
    user = await identity(request)
    member = await membership(request, identifier, user["id"])
    if member["role"] == "viewer":
        fail(403, "ROLE_REQUIRED", "查看者不能执行流程")
    rows = await request.app.state.db.fetch(
        "SELECT w.owner_id FROM organization_workflows s JOIN workflow_versions v ON v.id=s.version_id "
        "JOIN workflows w ON w.id=v.workflow_id WHERE s.organization_id=%s AND s.version_id=%s AND v.active",
        (identifier, version),
    )
    if not rows:
        fail(403, "VERSION_UNAVAILABLE", "此版本未共享或已下线")
    await request.app.state.db.execute(
        "INSERT INTO api_credentials(id,owner_id,name,version_id,token_hash,token_hint,expires_at,rpm,concurrency,"
        "internal_only,organization_id,caller_id) VALUES(%s,%s,'组织内部调用',%s,%s,'内部',now()+interval '365 "
        "days',10,1,true,%s,%s) "
        "ON CONFLICT(organization_id,caller_id,version_id) WHERE internal_only DO NOTHING",
        (uuid4(), rows[0]["owner_id"], version, token_digest(secrets.token_urlsafe(32)), identifier, user["id"]),
    )
    key = (
        await request.app.state.db.fetch(
            "SELECT * FROM api_credentials WHERE organization_id=%s AND caller_id=%s AND version_id=%s "
            "AND internal_only",
            (identifier, user["id"], version),
        )
    )[0]
    return await admit(body, request, key, request.headers.get("idempotency-key", ""))


@router.get("/{identifier}/runs")
async def runs(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    member = await membership(request, identifier, user["id"])
    return {
        "items": await request.app.state.db.fetch(
            "SELECT r.id,r.status,r.output,r.created_at FROM workflow_runs r JOIN external_workflow_jobs "
            "j ON j.run_id=r.id "
            "JOIN api_credentials k ON k.id=j.credential_id WHERE k.organization_id=%s AND (k.caller_id=%s OR %s) "
            "ORDER BY r.created_at DESC LIMIT 50",
            (identifier, user["id"], member["role"] in {"owner", "admin"}),
        )
    }


@router.delete("/{identifier}")
async def delete_empty(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    async with request.app.state.db.pool.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT id FROM organizations WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, user["id"])
            )
        ).fetchone()
        if not row:
            fail(403, "ROLE_REQUIRED", "只有所有者可以删除空组织")
        occupied = await (
            await connection.execute(
                "SELECT 1 FROM organization_members WHERE organization_id=%s AND user_id<>%s "
                "UNION ALL SELECT 1 FROM organization_workflows WHERE organization_id=%s "
                "UNION ALL SELECT 1 FROM api_credentials WHERE organization_id=%s LIMIT 1",
                (identifier, user["id"], identifier, identifier),
            )
        ).fetchone()
        if occupied:
            fail(409, "ORGANIZATION_IN_USE", "组织仍有成员、共享资源或调用记录，不能删除")
        await connection.execute("DELETE FROM organizations WHERE id=%s", (identifier,))
    return {"ok": True}
