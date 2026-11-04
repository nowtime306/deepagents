"""Instance-local release restriction; management and normal Ask remain available."""

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.routing import Match

from app.errors import fail

BLOCKED = {
    "workflow_api": {"test_run", "test_node", "published_run"},
    "external_workflows": {"invoke"},
    "automation_api": {"schedule_state", "batch_create", "batch_action"},
    "automation_events": {"receive"},
    "repository_api": {"probe"},
    "repository_actions": {"prepare", "decide", "check", "evidence"},
    "work_run_api": {"start", "decide", "resume_run", "recovery_request"},
    "browser_api": {"create", "action", "renew", "close", "review"},
    "browser_skill_api": {"start", "stop", "cancel", "status"},
    "operations_jobs_api": {"schedule", "enqueue"},
    "evaluation_api": {"experiment"},
    "agent_evaluation_platform_api": {"create_run"},
    "media_retrieval_video": {"upload"},
    "knowledge_ocr": {"extract"},
    "notification_channels": {"retry"},
    "identity_recovery": {"add_contact", "request_reset"},
    "identity_api": {"challenge"},
    "work_resources": {"invoke_workflow"},
    "speech_api": {"understand", "transcribe", "create"},
    "vision_api": {"analyze", "create_image", "analyze_video"},
}
CODE = "RELEASE_EXECUTION_PAUSED"
MESSAGE = "此版本尚未开放该执行功能，未提交任务。"


def bind(database, settings) -> None:
    """Bind one database instance without altering other apps or silently rebinding."""
    selected = getattr(settings, "restricted_execution", False)
    previous = getattr(database, "restricted_execution", None)
    if previous is not None and previous is not selected:
        raise ValueError("Conflicting execution policy for the same database instance")
    database.restricted_execution = selected


def require_execution(database) -> None:
    """Reject internal effects before a database lookup or submission."""
    if getattr(database, "restricted_execution", False):
        fail(503, CODE, MESSAGE)


def leaves(routes):
    """Read FastAPI's lazily included routes without forcing or replacing endpoints."""
    for route in routes:
        if hasattr(route, "original_router"):
            yield from leaves(route.original_router.routes)
        elif hasattr(route, "endpoint"):
            yield route


def install_gate(application: FastAPI) -> None:
    """Freeze required endpoint identities; new/missing identities require review."""
    routes = list(leaves(application.routes))
    expected = {(module, name) for module, names in BLOCKED.items() for name in names}
    actual = {(route.endpoint.__module__.rsplit(".", 1)[-1], route.endpoint.__name__) for route in routes}
    # Reject missing known identities; unknown future execution routes are not auto-classified.
    # New routes require manual review of this list before release.
    if not expected <= actual:
        raise ValueError("Restricted execution endpoint identities changed")

    @application.middleware("http")
    async def execution_gate(request, call_next):
        for route in routes:
            matched, _ = route.matches(request.scope)
            if matched == Match.FULL:
                identity = (route.endpoint.__module__.rsplit(".", 1)[-1], route.endpoint.__name__)
                if identity in expected:
                    return JSONResponse({"error": {"code": CODE, "message": MESSAGE}}, status_code=503)
                break
        return await call_next(request)
