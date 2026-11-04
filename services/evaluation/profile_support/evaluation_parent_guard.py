"""Target-only required parent marker and live boundary guards. Never imported by the production API."""

import asyncio
import json
import re
from functools import wraps
from importlib import import_module
from pathlib import Path
from uuid import UUID

from evaluation_platform_effects import configuration, require_permit
from fastapi.responses import JSONResponse
from psycopg.conninfo import conninfo_to_dict

from app.resource_runtime import current_scope

MARKER = "evaluation-parent.json"
CONTRACT = "platform-effect-permit-v1"
REQUIRED = {
    "schema_version",
    "contract",
    "parent_run_id",
    "parent_owner_id",
    "case_id",
    "candidate_commit",
    "environment_id",
    "owner_id",
}


class ParentGuard:
    def __init__(self, settings):
        self.settings = settings
        self.binding = None
        self.registered = set()
        path = Path(settings.data_dir) / MARKER
        if not path.exists():
            if configuration() is not None:
                raise ValueError("Parent delegation without its required persistent marker")
            return
        if path.is_symlink() or path.is_junction() or path.stat().st_size > 4096:
            raise ValueError("Invalid protected parent marker")
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            set(value) != REQUIRED
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["contract"] != CONTRACT
            or re.fullmatch(r"AET-[AWF][0-9]{2}", value["case_id"]) is None
            or value["candidate_commit"] != settings.evaluation_platform_commit
            or value["environment_id"] != settings.evaluation_instance_id
            or not settings.data_dir.name.endswith("_test")
            or not conninfo_to_dict(settings.database_url).get("dbname", "").endswith("_test")
        ):
            raise ValueError("Parent marker does not belong to this isolated target")
        for name in ("parent_run_id", "parent_owner_id", "environment_id", "owner_id"):
            if str(UUID(value[name])) != value[name]:
                raise ValueError("Canonical parent scope required")
        own = json.loads((Path(settings.data_dir) / "evaluation-instance.json").read_text(encoding="utf-8"))
        if own["owner_id"] != value["owner_id"] or own["environment_id"] != value["environment_id"]:
            raise ValueError("Parent marker conflicts with the actual target owner")
        # A persisted required marker never silently falls back to a standalone target if both env values were lost.
        if configuration() is None:
            raise ValueError("Required parent delegation was lost")
        self.binding = value

    @property
    def required(self):
        return self.binding is not None

    def check_sync(self, boundary, *, case_id=None):
        if not self.required:
            return
        scope = current_scope()
        if case_id is not None and case_id != self.binding["case_id"]:
            raise RuntimeError("Model case differs from its sealed parent task")
        if scope is not None and str(scope.owner) != self.binding["owner_id"]:
            raise RuntimeError("Effect owner differs from the isolated parent target")
        mode = (
            scope.mode if scope is not None else {"A": "ask", "W": "work", "F": "workflow"}[self.binding["case_id"][4]]
        )
        if configuration() is None:
            raise RuntimeError("Required parent delegation was lost")
        require_permit(self.binding["environment_id"], self.binding["case_id"], mode, boundary, required=True)

    async def check_async(self, boundary):
        await asyncio.to_thread(self.check_sync, boundary)

    def provider(self, case_id):
        self.check_sync("provider", case_id=case_id)

    def wrap_tools(self, tools, *, before_effect=None):
        if not self.required and before_effect is None:
            return tools
        for tool in tools:
            actual = getattr(tool, "coroutine", None)
            if not callable(actual):
                raise RuntimeError("Parent target refuses an unguarded native tool")  # noqa: TRY004 -- Missing async guards are a runtime capability failure.
            if getattr(actual, "_parent_guard", None) is self and getattr(actual, "_before_effect", None) is before_effect:
                continue

            def wrap(function):
                @wraps(function)
                async def guarded(*args, **kwargs):
                    if before_effect is not None:
                        before_effect()
                    await self.check_async("tool")
                    if before_effect is not None:
                        before_effect()
                    return await function(*args, **kwargs)

                guarded._parent_guard = self
                guarded._before_effect = before_effect
                return guarded

            tool.coroutine = wrap(actual)
        return tools

    def attach(self, app, *, before_effect=None):
        app.state.evaluation_parent_guard = self
        if not self.required and before_effect is None:
            return

        @app.middleware("http")
        async def parent_submission(request, call_next):
            safe = (
                request.method in {"GET", "HEAD", "OPTIONS"}
                or request.url.path in {"/api/v1/auth/login", "/api/v1/auth/logout"}
                or request.method == "POST"
                and re.fullmatch(r"/api/v1/(?:runs|work-runs)/[0-9a-f-]{36}/cancel", request.url.path)
            )
            if not safe and request.url.path.startswith("/api/v1/"):
                try:
                    if before_effect is not None:
                        before_effect()
                    await self.check_async("submission")
                    if before_effect is not None:
                        before_effect()
                except (RuntimeError, ValueError):
                    return JSONResponse(
                        {
                            "error": {
                                "code": "EVALUATION_PARENT_PERMISSION_UNAVAILABLE" if before_effect is None else "EVALUATION_F05_EFFECT_UNAVAILABLE",
                                "message": "评测父批次租约或当前权限不可用，未执行新操作" if before_effect is None else "评测授权或期限不可用，未执行新操作",
                            }
                        },
                        status_code=503,
                    )
            return await call_next(request)

        self.registered.add("submission")

    def install_runtime(self, application, state):
        if not self.required:
            return
        state.parent_effect_guard = self.provider
        application.state.db.evaluation_effect_guard = self.check_async
        application.state.db.evaluation_wrap_tools = self.wrap_tools
        if getattr(type(state), "parent_guard_contract", None) == CONTRACT:
            self.registered.add("provider")
        modules = {
            "tool": ("app.capabilities", "app.mcp_service", "app.work_engine", "app.workflow_runtime"),
            "sandbox": ("app.durable_sandboxes",),
        }
        for boundary, names in modules.items():
            if all(getattr(import_module(name), "EVALUATION_EFFECT_GUARD_VERSION", None) == CONTRACT for name in names):
                self.registered.add(boundary)

    async def capability(self):
        if not self.required:
            return None
        ready = self.registered == {"provider", "tool", "sandbox", "submission"}
        if ready:
            try:
                await self.check_async("submission")
            except (RuntimeError, ValueError):
                ready = False
        return {
            "required": True,
            "ready": ready,
            "contract": CONTRACT,
            "parent_run_id": self.binding["parent_run_id"],
            "case_id": self.binding["case_id"],
            "candidate_commit": self.binding["candidate_commit"],
            "boundaries": {key: key in self.registered for key in ("provider", "tool", "sandbox", "submission")},
        }
