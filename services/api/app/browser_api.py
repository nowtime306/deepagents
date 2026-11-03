"""Authenticated browser frames and explicit manual actions; no remote endpoint material."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from app.browser_workbench import PUBLIC_CONTROL
from app.errors import APIError, fail
from app.permissions import require_account
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["浏览器工作台"])


class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    release_environment: StrictBool = False
    clear_login: StrictBool = False


class CloseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release_environment: StrictBool = False
    clear_login: StrictBool = False


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    kind: str = Field(min_length=1, max_length=30)
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=3)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledged: StrictBool


def service(request):
    value = getattr(request.app.state, "browser_workbench", None)
    if value is None:
        fail(503, "BROWSER_WORKBENCH_UNAVAILABLE", "浏览器工作台尚未就绪，请检查任务服务")
    return value


async def own_run(identifier, request):
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT * FROM work_queue_runs WHERE id=%s AND owner_id=%s AND environment='browser'",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "BROWSER_RUN_NOT_FOUND", "浏览器任务不存在或无权访问")
    return rows[0]


@router.get("/work-runs/{identifier}/browser")
async def summary(identifier: UUID, request: Request) -> dict:
    run = await own_run(identifier, request)
    database = request.app.state.db
    sessions = await database.fetch(
        "SELECT s.state,s.browser_persistent,s.data_lost,(t.token_ciphertext<>'') AS transport_ready "
        "FROM work_sandbox_sessions s LEFT JOIN work_browser_transports t ON t.session_id=s.id "
        "WHERE s.run_id=%s ORDER BY s.created_at DESC LIMIT 1",
        (identifier,),
    )
    controls = await database.fetch(
        f"SELECT {PUBLIC_CONTROL} FROM work_browser_controls WHERE run_id=%s AND owner_id=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (identifier, run["owner_id"]),
    )
    state = await database.fetch(
        "SELECT status,revision,(state_ciphertext<>'') AS has_saved_state FROM work_browser_states "
        "WHERE task_id=%s AND owner_id=%s",
        (run["task_id"], run["owner_id"]),
    )
    actions = []
    if controls:
        actions = await database.fetch(
            "SELECT request_id,kind,status,reviewed_at,created_at FROM work_browser_actions "
            "WHERE control_id=%s ORDER BY created_at DESC LIMIT 20",
            (controls[0]["id"],),
        )
    return {
        "enabled": getattr(request.app.state, "browser_workbench", None) is not None,
        "session": sessions[0] if sessions else None,
        "control": controls[0] if controls else None,
        "login": state[0] if state else None,
        "actions": actions,
    }


@router.get("/work-runs/{identifier}/browser/frame")
async def frame(identifier: UUID, request: Request) -> Response:
    run = await own_run(identifier, request)
    try:
        async with service(request).connect(run) as bridge:
            data = await bridge.transport.request(frame=True)
        return Response(
            data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )
    except APIError:
        raise
    except Exception:
        fail(503, "BROWSER_FRAME_UNAVAILABLE", "当前截图暂不可用；可能正在操作或环境已过期")


@router.post("/work-runs/{identifier}/browser/controls", status_code=202)
async def create(identifier: UUID, body: ControlInput, request: Request) -> dict:
    run = await own_run(identifier, request)
    await require_account(request.app.state.db, run["owner_id"], "work.execute")
    control = await service(request).admit(
        identifier,
        run["owner_id"],
        body.request_id,
        release_environment=body.release_environment,
        clear_login=body.clear_login,
    )
    return {"id": control}


@router.post("/work-browser-controls/{identifier}/actions")
async def action(identifier: UUID, body: ActionInput, request: Request) -> dict:
    user = await identity(request)
    await require_account(request.app.state.db, user["id"], "work.execute")
    try:
        return await service(request).action(identifier, user["id"], body.request_id, body.kind, body.parameters)
    except ValueError as error:
        fail(422, "BROWSER_ACTION_INVALID", str(error))


@router.post("/work-browser-controls/{identifier}/renew")
async def renew(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    control = await service(request).control(identifier, user["id"])
    await service(request).guard_control(control)
    rows = await request.app.state.db.fetch(
        "UPDATE work_browser_controls SET lease_until=LEAST(deadline,now()+interval '120 seconds'),updated_at=now() "
        "WHERE id=%s AND owner_id=%s AND status='READY' AND lease_until>now() AND deadline>now() RETURNING lease_until",
        (identifier, user["id"]),
    )
    if not rows:
        fail(409, "BROWSER_CONTROL_EXPIRED", "控制已过期，不能续期")
    return rows[0]


@router.post("/work-browser-controls/{identifier}/close", status_code=202)
async def close(identifier: UUID, body: CloseInput, request: Request) -> dict:
    user = await identity(request)
    await service(request).request_close(
        identifier, user["id"], release_environment=body.release_environment, clear_login=body.clear_login
    )
    return {"accepted": True}


@router.post("/work-browser-controls/{identifier}/actions/{action_id}/review")
async def review(identifier: UUID, action_id: UUID, body: ReviewInput, request: Request) -> dict:
    user = await identity(request)
    await require_account(request.app.state.db, user["id"], "work.execute")
    await service(request).control(identifier, user["id"])
    if not body.acknowledged:
        fail(422, "BROWSER_REVIEW_REQUIRED", "请先检查当前页面，确认不会重复提交")
    rows = await request.app.state.db.fetch(
        "UPDATE work_browser_actions SET reviewed_at=now() WHERE control_id=%s AND request_id=%s "
        "AND status='UNKNOWN' RETURNING request_id",
        (identifier, action_id),
    )
    if not rows:
        fail(409, "BROWSER_ACTION_NOT_UNKNOWN", "此操作不是待检查的未知结果")
    return {"reviewed": True}
