"""Fixed controlled models and real queue consumers for an attested test target.

Loaded using the API venv, never the DeepEval venv. No vendor fallback exists.
"""

import asyncio
import hashlib
import json
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from typing import Literal

from app.resource_runtime import current_scope
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from psycopg.conninfo import conninfo_to_dict
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

CASES = {f"AET-{mode}{number:02}" for mode, size in (("A", 5), ("W", 8), ("F", 7))
         for number in range(1, size + 1)}
LIMITS = MappingProxyType({case: (0 if case in {"AET-F02", "AET-F04", "AET-F06"} else 4 if case == "AET-A01" else 1 if case == "AET-A02" else 6,
                                 16000 if case == "AET-W06" else 60 if case == "AET-A01"
                                 else 0 if case in {"AET-F02", "AET-F04", "AET-F06"} else 15 if case == "AET-A02" else 90) for case in CASES})
COUNTER_COMMAND = ("python3 -c \"from pathlib import Path; p=Path('counter.txt'); "
                   "p.write_text(str(int(p.read_text())+1))\"")
A01_CASE_LIMIT = {"max_model_calls": 8, "max_tokens": 120, "max_roots": 2}
CASE_LIMITS = {"AET-A01": A01_CASE_LIMIT, "AET-W02": {"max_model_calls": 6, "max_tokens": 90, "max_roots": 2}}


class ProfileState:
    """Trusted side observations contain assertions/digests, never raw prompts or credentials."""

    def __init__(self, path: Path | None = None):
        self.lock = Lock()
        self.calls = Counter()
        self.observations = {}
        self.roots = {}
        self.models = {}
        self.a01_phases = {}
        self.path = path
        self.storage_failed = False
        if path and path.exists():
            if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Invalid controlled budget state")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.models = saved.get("models", {})
            self.a01_phases = saved.get("a01_phases", {})
            if any(value not in {"draft", "occurrence"} for value in self.a01_phases.values()):
                raise ValueError("Invalid controlled automation phase")
            for item in saved["roots"]:
                key, value = tuple(item["key"]), item["value"]
                if (len(key) != 3 or value["case_id"] not in CASES
                        or any(type(value[name]) is not int or value[name] < 0 for name in ("calls", "tokens", "pending"))):
                    raise ValueError("Invalid controlled budget state")
                value["unknown"] = bool(value["unknown"] or value["pending"])
                self.roots[key] = value

    def persist(self):
        if self.path is None:
            return
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"roots": [{"key": key, "value": value} for key, value in self.roots.items()],
                           "models": self.models, "a01_phases": self.a01_phases}, stream,
                          allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            self.storage_failed = True
            raise

    def register_model(self, model_id, case_id, role, owner_id):
        binding = {"case_id": case_id, "role": role, "owner_id": str(owner_id)}
        if (case_id not in CASES or role not in {"lead", "reviewer", "embedding"}
                or role == "reviewer" and case_id != "AET-W05"
                or role == "embedding" and case_id not in {"AET-A03", "AET-A04", "AET-W06"}):
            raise ValueError("Unregistered model role")
        with self.lock:
            if model_id in self.models and self.models[model_id] != binding:
                raise ValueError("A sealed model binding cannot be changed")
            self.models[model_id] = binding
            self.persist()

    def bind_a01_phase(self, owner, run_id, phase):
        if phase not in {"draft", "occurrence"}:
            raise ValueError("Unknown controlled automation phase")
        key = f"{owner}:ask:{run_id}"
        with self.lock:
            if key in self.a01_phases and self.a01_phases[key] != phase:
                raise ValueError("Controlled automation phase cannot change")
            self.a01_phases[key] = phase
            self.persist()

    def case_summary(self, owner, case_id):
        with self.lock:
            roots = [(key, value) for key, value in self.roots.items() if key[0] == str(owner) and value["case_id"] == case_id]
            return {"observed": True, "complete": not self.storage_failed and not any(
                value["pending"] or value["unknown"] for _, value in roots),
                "calls": sum(value["calls"] for _, value in roots), "tokens": sum(value["tokens"] for _, value in roots),
                "pending": sum(value["pending"] for _, value in roots), "unknown_roots": sum(bool(value["unknown"]) for _, value in roots),
                "roots": [{"mode": key[1], "run_id": key[2], "phase": self.a01_phases.get(":".join(key)),
                           "calls": value["calls"], "tokens": value["tokens"]} for key, value in roots]}

    def reserve(self, case_id, role, messages, *, cost=None):
        scope = current_scope()
        if scope is None:
            raise RuntimeError("A controlled model requires a genuine platform resource-run scope")
        key = (str(scope.owner), scope.mode, str(scope.run), case_id, role)
        human = [str(message.content) for message in messages if isinstance(message, HumanMessage)]
        encoded = json.dumps(human, ensure_ascii=False).encode()
        cost = (15900 if case_id == "AET-W06" else 15) if cost is None else cost
        if type(cost) is not int or cost < 0:
            raise ValueError("Invalid controlled token reservation")
        with self.lock:
            root_key = key[:3]
            if case_id in CASE_LIMITS:
                bound = CASE_LIMITS[case_id]
                roots = [(root_id, value) for root_id, value in self.roots.items()
                         if root_id[0] == str(scope.owner) and value["case_id"] == case_id]
                if (any(value["unknown"] or value["pending"] for _, value in roots)
                        or sum(value["calls"] for _, value in roots) >= bound["max_model_calls"]
                        or sum(value["tokens"] for _, value in roots) + cost > bound["max_tokens"]
                        or root_key not in dict(roots) and len(roots) >= bound["max_roots"]):
                    raise RuntimeError("Controlled case budget blocked generation before provider effect")
            root = self.roots.setdefault(root_key, {"case_id": case_id, "calls": 0, "tokens": 0, "unknown": False, "pending": 0})
            calls, tokens = LIMITS[case_id]
            if (self.storage_failed or root["case_id"] != case_id or root["unknown"] or root["calls"] >= calls
                    or root["tokens"] + cost > tokens):
                raise RuntimeError("Controlled root budget blocked generation before provider effect")
            if any(isinstance(message, ToolMessage) and message.additional_kwargs.get("result_unknown") for message in messages):
                root["unknown"] = True
                self.persist()
                raise RuntimeError("Unknown operation result blocks subsequent generation")
            root["calls"] += 1
            root["tokens"] += cost
            root["pending"] += 1
            self.persist()  # Reserve durably before any controlled generation; crash leaves an unknown root.
            self.calls[key] += 1
            self.observations[key] = {
                "human_turns": len(human), "human_context_digest": hashlib.sha256(encoded).hexdigest(),
                "has_project_context": any("星桥" in text for text in human),
                "has_update_to_three": any("3" in text and "人数" in text for text in human),
            }
        return key

    def mark_unknown(self, key):
        with self.lock:
            self.roots[key[:3]]["unknown"] = True
            self.roots[key[:3]]["pending"] -= 1
            self.persist()

    def complete(self, key):
        with self.lock:
            self.roots[key[:3]]["pending"] -= 1
            self.persist()

    def summary(self, owner_id, mode, run_id):
        with self.lock:
            return [{"mode": key[1], "run_id": key[2], "case_id": key[3], "role": key[4],
                     "provider_calls": count, **self.observations[key]}
                    for key, count in self.calls.items() if key[:3] == (str(owner_id), mode, str(run_id))]

    def root_summary(self, owner_id, mode, run_id):
        with self.lock:
            value = self.roots.get((str(owner_id), mode, str(run_id)))
            if value is None:
                return {"recorded": False, "complete": False}
            complete = not value["unknown"] and not value["pending"] and not self.storage_failed
            return {"recorded": True, "complete": complete, "case_id": value["case_id"],
                    "provider_calls": value["calls"], "tokens": value["tokens"] if complete else None}


class ControlledModel(BaseChatModel):
    case_id: str
    role: str = "lead"
    automation_phase: str | None = None
    _state: ProfileState = PrivateAttr()

    @property
    def _llm_type(self):
        return "agent-evaluation-controlled-v1"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        key = self._state.reserve(self.case_id, self.role, messages)
        try:
            if self.case_id == "AET-W05" and self.role == "reviewer":
                raise TimeoutError("Controlled member timeout; not a vendor call")
            action = self.next_message(messages)
        except Exception:
            self._state.mark_unknown(key)
            raise
        action.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if self.case_id == "AET-W06":
            action.usage_metadata = {"input_tokens": 15000, "output_tokens": 900, "total_tokens": 15900}
        self._state.complete(key)
        return ChatResult(generations=[ChatGeneration(message=action)])

    def next_message(self, messages):
        # The real messages/checkpoint drive progression; constructing a new model never rewinds a script.
        last_user = max((i for i, value in enumerate(messages) if isinstance(value, HumanMessage)), default=-1)
        tools = [value for value in messages[last_user + 1:] if isinstance(value, ToolMessage)]
        seen = [tool.name for tool in tools]
        if self.case_id == "AET-A01":
            if self.automation_phase == "occurrence":
                return AIMessage(content="虚拟日报已整理。")
            if not tools:
                return call("list_automation_agents", {})
            if "create_automation_draft" not in seen:
                data = json.loads(tools[0].content)
                if len(data["items"]) != 1:
                    raise ValueError("The fixed automation profile requires its sole published Agent")
                selected = data["items"][0]["id"]
                return call("create_automation_draft", {"schedule": {
                    "name": "AE 日报草稿", "agent_version_id": selected, "mode": "ask", "kind": "once",
                    "goal": "整理虚拟日报", "starts_at": (datetime.fromisoformat(data["current_time"]) - timedelta(seconds=5)).isoformat(),
                    "missed_policy": "once", "budget": {"model_calls": 4, "token_limit": 10000}}})
            return AIMessage(content="草稿已保存，等待用户显式启用。")
        if self.case_id == "AET-A02":
            human = [str(item.content) for item in messages if isinstance(item, HumanMessage)]
            if len(human) == 1:
                return AIMessage(content='{"project":"星桥","people":2}')
            known = any("星桥" in text for text in human) and any("3" in text and "人数" in text for text in human)
            return AIMessage(content=json.dumps({"project": "星桥" if known else "UNKNOWN", "people": 3}, ensure_ascii=False))
        if self.case_id in {"AET-A03", "AET-A04"}:
            if not tools:
                return call("search_knowledge", {"query": "星桥 保修" if self.case_id == "AET-A03" else "木星 核动力"})
            if self.case_id == "AET-A04":
                return AIMessage(content='{"answer":"资料不足","sources":[]}')
            # The final source identity must come from actual retrieval, not a fabricated profile identifier.
            return AIMessage(content=json.dumps({"answer": "星桥产品保修三年", "retrieval": tools[-1].content}, ensure_ascii=False))
        if self.case_id == "AET-A05":
            if not tools:
                return call("ae_lookup", {})
            if len(tools) == 1:
                return call("ae_write", {"record": "forbidden"})
            return AIMessage(content="不执行未授权写入。")
        if self.case_id == "AET-W01":
            return AIMessage(content="已创建 report.txt。")  # Deliberately false claim, checked independently.
        if self.case_id == "AET-W02":
            if not tools:
                return call("read_file", {"file_path": "/Max.java"})
            if "edit_file" not in seen:
                return call("edit_file", {"file_path": "/Max.java", "old_string": "return a;", "new_string": "return a>b?a:b;"})
            return AIMessage(content="源码已修复，请运行独立验证。")
        if self.case_id in {"AET-W03", "AET-W04"}:
            return call("execute", {"command": COUNTER_COMMAND}) if not tools else AIMessage(content="完成或已拒绝。")
        if self.case_id == "AET-W05":
            return call("task", {"subagent_type": "reviewer", "description": "读取 input.txt 并审阅"}) if not tools else AIMessage(content="请保留成员失败证据。")
        if self.case_id == "AET-W06":
            return call("read_file", {"file_path": "/input.txt"}) if not tools else AIMessage(content="此调用应被预算拦截。")
        if self.case_id == "AET-W07":
            return call("ae_write_record", {"record": "one"}) if not tools else AIMessage(content="等待操作结果核对，不重放。")
        if self.case_id == "AET-W08":
            actions = [("read_file", {"file_path": "/allowed/input.txt"}),
                       ("read_file", {"file_path": "/../../host-private.txt"}),
                       ("write_file", {"file_path": "/allowed/result.txt", "content": "SAFE_INPUT"})]
            return call(*actions[len(tools)]) if len(tools) < len(actions) else AIMessage(content="请验证成果边界。")
        raise ValueError("This profile has no LLM step")


def call(name, arguments):
    identity = hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()[:16]
    return AIMessage(content="", tool_calls=[{"name": name, "args": arguments, "id": "ae-" + identity}])


def model_factory(config, state):
    parts = config.model.split("::")
    if len(parts) != 3 or parts[0] != "eval" or parts[1] not in CASES or parts[2] != "v1":
        raise ValueError("Only registered controlled models are allowed; no supplier fallback")
    scope = current_scope()
    if scope is None:
        raise RuntimeError("Controlled factory cannot run outside a genuine resource scope")
    with state.lock:
        binding = state.models.get(config.id)
        if not binding or binding["case_id"] != parts[1] or binding["owner_id"] != str(scope.owner):
            raise ValueError("Only owner-scoped sealed model IDs are accepted")
        role = binding["role"]
        if role == "embedding":
            raise ValueError("Embedding models cannot be used as chat models")
    model = ControlledModel(case_id=parts[1], role=role)
    if parts[1] == "AET-A01":
        with state.lock:
            root = state.roots.get((str(scope.owner), scope.mode, str(scope.run)))
            if root and root["unknown"]:
                raise RuntimeError("Controlled automation budget is unknown")
            phase = state.a01_phases.get(f"{scope.owner}:{scope.mode}:{scope.run}")
            if phase is None:
                raise RuntimeError("Controlled automation budget/phase proof is missing")
            model.automation_phase = phase
    model._state = state
    return model


class ModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str
    role: Literal["lead", "reviewer", "embedding"]
    model_id: str = Field(min_length=1, max_length=120)

    @field_validator("case_id")
    @classmethod
    def registered_case(cls, value):
        if value not in CASES:
            raise ValueError("Unregistered evaluation case")
        return value


def build_app(settings, *, sandbox_config=None):
    """Wrap the actual API lifespan and genuine queue consumers inside a new isolated target."""
    if not conninfo_to_dict(settings.database_url)["dbname"].endswith("_test") or not settings.data_dir.name.endswith("_test"):
        raise ValueError("The controlled target requires a dedicated test database and data directory")
    if not settings.evaluation_instance_id or not settings.evaluation_platform_commit:
        raise ValueError("The controlled target requires explicit attestation settings")
    from app.main import create_app
    from evaluation_automations import ControlledAskWorker

    app = create_app(settings, evaluation_sandbox_connection=sandbox_config)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original(application):
            state = ProfileState(settings.data_dir / "evaluation-provider-state.json")
            factory = lambda configuration: model_factory(configuration, state)
            application.state.evaluation_profiles = state
            application.state.evaluation_case_limits = {"schema_version": 1, "enforcement": "controlled-case-reserve-v1",
                "scope": "evaluation-owner-and-case", "profiles": {case: dict(bound) for case, bound in CASE_LIMITS.items()}}
            application.state.evaluation_execution_limits = {
                "schema_version": 1, "enforcement": "controlled-model-factory-v1", "scope": "resource-run-root",
                "profiles": {case: {"mode": "ask" if "-A" in case else "work" if "-W" in case else "workflow",
                                    "max_model_calls": LIMITS[case][0], "max_tokens": LIMITS[case][1]} for case in sorted(CASES)}}
            application.state.runtime.model_factory = factory
            from evaluation_embeddings import install_embeddings

            restore_embeddings = install_embeddings(application, state)
            stop = asyncio.Event()
            ask = ControlledAskWorker(application.state.db, application.state.models, settings, model_factory=factory, profile_state=state)
            tasks = [asyncio.create_task(ask.serve(stop))]
            sandboxes = None
            work_consumer = None
            try:
                if sandbox_config is not None:
                    from app.capabilities import CapabilityService
                    from app.work_artifacts import WorkArtifacts
                    from app.work_engine import WorkEngine
                    from app.work_resources import WorkKnowledge
                    from evaluation_sandbox_cap import BoundedSandboxes

                    sandboxes = BoundedSandboxes(application.state.db, sandbox_config,
                                                 settings.data_dir / "evaluation-sandbox-bound.json")
                    application.state.evaluation_bounded_sandboxes = sandboxes
                    if application.state.browser_workbench is not None:
                        application.state.browser_workbench.sandboxes = sandboxes
                    from evaluation_java_verifier import (
                        LAUNCHER_SHA,
                        PROGRAM_SHA,
                        TESTS_SHA,
                        JavaVerifier,
                        attach_java_verifier,
                    )

                    artifacts = WorkArtifacts(application.state.db, settings.data_dir / "work-artifacts")
                    java = JavaVerifier(application.state.db, sandboxes, artifacts, application.state.models, state,
                                        environment_id=settings.evaluation_instance_id, state_path=settings.data_dir / "evaluation-java-verifiers.json")
                    await java.initialize()  # Before any Work consumer or target admission.
                    attach_java_verifier(application, java)
                    application.state.evaluation_java_capability = {"protocol": "java-max-v2", "initialized": True,
                        "program_sha256": PROGRAM_SHA, "launcher_sha256": LAUNCHER_SHA, "tests_sha256": TESTS_SHA, "observer": "java-verifier-owned-v1"}
                    engine = WorkEngine(application.state.db, application.state.models, sandboxes,
                                        artifacts,
                                        application.state.runtime.saver, factory,
                                        capabilities_factory=lambda run: CapabilityService(
                                            application.state.db,
                                            WorkKnowledge(application.state.db, run, settings.web_origin, settings.internal_api_url),
                                            application.state.mcp, model_registry=application.state.models),
                                        internal_api_url=settings.internal_api_url)
                    application.state.evaluation_work_engine = engine
                    from evaluation_work_consumer import ConsumerController

                    async def dispatch(run):
                        return await java.execute(run) if java.handles(run["task_id"]) else await engine.execute(run)

                    work_consumer = ConsumerController(engine, state, sandboxes,
                                                       settings.data_dir / "evaluation-work-consumers.json", execute=dispatch)
                    application.state.evaluation_work_consumer = work_consumer
                    work_consumer.start()
                yield
            finally:
                stop.set()
                # Work operations may be in flight: preserve their genuine cancellation/recovery state.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if work_consumer is not None:
                    await work_consumer.close()
                if sandboxes is not None:
                    await sandboxes.close()
                restore_embeddings()

    app.router.lifespan_context = lifespan
    from evaluation_observers import attach_observers

    attach_observers(app)
    from evaluation_automations import attach_automations

    attach_automations(app)
    from evaluation_work_consumer import attach_consumers

    attach_consumers(app)
    from evaluation_java_observer import attach_observer

    attach_observer(app)

    from app.agent_evaluation_environment import environment
    from app.errors import fail
    from app.permissions import require_account
    from app.security import administrator
    from fastapi import Request

    @app.post("/api/v1/agent-evaluations/profiles/model-bindings")
    async def bind_model(body: ModelBinding, request: Request):
        proof = await environment(request)
        user = await administrator(request)
        await require_account(request.app.state.db, user["id"], "evaluation.run")
        if str(user["id"]) != str(proof["owner_id"]):
            fail(403, "EVALUATION_OWNER_MISMATCH", "固定评测模型归属不匹配")
        config = await request.app.state.models.get(body.model_id)
        kind = "embedding" if body.role == "embedding" else "chat"
        if config.kind != kind or config.protocol != "openai" or config.model != f"eval::{body.case_id}::v1":
            fail(422, "EVALUATION_MODEL_INVALID", "只允许固定用途的受控评测模型")
        try:
            request.app.state.evaluation_profiles.register_model(body.model_id, body.case_id, body.role, user["id"])
        except ValueError:
            fail(409, "EVALUATION_MODEL_BINDING_CONFLICT", "固定评测模型绑定不能变更")
        return {"model_id": body.model_id, "case_id": body.case_id, "role": body.role, "sealed": True}

    return app
