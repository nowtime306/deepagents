"""Lease-authorized access to the API's single embedded vector index."""

from uuid import UUID

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import DEFAULT_INTERNAL_API_URL, validate_internal_api_url
from app.errors import fail
from app.permissions import require_account

router = APIRouter(prefix="/api/v1/internal/ask-runs", tags=["对话内部检索"])


class SearchInput(BaseModel):
    token: UUID
    knowledge_id: UUID
    query: str = Field(min_length=1, max_length=4000)


@router.post("/{identifier}/knowledge")
async def search(identifier: UUID, body: SearchInput, request: Request) -> dict:
    rows = await request.app.state.db.fetch(
        "SELECT c.owner_id,r.agent_snapshot FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
        "WHERE r.id=%s AND r.lease_token=%s AND r.lease_until>now() AND r.status='RUNNING' AND NOT r.cancel_requested",
        (identifier, body.token),
    )
    if not rows:
        fail(403, "ASK_LEASE_LOST", "对话执行租约已失效")
    run = rows[0]
    await require_account(request.app.state.db, run["owner_id"], "chat.run")
    await require_account(request.app.state.db, run["owner_id"], "knowledge.search")
    snapshot = run["agent_snapshot"] or await request.app.state.capabilities.bindings(run["owner_id"])
    current = await request.app.state.capabilities.bindings(run["owner_id"])
    if snapshot.get("id"):
        agents = await request.app.state.db.fetch(
            "SELECT knowledge_ids FROM agents WHERE id=%s AND owner_id=%s AND enabled",
            (snapshot["id"], run["owner_id"]),
        )
        current = agents[0] if agents else {"knowledge_ids": []}
    if (
        str(body.knowledge_id) not in snapshot.get("knowledge_ids", [])
        or str(body.knowledge_id) not in current["knowledge_ids"]
    ):
        fail(403, "KNOWLEDGE_REVOKED", "知识库授权已撤销")
    return await request.app.state.knowledge.search(
        body.knowledge_id, run["owner_id"], body.query,
        usage_context={"id": identifier, "mode": "ask", "lease_token": body.token},
    )


class AskKnowledge:
    def __init__(self, database, run: dict, origin: str, internal_api_url: str = DEFAULT_INTERNAL_API_URL) -> None:
        self.database, self.run, self.origin = database, run, origin
        self.internal_api_url = validate_internal_api_url(internal_api_url)

    async def owned(self, identifier: UUID, owner: UUID) -> dict:
        if owner != self.run["owner_id"]:
            fail(403, "KNOWLEDGE_UNAVAILABLE", "知识库无权访问")
        rows = await self.database.fetch(
            "SELECT * FROM knowledge_bases WHERE id=%s AND owner_id=%s", (identifier, owner)
        )
        if not rows:
            fail(404, "KNOWLEDGE_UNAVAILABLE", "知识库不存在或无权访问")
        return rows[0]

    async def search(self, knowledge_id: UUID, owner_id: UUID, query: str, *, usage_context=None) -> dict:
        await self.owned(knowledge_id, owner_id)
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                f"{self.internal_api_url}/api/v1/internal/ask-runs/{self.run['id']}/knowledge",
                headers={"Origin": self.origin},
                json={"token": str(self.run["lease_token"]), "knowledge_id": str(knowledge_id), "query": query},
            )
        if response.status_code != 200:
            fail(503, "KNOWLEDGE_SERVICE_UNAVAILABLE", "知识检索服务暂不可用或授权已撤销")
        return response.json()
