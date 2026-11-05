"""Explicit fixed-target extension only. No process startup or supplier fallback."""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from uuid import UUID

from app.agent_evaluation_environment import environment, read_marker
from app.errors import APIError, fail
from app.main import create_app
from app.resource_runtime import current_scope
from fastapi import Request
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, PrivateAttr

from evaluation_runner.profiles_v2.f07.contract import BASE, EXECUTION_LIMITS, INPUT, LIMITS, scene_for


class F07Model(BaseChatModel):
    _controller: object = PrivateAttr()

    @property
    def _llm_type(self):
        return "fixed-f07-timeout-v2"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("Only the genuine asynchronous Workflow path is allowed")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        await self._controller.reserve_model()
        try:
            async with asyncio.timeout(0.005):
                await asyncio.sleep(0.2)
        finally:
            self._controller.state["provider"]["status"] = "UNKNOWN"
            self._controller.save()
        raise AssertionError("The fixed actual timer must expire")


class Seal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: UUID


class Controller:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.marker = read_marker(settings.data_dir)
        if self.marker.fixture_profile.model_dump() != {"id": "handled-transform-error", "version": 2}:
            raise ValueError("Only the explicit F07 v2 instance marker is accepted")
        self.path = settings.data_dir / "f07-controller.json"
        self.lock = asyncio.Lock()
        self.reconstructed = self.path.exists()
        self.state = (
            json.loads(self.path.read_text())
            if self.reconstructed
            else {
                "phase": "NEW",
                "environment_id": str(self.marker.environment_id),
                "owner_id": str(self.marker.owner_id),
                "namespace": str(self.marker.sandbox_namespace),
                "roots": {},
                "provider": {"calls": 0, "reserved_tokens": 0, "status": "UNUSED"},
                "pure": {},
            }
        )
        if self.state["environment_id"] != str(self.marker.environment_id):
            raise ValueError("Foreign F07 controller state")

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.state, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def dispatch(self):
        if (
            self.reconstructed
            or self.state["phase"] != "ACTIVE"
            or not self.state["started"] <= time.time() < self.state["expires"]
        ):
            raise ValueError("Original active F07 deadline is required; never reset or replay")

    async def proof(self, request):
        proof = await environment(request)
        if (
            str(proof["owner_id"]) != self.state["owner_id"]
            or str(proof["sandbox_namespace"]) != self.state["namespace"]
        ):
            fail(403, "F07_SCOPE_DENIED", "固定评测归属不匹配")
        required = {"evaluation.read", "evaluation.run", "workflows.execute"}
        if not required <= set(proof["permissions"]):
            fail(403, "F07_PERMISSION_DENIED", "固定评测权限已撤销")
        return proof

    async def root(self):
        self.dispatch()
        scope = current_scope()
        if scope is None or scope.mode != "workflow" or str(scope.owner) != self.state["owner_id"]:
            raise ValueError("Genuine owned workflow scope required")
        root = str(scope.run)
        reserved = self.state["roots"].get(root)
        if reserved is None:
            raise ValueError("Root must be durably reserved at the formal API ingress")
        rows = await self.app.state.db.fetch(
            "SELECT r.*,w.owner_id FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE r.id=%s",
            (scope.run,),
        )
        if (
            len(rows) != 1
            or str(rows[0]["owner_id"]) != self.state["owner_id"]
            or rows[0]["input"] != reserved["input"]
        ):
            raise ValueError("Original native root differs")
        scene = scene_for(rows[0]["snapshot"]["definition"])
        if scene != reserved["scene"]:
            raise ValueError("Fixed business snapshot changed")
        return root, scene

    def pure_factory(self, node, value, values):
        self.dispatch()
        scope = current_scope()
        root = str(scope.run) if scope else None
        reserved = self.state["roots"].get(root, {})
        if (
            scope is None
            or scope.mode != "workflow"
            or str(scope.owner) != self.state["owner_id"]
            or reserved.get("scene") != "timer"
            or node.id != "action"
            or value != INPUT
        ):
            raise ValueError("Only the fixed pure timer node is injected")
        if root in self.state["pure"]:
            raise ValueError("A pure operation cannot be repeated")
        self.state["pure"][root] = {"started": True, "cancelled": False}
        self.save()

        async def pure_operation():
            try:
                await asyncio.sleep(0.2)
                return value.upper()
            finally:
                self.state["pure"][root]["cancelled"] = True
                self.save()

        return pure_operation()

    async def reserve_model(self):
        async with self.lock:
            root, scene = await self.root()
            self.dispatch()
            if scene != "model" or self.state["provider"]["calls"]:
                raise ValueError("Only one fixed model timeout reservation is allowed")
            self.state["provider"] = {"calls": 1, "reserved_tokens": 15, "status": "PENDING", "root": root}
            self.save()

    def factory(self, config):
        scope = current_scope()
        if (
            scope is None
            or str(scope.owner) != self.state["owner_id"]
            or scope.mode != "workflow"
            or config.id != self.state.get("model_id")
            or config.model != "eval::AET-F07::v2"
        ):
            raise ValueError("Only the sealed owned F07 model is allowed; no vendor fallback")
        model = F07Model()
        model._controller = self
        return model


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    started: float
    expires: float


def build_app(settings):
    if (
        settings.restricted_execution
        or settings.work_enabled
        or settings.ask_worker_enabled
        or settings.automation_enabled
        or settings.evaluation_enabled
        or settings.media_worker_enabled
        or not settings.data_dir.name.endswith("_test")
        or settings.run_timeout != 120
    ):
        raise ValueError("Dedicated zero-Work zero-background F07 target required")
    app = create_app(settings)
    controller = Controller(app, settings)
    app.state.evaluation_f07_controller = controller
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            application.state.runtime.model_factory = controller.factory
            application.state.runtime.pure_transform_factory = controller.pure_factory
            application.state.evaluation_execution_limits = EXECUTION_LIMITS
            yield

    app.router.lifespan_context = lifespan

    @app.post(BASE + "/activate")
    async def activate(body: Activation, request: Request):
        await controller.proof(request)
        if controller.reconstructed or controller.state["phase"] != "NEW":
            fail(409, "F07_ALREADY_ACTIVATED", "不可重置或重启原评测")
        now = time.time()
        if not body.started <= now < body.expires or body.expires != body.started + 120:
            fail(422, "F07_DEADLINE_INVALID", "必须使用原评测完整期限")
        controller.state.update(phase="ACTIVE", started=body.started, expires=body.expires)
        controller.save()
        return {"active": True, "limits": LIMITS}

    @app.post(BASE + "/seal-model")
    async def seal(body: Seal, request: Request):
        await controller.proof(request)
        controller.dispatch()
        if controller.state.get("model_id"):
            fail(409, "F07_MODEL_SEALED", "模型已绑定")
        rows = await app.state.db.fetch(
            "SELECT model,enabled,kind FROM model_configs WHERE id=%s", (str(body.model_id),)
        )
        if len(rows) != 1 or rows[0] != {"model": "eval::AET-F07::v2", "enabled": True, "kind": "chat"}:
            fail(422, "F07_MODEL_INVALID", "请选择固定评测模型")
        controller.dispatch()
        if controller.state.get("model_id"):
            fail(409, "F07_MODEL_SEALED", "模型已绑定")
        controller.state["model_id"] = str(body.model_id)
        controller.save()
        return {"sealed": True}

    async def inspect_request(request, call_next):
        path = request.url.path
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith(
            ("/api/v1/admin/models", "/api/v1/workflows")
        ):
            if request.headers.get("origin") not in {None, settings.web_origin}:
                fail(403, "F07_ORIGIN_DENIED", "来源地址不匹配")
            await controller.proof(request)
            controller.dispatch()
            if controller.state["provider"]["status"] != "UNUSED" and not path.endswith(("/test-runs", "/runs")):
                fail(409, "F07_UNKNOWN_STOPS_WRITES", "原模型结果待核验，不得继续写入")
        if (
            request.method == "POST"
            and path.startswith("/api/v1/workflows/")
            and path.endswith(("/test-runs", "/runs"))
        ):
            if request.headers.get("origin") not in {None, settings.web_origin}:
                fail(403, "F07_ORIGIN_DENIED", "来源地址不匹配")
            await controller.proof(request)
            controller.dispatch()
            if controller.state["provider"]["status"] != "UNUSED":
                fail(409, "F07_UNKNOWN_STOPS_ROOTS", "原模型结果待核验，不得继续提交运行")
            body = await request.json()
            root = str(UUID(body["run_id"]))
            workflow = path.split("/")[4]
            rows = await app.state.db.fetch(
                "SELECT definition,model_id FROM workflows WHERE id=%s AND owner_id=%s",
                (UUID(workflow), controller.marker.owner_id),
            )
            if len(rows) != 1 or rows[0]["model_id"] != controller.state.get("model_id"):
                fail(403, "F07_WORKFLOW_INVALID", "固定评测工作流不存在")
            scene = scene_for(rows[0]["definition"])
            expected = "not-json" if scene == "json" else INPUT
            async with controller.lock:
                controller.dispatch()
                if controller.state["provider"]["status"] != "UNUSED":
                    fail(409, "F07_UNKNOWN_STOPS_ROOTS", "原模型结果待核验，不得继续提交运行")
                if root in controller.state["roots"] or len(controller.state["roots"]) >= 4 or body["text"] != expected:
                    fail(409, "F07_ROOT_BLOCKED", "固定评测根或输入上限不匹配")
                controller.state["roots"][root] = {"scene": scene, "workflow_id": workflow, "input": expected}
                controller.save()
        return await call_next(request)

    @app.middleware("http")
    async def ingress(request, call_next):
        try:
            return await inspect_request(request, call_next)
        except APIError as error:
            return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)
        except (ValueError, KeyError, TypeError):
            return JSONResponse(
                {"error": {"code": "F07_REQUEST_REJECTED", "message": "固定评测请求不匹配"}}, status_code=422
            )

    @app.get(BASE + "/observations")
    async def observe(request: Request):
        await controller.proof(request)
        roots = list(controller.state["roots"])
        rows = await app.state.db.fetch(
            "SELECT id,status,error_node FROM workflow_runs WHERE id=ANY(%s::uuid[])", (roots,)
        )
        usage = await app.state.db.fetch(
            "SELECT run_id,status,input_tokens,output_tokens FROM model_usage WHERE owner_id=%s",
            (controller.marker.owner_id,),
        )
        counts = await app.state.db.fetch(
            "SELECT (SELECT count(*) FROM work_tasks WHERE owner_id=%s) AS work_tasks,"
            "(SELECT count(*) FROM work_sandbox_sessions WHERE owner_id=%s) AS sandboxes,"
            "(SELECT count(*) FROM operation_executions e JOIN work_queue_runs r ON r.id=e.run_id "
            "WHERE r.owner_id=%s) AS operations,"
            "(SELECT count(*) FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
            "WHERE c.owner_id=%s) AS ask_runs,"
            "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s) AS work_runs,"
            "(SELECT count(*) FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
            "WHERE w.owner_id=%s) AS workflow_runs",
            (controller.marker.owner_id,) * 6,
        )
        return {
            "state": controller.state,
            "runs": rows,
            "usage": usage,
            "limits": LIMITS,
            "counts": counts[0],
            "reconstructed": controller.reconstructed,
        }

    @app.post(BASE + "/close")
    async def close(request: Request):
        await controller.proof(request)
        controller.dispatch()
        rows = await app.state.db.fetch(
            "SELECT status FROM workflow_runs WHERE id=ANY(%s::uuid[])", (list(controller.state["roots"]),)
        )
        if len(rows) != 4 or any(row["status"] not in {"SUCCEEDED", "FAILED"} for row in rows):
            fail(409, "F07_UNSETTLED", "原运行尚未终态，不得重放")
        controller.dispatch()
        controller.state["phase"] = "CLOSED"
        controller.save()
        return {"closed": True, "unknown_preserved": controller.state["provider"]["status"] == "UNKNOWN"}

    return app
