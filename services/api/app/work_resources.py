"""Reuse the API-owned vector index from a fenced independent Work process."""

import asyncio
import hashlib
import secrets
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Request
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.config import DEFAULT_INTERNAL_API_URL, validate_internal_api_url
from app.errors import fail
from app.external_workflows import InvokeInput, admit
from app.security import token_digest
from app.storage import Database
from app.team_context import active_member
from app.team_resource_context import member_context

router = APIRouter(prefix="/api/v1/internal/work-runs", tags=["任务内部检索"])


TEAM_RESOURCE_CONTEXT_VERSION = 'work-member-resource-v1'


class SearchInput(BaseModel):
    token: UUID
    knowledge_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    team_call_id: UUID | None = None


@router.post("/{identifier}/knowledge")
async def search(identifier: UUID, body: SearchInput, request: Request) -> dict:
    rows = await request.app.state.db.fetch(
        "SELECT owner_id,agent_snapshot FROM work_queue_runs WHERE id=%s AND status='RUNNING' "
        "AND lease_token=%s AND lease_until>now() AND NOT cancel_requested",
        (identifier, body.token),
    )
    if not rows:
        fail(403, "WORK_LEASE_INVALID", "任务租约已失效")
    run = rows[0]
    selection = run["agent_snapshot"] or await request.app.state.capabilities.bindings(run["owner_id"])
    if selection.get("id"):
        agents = await request.app.state.db.fetch(
            "SELECT knowledge_ids FROM agents WHERE id=%s AND owner_id=%s AND enabled",
            (selection["id"], run["owner_id"]),
        )
        current = agents[0] if agents else {"knowledge_ids": []}
    else:
        current = await request.app.state.capabilities.bindings(run["owner_id"])
    identifier_string = str(body.knowledge_id)
    if identifier_string not in selection.get("knowledge_ids", []) or identifier_string not in current["knowledge_ids"]:
        fail(403, "KNOWLEDGE_REVOKED", "此任务无权检索所选知识库")
    async with member_context(request.app.state.db, run["owner_id"], identifier, body.token,
                              selection, body.team_call_id, body.knowledge_id) as member_authority:
        return await request.app.state.knowledge.search(
            body.knowledge_id, run["owner_id"], body.query,
            usage_context={"id": identifier, "mode": "work", "lease_token": body.token, "guard": member_authority},
        )


class WorkKnowledge:
    """Carry a short-lived run lease rather than a browser session or management key."""

    def __init__(self, database: Database, run: dict, origin: str,
                 internal_api_url: str = DEFAULT_INTERNAL_API_URL) -> None:
        self.database, self.run, self.origin = database, run, origin
        self.internal_api_url = validate_internal_api_url(internal_api_url)

    async def owned(self, identifier: UUID, owner_id: UUID) -> dict:
        rows = await self.database.fetch(
            "SELECT * FROM knowledge_bases WHERE id=%s AND owner_id=%s", (identifier, owner_id)
        )
        if not rows or owner_id != self.run["owner_id"]:
            fail(403, "KNOWLEDGE_UNAVAILABLE", "知识库无权访问")
        return rows[0]

    async def search(self, knowledge_id: UUID, owner_id: UUID, query: str, *, usage_context=None) -> dict:
        await self.owned(knowledge_id, owner_id)
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                f"{self.internal_api_url}/api/v1/internal/work-runs/{self.run['id']}/knowledge",
                headers={"Origin": self.origin},
                json={"token": str(self.run["lease_token"]), "knowledge_id": str(knowledge_id), "query": query,
                      "team_call_id": str(active_member.get()["id"]) if active_member.get() else None},
            )
            if response.status_code != 200:
                raise RuntimeError("Work knowledge retrieval unavailable or permission revoked")
            return response.json()


class WorkflowInput(BaseModel):
    token: UUID
    version_id: UUID
    text: str = Field(min_length=1, max_length=16000)


class LeaseInput(BaseModel):
    token: UUID


async def live_work(request: Request, identifier: UUID, token: UUID) -> dict:
    rows = await request.app.state.db.fetch(
        "SELECT * FROM work_queue_runs WHERE id=%s AND lease_token=%s AND status='RUNNING' "
        "AND lease_until>now() AND NOT cancel_requested",
        (identifier, token),
    )
    if not rows:
        fail(403, "WORK_LEASE_INVALID", "任务执行租约已失效")
    return rows[0]


@router.post("/{identifier}/workflows", status_code=202)
async def invoke_workflow(identifier: UUID, body: WorkflowInput, request: Request) -> dict:
    run = await live_work(request, identifier, body.token)
    selection = run["agent_snapshot"]
    if not selection.get("id") or str(body.version_id) not in selection.get("workflow_version_ids", []):
        fail(403, "WORKFLOW_UNAVAILABLE", "请先为任务智能体绑定发布版本")
    current = await request.app.state.db.fetch(
        "SELECT workflow_version_ids FROM agents WHERE id=%s AND owner_id=%s AND enabled",
        (selection["id"], run["owner_id"]),
    )
    if not current or str(body.version_id) not in current[0]["workflow_version_ids"]:
        fail(403, "WORKFLOW_REVOKED", "工作流调用授权已撤销")
    await request.app.state.db.execute(
        "INSERT INTO api_credentials(id,owner_id,name,version_id,token_hash,token_hint,expires_at,rpm,concurrency,"
        "internal_only,parent_work_run,parent_lease_token) "
        "VALUES(%s,%s,'任务内部调用',%s,%s,'内部',now()+interval '1 hour',10,1,true,%s,%s) "
        "ON CONFLICT(parent_work_run,parent_lease_token,version_id) WHERE internal_only AND "
        "parent_work_run IS NOT NULL DO NOTHING",
        (uuid4(), run["owner_id"], body.version_id, token_digest(secrets.token_urlsafe(32)), identifier, body.token),
    )
    key = (
        await request.app.state.db.fetch(
            "SELECT * FROM api_credentials WHERE parent_work_run=%s AND parent_lease_token=%s AND version_id=%s",
            (identifier, body.token, body.version_id),
        )
    )[0]
    fingerprint = hashlib.sha256(body.text.encode()).hexdigest()
    return await admit(InvokeInput(text=body.text), request, key, str(body.version_id) + ":" + fingerprint)


@router.post("/{identifier}/workflows/{run_id}/status")
async def workflow_status(identifier: UUID, run_id: UUID, body: LeaseInput, request: Request) -> dict:
    await live_work(request, identifier, body.token)
    rows = await request.app.state.db.fetch(
        "SELECT r.id,r.status,r.output FROM workflow_runs r JOIN external_workflow_jobs j ON j.run_id=r.id "
        "JOIN api_credentials k ON k.id=j.credential_id "
        "WHERE r.id=%s AND k.parent_work_run=%s AND k.parent_lease_token=%s "
        "AND EXISTS(SELECT 1 FROM work_queue_runs wr JOIN agents a ON a.id=(wr.agent_snapshot->>'id')::uuid "
        "WHERE wr.id=k.parent_work_run AND a.owner_id=wr.owner_id AND a.enabled "
        "AND a.workflow_version_ids ? k.version_id::text)",
        (run_id, identifier, body.token),
    )
    if not rows:
        fail(404, "RUN_NOT_FOUND", "任务子流程不存在")
    return rows[0]


class WorkWorkflows:
    def __init__(self, run: dict, origin: str, internal_api_url: str = DEFAULT_INTERNAL_API_URL) -> None:
        self.run, self.origin = run, origin
        self.internal_api_url = validate_internal_api_url(internal_api_url)

    def tool(self):
        async def invoke_workflow(version_id: str, text: str) -> str:
            """调用智能体明确绑定的发布工作流。version_id 必须从授权清单选取；text 为输入文本。"""
            if version_id not in self.run["agent_snapshot"].get("workflow_version_ids", []):
                raise ValueError("Workflow version is not granted")
            base = f"{self.internal_api_url}/api/v1/internal/work-runs/{self.run['id']}/workflows"
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                                         headers={"Origin": self.origin}) as client:
                result = await client.post(
                    base, json={"token": str(self.run["lease_token"]), "version_id": version_id, "text": text}
                )
                if result.status_code != 202:
                    raise RuntimeError("Workflow admission failed or permission revoked")
                identifier = result.json()["id"]
                async with asyncio.timeout(110):
                    while True:
                        result = await client.post(
                            base + f"/{identifier}/status", json={"token": str(self.run["lease_token"])}
                        )
                        if result.status_code != 200:
                            raise RuntimeError("Workflow authorization lost")
                        state = result.json()
                        if state["status"] == "SUCCEEDED":
                            return state["output"]
                        if state["status"] not in {"PENDING", "RUNNING"}:
                            raise RuntimeError("Workflow failed; inspect its node records")
                        await asyncio.sleep(0.5)

        tool = StructuredTool.from_function(coroutine=invoke_workflow)
        tool.description += " 授权 version_id 清单：" + ", ".join(self.run["agent_snapshot"]["workflow_version_ids"])
        return tool
