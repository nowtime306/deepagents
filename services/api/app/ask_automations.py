"""Interactive Ask tools create paused, owned schedules through the API's authority."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

import httpx
from fastapi import APIRouter, Request
from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, ConfigDict, Field

from app.automation_api import create_schedule
from app.automation_types import ScheduleInput
from app.config import DEFAULT_INTERNAL_API_URL, validate_internal_api_url
from app.errors import fail
from app.permissions import require_account

router = APIRouter(prefix="/api/v1/internal/ask-runs", tags=["对话自动任务"])


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: UUID


class Draft(Authorization):
    call_id: str = Field(min_length=1, max_length=128)
    schedule: ScheduleInput


async def authorize(request: Request, identifier: UUID, token: UUID) -> UUID:
    rows = await request.app.state.db.fetch(
        "SELECT c.owner_id FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
        "WHERE r.id=%s AND r.lease_token=%s AND r.lease_until>now() AND r.status='RUNNING' "
        "AND NOT r.cancel_requested AND r.origin='interactive'", (identifier, token),
    )
    if not rows:
        fail(403, "ASK_LEASE_LOST", "只允许当前交互对话创建任务，自动运行不得递归创建")
    owner = rows[0]["owner_id"]
    for permission in ("chat.run", "automation.read", "automation.manage", "automation.execute"):
        await require_account(request.app.state.db, owner, permission)
    return owner


@router.post("/{identifier}/automation-targets")
async def targets(identifier: UUID, body: Authorization, request: Request):
    owner = await authorize(request, identifier, body.token)
    rows = await request.app.state.db.fetch(
        "SELECT v.id,v.version,v.snapshot->>'name' AS name FROM agent_versions v JOIN agents a ON a.id=v.agent_id "
        "WHERE v.owner_id=%s AND a.owner_id=%s AND a.enabled AND v.active ORDER BY a.created_at DESC,v.version DESC",
        (owner, owner),
    )
    return {"items": rows, "current_time": datetime.now(UTC).isoformat(),
            "message": "请选择已发布智能体；列表为空时需先到智能体管理创建并发布。"}


@router.post("/{identifier}/automation-drafts")
async def draft(identifier: UUID, body: Draft, request: Request):
    owner = await authorize(request, identifier, body.token)
    if body.schedule.kind == "webhook":
        fail(422, "AUTOMATION_WEBHOOK_UI", "签名事件任务请在自动任务页面配置，密钥不交给模型")
    result = await create_schedule(request.app.state, owner, body.schedule,
        identifier=uuid5(identifier, body.call_id), lease=(identifier, body.token))
    return {"type": "automation_draft", "id": result["id"], "name": body.schedule.name,
            "enabled": result["enabled"], "message": "草稿已保存到自动任务列表；须由用户启用后才会执行。"}


def automation_tools(origin: str, identifier: UUID, token: UUID,
                     internal_api_url: str = DEFAULT_INTERNAL_API_URL) -> list:
    base = validate_internal_api_url(internal_api_url)
    async def call(path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            response = await client.post(f"{base}/api/v1/internal/ask-runs/{identifier}/{path}",
                                         headers={"Origin": origin}, json={"token": str(token), **payload})
        if not response.is_success:
            return {"error": response.json().get("error", {}), "created": False}
        return response.json()

    @tool
    async def list_automation_agents() -> dict:
        """列出本人可用的已发布智能体及当前时间。创建自动任务前调用；不得编造版本ID。"""
        return await call("automation-targets", {})

    @tool
    async def create_automation_draft(schedule: ScheduleInput, runtime: ToolRuntime) -> dict:
        """按用户明确要求创建暂停的定时任务草稿。先列出可用智能体；时间/目标不清时询问。

        kind只用once、interval或cron，starts_at含时区；默认Asia/Shanghai。
        创建后用户须在卡片或自动任务页启用，不得声称已执行。不能配置签名事件或泄露凭证。
        """
        return await call("automation-drafts", {"schedule": schedule.model_dump(mode="json"),
                                                "call_id": runtime.tool_call_id})

    return [list_automation_agents, create_automation_draft]
