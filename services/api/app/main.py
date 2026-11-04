"""Authenticated platform API and replayable Ask event transport."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opensandbox.config import ConnectionConfig
from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.account_api import router as account_router
from app.account_api import throttle
from app.agent_api import router as agent_router
from app.agent_evaluation_environment import router as agent_evaluation_environment_router
from app.agent_evaluation_platform_api import router as agent_evaluation_platform_router
from app.agent_evaluation_platform_extension_api import router as agent_evaluation_platform_extension_router
from app.agent_versions import router as agent_version_router
from app.agent_versions import selected as selected_agent_version
from app.ask_automations import router as ask_automations_router
from app.ask_queue import AskQueue
from app.ask_resources import router as ask_resources_router
from app.attachment_api import router as attachment_router
from app.attachment_api import storage as attachment_storage
from app.automation_api import router as automation_router
from app.automation_events import router as automation_events_router
from app.automation_scheduler import AutomationScheduler
from app.browser_api import router as browser_router
from app.browser_workbench import BrowserWorkbench
from app.capabilities import CapabilityService
from app.capability_api import router as capability_router
from app.config import ConversationInput, LoginInput, RunInput, Settings
from app.conversation_archive import router as archive_router
from app.conversation_branches import router as branches_router
from app.conversation_references import freeze as freeze_references
from app.conversation_references import router as references_router
from app.conversation_workspace import TitleInput, lock_project
from app.conversation_workspace import listing as workspace_listing
from app.conversation_workspace import router as workspace_router
from app.diagnostics_api import router as diagnostics_router
from app.errors import APIError, fail
from app.evaluation_api import router as evaluation_router
from app.evaluation_worker import EvaluationWorker
from app.external_workflows import router as external_workflow_router
from app.harness import ASK_POLICY
from app.knowledge import KnowledgeService
from app.knowledge_documents import router as knowledge_documents_router
from app.knowledge_ocr import router as knowledge_ocr_router
from app.mcp_service import MCPService
from app.memory_api import router as memory_router
from app.memory_candidates import router as memory_candidates_router
from app.memory_jobs import purge_source as purge_memory_source
from app.memory_jobs import router as memory_jobs_router
from app.memory_vectors import router as memory_vectors_router
from app.message_feedback import router as message_feedback_router
from app.model_api import router as model_router
from app.model_capabilities import image_input, native_search_mode
from app.model_catalog import supports_chat
from app.models import CredentialVault, ModelRegistry
from app.multimodal_api import router as multimodal_router
from app.notification_channels import router as notification_channels_router
from app.notifications import router as notifications_router
from app.operations_api import router as operations_router
from app.organization_api import router as organization_router
from app.permissions import account
from app.runtime import Runtime
from app.security import COOKIE, hash_password, identity, token_digest, verify_password
from app.speech_api import router as speech_router
from app.storage import Database
from app.team_api import router as team_router
from app.usage_api import router as usage_router
from app.vision_api import router as vision_router
from app.web_search import ensure_available
from app.web_search import router as search_router
from app.work_input_api import router as work_input_router
from app.work_resources import router as work_resources_router
from app.work_run_api import router as work_run_router
from app.work_task_api import router as work_task_router
from app.workflow_api import router as workflow_router
from app.workflow_queue import WorkflowQueue, reconcile_startup
from app.workflow_runtime import WorkflowRuntime
from app.workflow_tasks import router as workflow_tasks_router

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
# Equal-cost fallback when the requested user does not exist.
DUMMY_PASSWORD = hash_password(secrets.token_urlsafe(32))


async def owned_conversation(request: Request, conversation_id: UUID) -> dict:
    """Check ownership for every conversation operation."""
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT * FROM conversations WHERE id=%s AND owner_id=%s",
        (conversation_id, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "对话不存在或无权访问")
    return rows[0]


async def owned_run(request: Request, run_id: UUID) -> dict:
    """Authorize progress, cancellation and event replay using the parent resource."""
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT r.* FROM task_runs r JOIN conversations c ON c.id=r.conversation_id WHERE r.id=%s AND c.owner_id=%s",
        (run_id, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "运行不存在或无权访问")
    return rows[0]


def create_app(
    settings: Settings | None = None, *, evaluation_sandbox_connection: ConnectionConfig | None = None
) -> FastAPI:
    """Create the local application with explicitly managed PostgreSQL connections."""
    settings = settings or Settings.from_env()
    if evaluation_sandbox_connection and (
        not settings.evaluation_instance_id
        or not conninfo_to_dict(settings.database_url).get("dbname", "").endswith("_test")
        or not settings.data_dir.name.endswith("_test")
    ):
        raise ValueError("Dedicated evaluation sandbox configuration requires an isolated test instance")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(settings.database_url)
        from app.execution_policy import bind

        bind(database, settings)
        try:
            await database.pool.open(wait=True)
            async with (
                await AsyncConnection.connect(settings.database_url, autocommit=True) as guard,
                AsyncPostgresSaver.from_conn_string(settings.database_url) as saver,
            ):
                lock = await guard.execute("SELECT pg_try_advisory_lock(710014)")
                if not (await lock.fetchone())[0]:
                    msg = "当前数据库已有 API 进程运行；P0/P1 仅支持单进程"
                    raise RuntimeError(msg)
                app.state.db = database
                from app.browser_skill_runtime import BrowserSkillRuntime

                app.state.browser_skills = BrowserSkillRuntime(database, settings.data_dir)
                if not settings.restricted_execution:
                    await app.state.browser_skills.recover()
                encrypted = await database.fetch(
                    "SELECT 1 FROM model_configs WHERE api_key_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM mcp_servers WHERE token_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM search_settings WHERE api_key_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM automations WHERE webhook_key_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM notification_channels WHERE endpoint_ciphertext<>'' "
                    "OR secret_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_mfa WHERE secret_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_providers WHERE secret_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_channels WHERE config_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_contacts WHERE address_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_pending_contacts WHERE address_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_oidc_states WHERE verifier_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM identity_deliveries WHERE payload_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM mcp_oauth_clients WHERE secret_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM mcp_oauth_flows WHERE verifier_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM mcp_oauth_grants WHERE access_ciphertext<>'' OR refresh_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM work_browser_states WHERE state_ciphertext<>'' "
                    "UNION ALL SELECT 1 FROM work_browser_transports WHERE token_ciphertext<>'' LIMIT 1"
                )
                repository_secrets = await database.fetch(
                    "SELECT 1 FROM repositories WHERE token_ciphertext<>'' LIMIT 1"
                )
                vault = CredentialVault(settings.data_dir / "model.key", create=not (encrypted or repository_secrets))
                app.state.models = ModelRegistry(database, vault)
                app.state.knowledge = KnowledgeService(database, app.state.models, settings.data_dir / "vectors")
                app.state.mcp = MCPService(database, vault)
                app.state.capabilities = CapabilityService(database, app.state.knowledge, app.state.mcp)
                app.state.runtime = Runtime(database, saver, settings, model_registry=app.state.models)
                app.state.runtime.capabilities = app.state.capabilities
                if not settings.restricted_execution:
                    await database.execute(
                        "UPDATE repository_actions SET status='UNKNOWN',error_code='SERVER_RESTARTED',"
                        "updated_at=now() WHERE status='STARTED'"
                    )
                browser = None
                connection = evaluation_sandbox_connection if settings.evaluation_instance_id else None
                key_path = Path(__file__).parents[2] / "sandbox/.local/manager.key"
                if not settings.evaluation_instance_id and settings.work_enabled and key_path.is_file():
                    connection = ConnectionConfig(
                        domain="127.0.0.1:18180", api_key=key_path.read_text().strip(), use_server_proxy=True
                    )
                if settings.work_enabled and connection:
                    from app.work_artifacts import WorkArtifacts

                    browser = BrowserWorkbench(
                        database,
                        vault,
                        connection,
                        WorkArtifacts(database, settings.data_dir / "work-artifacts"),
                    )
                app.state.browser_workbench = browser
                if not settings.restricted_execution:
                    await reconcile_startup(database)
                    # P0/P1 support one API process. Interrupted short runs fail visibly.
                    interrupted = await database.fetch(
                        "SELECT id FROM task_runs WHERE status IN ('PENDING','RUNNING') "
                        "AND (NOT %s OR (status='RUNNING' AND lease_token IS NULL))",
                        (settings.ask_worker_enabled,),
                    )
                    for row in interrupted:
                        await database.event(
                            row["id"],
                            "run.failed",
                            {"message": "服务重启，请重新发送"},
                            status="FAILED",
                            error="SERVER_RESTARTED",
                        )
                workflow_queue = WorkflowQueue(
                    database,
                    WorkflowRuntime(
                        database,
                        app.state.models,
                        app.state.capabilities,
                        lambda config: app.state.runtime.model_factory(config),
                    ),
                )
                app.state.workflow_queue = workflow_queue
                from app.service_fence import ServiceFence

                fence = ServiceFence(
                    database, guard, "api", [(710014, False)], stop_file=settings.data_dir / "services/api.stop"
                )
                await fence.start()
                database.service_fence = app.state.service_fence = fence
                service_monitor = asyncio.create_task(fence.monitor())
                consumer = (asyncio.create_task(workflow_queue.serve())
                            if not settings.restricted_execution else None)
                automatic = (
                    asyncio.create_task(AutomationScheduler(app.state).serve()) if settings.automation_enabled else None
                )
                evaluation = (
                    asyncio.create_task(EvaluationWorker(database, app.state.knowledge).serve())
                    if settings.evaluation_enabled
                    else None
                )
                browser_maintenance = asyncio.create_task(browser.serve()) if browser else None
                from app.identity_delivery import start_delivery, stop_delivery

                if not settings.restricted_execution:
                    start_delivery(app)
                from app.media_retrieval_video import serve as serve_media

                media_worker = asyncio.create_task(serve_media(app.state)) if settings.media_worker_enabled else None
                try:
                    yield
                finally:
                    await app.state.browser_skills.close()
                    if consumer:
                        consumer.cancel()
                        await asyncio.gather(consumer, return_exceptions=True)
                    if automatic:
                        automatic.cancel()
                        await asyncio.gather(automatic, return_exceptions=True)
                    if evaluation:
                        evaluation.cancel()
                        await asyncio.gather(evaluation, return_exceptions=True)
                    if browser_maintenance:
                        browser_maintenance.cancel()
                        await asyncio.gather(browser_maintenance, return_exceptions=True)
                    if browser:
                        await browser.close()
                    if not settings.restricted_execution:
                        await stop_delivery(app)
                    if media_worker:
                        media_worker.cancel()
                        await asyncio.gather(media_worker, return_exceptions=True)
                    await app.state.runtime.close()
                    await app.state.knowledge.vectors.close()
                    service_monitor.cancel()
                    await asyncio.gather(service_monitor, return_exceptions=True)
                    await fence.close()
        finally:
            await database.pool.close()

    app = FastAPI(title="自主智能体平台", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def configuration_context(request: Request, call_next):
        from app.configuration_history import begin_context, end_context

        tokens = begin_context()
        try:
            return await call_next(request)
        finally:
            end_context(tokens)

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        fence = getattr(app.state, "service_fence", None)
        if fence and request.url.path != "/health/live":
            try:
                await fence.check()
            except APIError as error:
                return JSONResponse(
                    status_code=503,
                    content={"error": {"code": error.code, "message": error.message, "request_id": str(uuid4())}},
                    headers={"Cache-Control": "no-store"},
                )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") != settings.web_origin and not request.url.path.startswith(
                "/api/public/v1/"
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "ORIGIN_REJECTED",
                            "message": "请求来源不受信任",
                            "request_id": str(uuid4()),
                        }
                    },
                )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            from app.maintenance import request_slot

            try:
                async with request_slot(settings.database_url):
                    response = await call_next(request)
            except APIError as error:
                return JSONResponse(
                    status_code=error.status,
                    content={"error": {"code": error.code, "message": error.message, "request_id": str(uuid4())}},
                )
        else:
            response = await call_next(request)
        actor = getattr(request.state, "actor", None)
        if actor and (request.method in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path.endswith("/download")):
            try:
                await app.state.db.execute(
                    "INSERT INTO audit_events(actor_id,method,resource_path,status_code) VALUES(%s,%s,%s,%s)",
                    (actor["id"], request.method, request.url.path[:512], response.status_code),
                )
            except Exception as error:
                logging.getLogger(__name__).error("Audit persistence failed (%s)", type(error).__name__)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = (
            "no-store, no-transform" if response.headers.get("content-type", "").startswith("text/event-stream")
            else "no-store"
        )
        # The local Next rewrite proxy polls at the API's idle-close boundary.
        # Explicit close prevents stale pooled sockets without replaying requests.
        if request.headers.get("x-forwarded-host"):
            response.headers["Connection"] = "close"
        return response

    @app.exception_handler(APIError)
    async def api_error(request: Request, error: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": str(uuid4()),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "输入格式不正确，请检查必填项和内容长度",
                    "request_id": str(uuid4()),
                }
            },
        )

    register_routes(app)
    from app.resource_configuration_api import router as configuration_router
    from app.resource_recovery_api import router as resource_recovery_router
    from app.resource_trace_api import router as resource_trace_router

    app.include_router(configuration_router)
    app.include_router(resource_trace_router)
    app.include_router(resource_recovery_router)
    from app.repository_actions import router as repository_actions_router
    from app.repository_api import router as repository_router

    app.include_router(repository_router)
    app.include_router(repository_actions_router)
    from app.service_api import router as service_router

    app.include_router(service_router)
    from app.model_routing import router as routing_router

    app.include_router(routing_router)
    from app.money_api import router as money_router

    app.include_router(money_router)
    from app.operations_jobs_api import router as operations_jobs_router

    app.include_router(operations_jobs_router)
    app.include_router(organization_router)
    app.include_router(account_router)
    app.include_router(automation_router)
    app.include_router(automation_events_router)
    app.include_router(notifications_router)
    app.include_router(notification_channels_router)
    app.include_router(team_router)
    app.include_router(ask_resources_router)
    app.include_router(ask_automations_router)
    app.include_router(operations_router)
    from app.monitoring_api import router as monitoring_router

    app.include_router(monitoring_router)
    from app.quality_gate import router as quality_router

    app.include_router(quality_router)
    from app.monitoring_details import router as monitoring_details_router

    app.include_router(monitoring_details_router)
    from app.extra_cost_api import router as extra_cost_router
    from app.identity_api import router as identity_router
    from app.identity_oidc import router as oidc_router
    from app.identity_recovery import router as recovery_router
    from app.mcp_oauth import router as tool_oauth_router
    from app.media_retrieval_api import router as media_retrieval_router
    from app.media_retrieval_video import router as video_retrieval_router

    for router in (identity_router, oidc_router, recovery_router, tool_oauth_router,
                    media_retrieval_router, extra_cost_router, video_retrieval_router):
        app.include_router(router)
    app.include_router(external_workflow_router)
    app.include_router(memory_candidates_router)
    app.include_router(memory_jobs_router)
    app.include_router(memory_vectors_router)
    app.include_router(memory_router)
    app.include_router(model_router)
    from app.habit_api import router as habit_router

    app.include_router(habit_router)
    from app.browser_skill_api import router as browser_skill_router

    app.include_router(browser_skill_router)
    app.include_router(capability_router)
    app.include_router(knowledge_documents_router)
    app.include_router(knowledge_ocr_router)
    app.include_router(evaluation_router)
    app.include_router(agent_evaluation_environment_router)
    app.include_router(agent_evaluation_platform_router)
    app.include_router(agent_evaluation_platform_extension_router)
    app.include_router(message_feedback_router)
    app.include_router(search_router)
    app.include_router(workflow_router)
    from app.workflow_versions import router as workflow_version_router

    app.include_router(workflow_version_router)
    app.include_router(workflow_tasks_router)
    app.include_router(speech_router)
    app.include_router(vision_router)
    app.include_router(multimodal_router)
    app.include_router(work_task_router)
    app.include_router(work_input_router)
    app.include_router(work_run_router)
    app.include_router(browser_router)
    app.include_router(work_resources_router)
    app.include_router(archive_router)
    app.include_router(branches_router)
    app.include_router(references_router)
    app.include_router(workspace_router)
    app.include_router(attachment_router)
    app.include_router(usage_router)
    app.include_router(diagnostics_router)
    if settings.restricted_execution:
        from app.execution_policy import install_gate

        install_gate(app)
    return app


def register_routes(app: FastAPI) -> None:
    """Install health, identity, conversations and execution endpoints."""

    @app.get("/health/live")
    async def live() -> dict:
        return {"status": "ok", "service": "owner-agent"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict:
        await request.app.state.db.fetch("SELECT 1")
        return {"status": "ready", "models_configured": bool(await app.state.models.list(enabled_only=True))}

    @app.post("/api/v1/auth/login")
    async def login(body: LoginInput, request: Request) -> JSONResponse:
        peer = request.client.host if request.client else "unknown"
        await throttle(request, "login-ip", peer, 120, 60)
        await throttle(request, "login-account", body.username.lower(), 60, 60)
        rows = await app.state.db.fetch("SELECT * FROM users WHERE lower(username)=%s", (body.username.lower(),))
        encoded = rows[0]["password_hash"] if rows else DUMMY_PASSWORD
        valid = await asyncio.to_thread(verify_password, body.password, encoded)
        if not valid or not rows:
            fail(401, "INVALID_CREDENTIALS", "账号或密码错误")
        token = secrets.token_urlsafe(32)
        async with app.state.db.pool.connection() as connection:
            # Serialize session creation with role changes and password resets.
            # Verification happens outside this short lock, then its hash is rechecked.
            await connection.execute("SELECT pg_advisory_xact_lock(710018)")
            current = await (
                await connection.execute("SELECT password_hash FROM users WHERE id=%s FOR UPDATE", (rows[0]["id"],))
            ).fetchone()
            if not current or current["password_hash"] != encoded:
                fail(401, "INVALID_CREDENTIALS", "账号或密码已变化，请重新登录")
            user = await account(app.state.db, rows[0]["id"])
            from app.identity_api import login_challenge

            challenge = await login_challenge(connection, request, user)
            if challenge is not None:
                return challenge
            await connection.execute(
                "INSERT INTO auth_sessions(token_hash,owner_id,expires_at) VALUES (%s,%s,%s)",
                (token_digest(token), rows[0]["id"], datetime.now(UTC) + timedelta(hours=12)),
            )
        request.state.actor = user
        response = JSONResponse(jsonable_encoder(user))
        response.set_cookie(
            COOKIE, token, httponly=True, secure=app.state.settings.cookie_secure, samesite="strict", max_age=43200
        )
        return response

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        await app.state.db.execute(
            "DELETE FROM auth_sessions WHERE token_hash=%s", (token_digest(request.cookies.get(COOKIE, "")),)
        )
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/v1/auth/me")
    async def me(request: Request) -> dict:
        return await identity(request)

    @app.get("/api/v1/models")
    async def models(request: Request) -> dict:
        await identity(request)
        rows = await app.state.models.list(enabled_only=True)

        def metadata(row):
            from types import SimpleNamespace

            config = SimpleNamespace(**row)
            return {
                "id": row["id"],
                "name": row["name"],
                "supports_images": image_input(config),
                "native_search_mode": native_search_mode(config),
            }

        return {
            "items": [metadata(row) for row in rows if supports_chat(row["kind"], row["protocol"])],
            "vision_models": [metadata(row) for row in rows if row["kind"] == "vision" and row["protocol"] == "openai"],
        }

    app.include_router(agent_router)
    app.include_router(agent_version_router)
    register_conversations(app)
    register_runs(app)


def register_conversations(app: FastAPI) -> None:
    """Install owner-filtered conversation reads and writes."""

    @app.post("/api/v1/conversations", status_code=201)
    async def create(body: ConversationInput, request: Request) -> dict:
        user = await identity(request)
        if body.agent_id:
            available = await app.state.db.fetch(
                "SELECT id FROM agents WHERE id=%s AND owner_id=%s AND enabled", (body.agent_id, user["id"])
            )
            if not available:
                fail(404, "AGENT_NOT_FOUND", "智能体不存在或已停用")
        if body.agent_version_id:
            await selected_agent_version(request, user["id"], body.agent_id, body.agent_version_id)
        async with app.state.db.pool.connection() as connection:
            await lock_project(connection, body.project_id, user["id"])
            cursor = await connection.execute(
                "INSERT INTO conversations(id,owner_id,title,agent_id,agent_version_id,project_id) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                (uuid4(), user["id"], body.title, body.agent_id, body.agent_version_id, body.project_id),
            )
            return await cursor.fetchone()

    @app.get("/api/v1/conversations")
    async def listing(
        request: Request,
        before: UUID | None = None,
        archived: bool = False,
        q: str = Query(default="", max_length=200),
        project_id: UUID | None = None,
        unassigned: bool = False,
    ) -> dict:
        return await workspace_listing(request, before, archived, q.strip(), project_id, unassigned)

    @app.get("/api/v1/conversations/{conversation_id}")
    async def detail(conversation_id: UUID, request: Request) -> dict:
        return await owned_conversation(request, conversation_id)

    @app.patch("/api/v1/conversations/{conversation_id}")
    async def rename(conversation_id: UUID, body: TitleInput, request: Request) -> dict:
        await owned_conversation(request, conversation_id)
        await app.state.db.execute("UPDATE conversations SET title=%s WHERE id=%s", (body.title, conversation_id))
        return {"id": conversation_id, "title": body.title}

    @app.delete("/api/v1/conversations/{conversation_id}")
    async def delete(conversation_id: UUID, request: Request) -> dict:
        await owned_conversation(request, conversation_id)
        async with app.state.db.pool.connection() as connection:
            await connection.execute("SELECT id FROM conversations WHERE id=%s FOR UPDATE", (conversation_id,))
            cursor = await connection.execute(
                "SELECT 1 FROM task_runs WHERE conversation_id=%s AND status IN ('PENDING','RUNNING')",
                (conversation_id,),
            )
            if await cursor.fetchone():
                fail(409, "RUN_STATE_CONFLICT", "请先停止当前生成再删除对话")
            await purge_memory_source(connection, conversation_id, app.state.runtime.saver)
            files = await (
                await connection.execute(
                    "SELECT id FROM conversation_attachments WHERE conversation_id=%s",
                    (conversation_id,),
                )
            ).fetchall()
            await app.state.runtime.saver.adelete_thread(str(conversation_id))
            await connection.execute("DELETE FROM conversations WHERE id=%s", (conversation_id,))
        for file in files:
            attachment_storage(request, file["id"]).unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/v1/conversations/{conversation_id}/messages")
    async def messages(conversation_id: UUID, request: Request, before: UUID | None = None) -> dict:
        conversation = await owned_conversation(request, conversation_id)
        rows = await app.state.db.fetch(
            "SELECT * FROM conversation_messages WHERE conversation_id=%s AND "
            "(%s::uuid IS NULL OR (created_at,id)<(SELECT created_at,id FROM conversation_messages "
            "WHERE id=%s AND conversation_id=%s)) ORDER BY created_at DESC,id DESC LIMIT 101",
            (conversation_id, before, before, conversation_id),
        )
        runs = await app.state.db.fetch(
            "SELECT id,status FROM task_runs WHERE conversation_id=%s ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        )
        pending = await app.state.db.fetch(
            "SELECT id,name,size,media_type FROM conversation_attachments WHERE conversation_id=%s AND used_at IS NULL "
            "ORDER BY created_at,id",
            (conversation_id,),
        )
        draft = await app.state.db.fetch(
            "SELECT draft_text FROM conversation_branches WHERE conversation_id=%s",
            (conversation_id,),
        )
        return {
            "saved_draft": draft[0]["draft_text"] if draft else "",
            "pending_attachments": pending,
            "items": list(reversed(rows[:100])),
            "next_before": str(rows[99]["id"]) if len(rows) > 100 else None,
            "latest_run": runs[0] if runs else None,
            "agent_id": conversation["agent_id"],
            "agent_version_id": conversation["agent_version_id"],
            "archived": conversation["archived"],
            "native_search": conversation["native_search"],
        }


def register_runs(app: FastAPI) -> None:
    """Install model execution, cancellation and persisted SSE replay."""
    settings = app.state.settings

    @app.get("/api/v1/runs")
    async def listing(request: Request) -> dict:
        user = await identity(request)
        rows = await app.state.db.fetch(
            "SELECT r.id,r.conversation_id,r.model_id,r.status,r.error_code,r.created_at,r.finished_at,"
            "c.title AS conversation_title FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE c.owner_id=%s AND c.internal_purpose IS NULL ORDER BY r.created_at DESC,r.id DESC LIMIT 100",
            (user["id"],),
        )
        return {"items": rows}

    @app.post("/api/v1/conversations/{conversation_id}/runs", status_code=202)
    async def submit(conversation_id: UUID, body: RunInput, request: Request) -> dict:
        conversation = await owned_conversation(request, conversation_id)
        if conversation.get("internal_purpose"):
            fail(409, "INTERNAL_CONVERSATION_READONLY", "这是内部提取记录，请在对应能力页面发起新提取")
        if conversation["archived"]:
            fail(409, "CONVERSATION_ARCHIVED", "请先恢复归档对话，再发送消息")
        snapshot = None
        if conversation["agent_id"] and not conversation.get("agent_version_id"):
            agents = await app.state.db.fetch(
                "SELECT id,name,instructions,model_id,revision,web_enabled,web_mode,knowledge_ids,"
                "skill_ids,mcp_ids,memory_enabled,workflow_version_ids,team,model_routing "
                "FROM agents "
                "WHERE id=%s AND owner_id=%s AND enabled",
                (conversation["agent_id"], conversation["owner_id"]),
            )
            if not agents:
                fail(422, "AGENT_UNAVAILABLE", "该对话的智能体已停用")
            snapshot = jsonable_encoder(agents[0])
            await app.state.capabilities.validate(conversation["owner_id"], snapshot)
            if snapshot["web_enabled"] and snapshot["web_mode"] == "tavily":
                await ensure_available(app.state.db)
        if conversation.get("agent_version_id"):
            snapshot = await selected_agent_version(
                request, conversation["owner_id"], conversation["agent_id"], conversation["agent_version_id"]
            )
        if snapshot is None:
            snapshot = await app.state.capabilities.bindings(conversation["owner_id"])
        snapshot = await app.state.capabilities.freeze(conversation["owner_id"], snapshot)
        run_id = uuid4()
        from app.model_routing import resolve

        routing = snapshot.get("model_routing", False) if conversation["agent_id"] else body.model_routing
        model = await app.state.models.get(snapshot.get("model_id", body.model_id), enabled_only=not routing)
        if routing:
            images = await app.state.db.fetch(
                "SELECT 1 FROM conversation_attachments WHERE conversation_id=%s "
                "AND owner_id=%s AND media_type LIKE 'image/%%' "
                "AND (used_at IS NOT NULL OR id=ANY(%s::uuid[])) LIMIT 1",
                (conversation_id, conversation["owner_id"], body.attachment_ids),
            )
            model = await resolve(
                app.state.models,
                conversation["owner_id"],
                run_id,
                model.id,
                enabled=True,
                allow_vision=True,
                expected=snapshot.get("route_pin"),
                images=bool(images),
                tools=True,
                native=bool(body.native_search if body.native_search is not None else conversation["native_search"])
                and not conversation["agent_id"],
                openai_native=bool(snapshot.get("web_enabled") and snapshot.get("web_mode") == "openai_native"),
            )
            snapshot["route_selection_id"] = str(model.route_selection_id)
        if not supports_chat(model.kind, model.protocol) and not (
            model.kind == "vision" and model.protocol == "openai"
        ):
            fail(422, "MODEL_KIND_INVALID", "请选择聊天或图片理解模型")
        requested_search = body.native_search if body.native_search is not None else conversation["native_search"]
        mode = native_search_mode(model)
        if not conversation["agent_id"]:
            if body.native_search is True and not mode:
                fail(422, "NATIVE_SEARCH_UNSUPPORTED", "此模型尚未适配原生联网，请选择支持的模型")
            snapshot["ask_native_mode"] = mode if requested_search else None
        elif body.native_search is False:
            snapshot["web_enabled"] = False
        elif body.native_search is True and not snapshot.get("web_enabled"):
            fail(403, "AGENT_SEARCH_DISABLED", "此智能体未授权联网，请先在智能体设置中启用")
        if (
            snapshot
            and snapshot.get("web_enabled")
            and snapshot["web_mode"] == "openai_native"
            and (model.provider != "openai" or model.protocol != "openai")
        ):
            fail(422, "WEB_MODE_UNSUPPORTED", "OpenAI 原生联网需要 OpenAI 服务商的聊天模型")
        try:
            async with app.state.db.pool.connection() as connection:
                locked = await connection.execute(
                    "SELECT id,archived FROM conversations WHERE id=%s AND owner_id=%s FOR UPDATE",
                    (conversation_id, conversation["owner_id"]),
                )
                current = await locked.fetchone()
                if not current:
                    fail(404, "NOT_FOUND", "对话已删除")
                if current["archived"]:
                    fail(409, "CONVERSATION_ARCHIVED", "请先恢复归档对话")
                attachments = []
                if body.attachment_ids:
                    documents = await connection.execute(
                        "SELECT id,name,extracted,media_type,size,digest FROM conversation_attachments "
                        "WHERE id=ANY(%s) "
                        "AND conversation_id=%s AND owner_id=%s FOR UPDATE",
                        (body.attachment_ids, conversation_id, conversation["owner_id"]),
                    )
                    attachments = await documents.fetchall()
                    if len(attachments) != len(body.attachment_ids):
                        fail(422, "ATTACHMENT_UNAVAILABLE", "附件无权访问、已删除或重复选择")
                    if any(item["media_type"].startswith("image/") for item in attachments) and not image_input(model):
                        fail(422, "MODEL_IMAGE_UNSUPPORTED", "当前模型未启用图片输入，请切换支持图片的模型")
                    if sum(len(item["extracted"]) for item in attachments) > 80000:
                        fail(413, "ATTACHMENT_TEXT_LIMIT", "本次附件文字合计超过 8 万字，请分批提问")
                    await connection.execute(
                        "UPDATE conversation_attachments SET used_at=now() WHERE id=ANY(%s)",
                        (body.attachment_ids,),
                    )
                if (
                    not image_input(model)
                    and await (
                        await connection.execute(
                            "SELECT 1 FROM conversation_attachments WHERE conversation_id=%s AND used_at IS NOT NULL "
                            "AND media_type LIKE 'image/%%' LIMIT 1",
                            (conversation_id,),
                        )
                    ).fetchone()
                ):
                    fail(422, "MODEL_IMAGE_HISTORY", "此对话含有图片，请选择支持图片输入的模型")
                if body.native_search is not None:
                    await connection.execute(
                        "UPDATE conversations SET native_search=%s WHERE id=%s", (body.native_search, conversation_id)
                    )
                await connection.execute(
                    "INSERT INTO task_runs(id,conversation_id,model_id,model_revision,status,"
                    "agent_snapshot,attachment_snapshot,harness_policy) "
                    "VALUES (%s,%s,%s,%s,'PENDING',%s,%s,%s)",
                    (
                        run_id,
                        conversation_id,
                        model.id,
                        model.revision,
                        Jsonb(snapshot) if snapshot else None,
                        Jsonb(jsonable_encoder(attachments)),
                        Jsonb(asdict(ASK_POLICY)),
                    ),
                )
                await connection.execute(
                    "UPDATE conversation_branches SET draft_text='' WHERE conversation_id=%s",
                    (conversation_id,),
                )
                await freeze_references(connection, conversation_id, run_id)
                await connection.execute(
                    "INSERT INTO conversation_messages(id,conversation_id,run_id,role,content) VALUES (%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        conversation_id,
                        run_id,
                        "user",
                        Jsonb(
                            {
                                "text": body.message,
                                "attachments": [
                                    {"id": str(item["id"]), "name": item["name"], "media_type": item["media_type"]}
                                    for item in attachments
                                ],
                            }
                        ),
                    ),
                )
        except UniqueViolation:
            fail(409, "RUN_STATE_CONFLICT", "当前对话已有生成任务")
        if not settings.ask_worker_enabled:
            app.state.runtime.start(run_id, conversation_id, body.message, model)
        return {"run_id": run_id, "status": "PENDING", "events_url": f"/api/v1/runs/{run_id}/events"}

    @app.get("/api/v1/runs/{run_id}")
    async def detail(run_id: UUID, request: Request) -> dict:
        run = await owned_run(request, run_id)
        return {key: value for key, value in run.items() if key != "lease_token"}

    @app.post("/api/v1/runs/{run_id}/cancel", status_code=202)
    async def cancel(run_id: UUID, request: Request) -> dict:
        row = await owned_run(request, run_id)
        if row["status"] in TERMINAL:
            fail(409, "RUN_STATE_CONFLICT", "此运行已结束")
        changed = await app.state.db.fetch(
            "UPDATE task_runs SET cancel_requested=true WHERE id=%s AND NOT cancel_requested RETURNING id",
            (run_id,),
        )
        task = app.state.runtime.tasks.get(run_id)
        if changed and task and row["status"] == "RUNNING":
            task.cancel()
        if settings.ask_worker_enabled:
            await AskQueue(app.state.db).reconcile()
        return {"accepted": True}

    @app.get("/api/v1/runs/{run_id}/events")
    async def events(run_id: UUID, request: Request, after_event_id: int = 0) -> StreamingResponse:
        from app.run_streams import response

        return await response(request, run_id, after_event_id)


async def event_stream(request: Request, run_id: UUID, cursor: int) -> AsyncIterator[str]:
    """Keep the existing generator entry point for server-side integrations."""
    from app.run_streams import events

    async for item in events(request, run_id, cursor):
        yield item



app = create_app()
