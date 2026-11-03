"""Run Deep Agents in a task-bound sandbox with durable human checkpoints."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from deepagents.backends.composite import CompositeBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from psycopg.types.json import Jsonb

from app.browser_bridge import BrowserBridge
from app.browser_tools import browser_tool
from app.capabilities import CapabilityService
from app.config import DEFAULT_INTERNAL_API_URL, ModelConfig, validate_internal_api_url
from app.durable_sandboxes import DurableSandboxes
from app.graphs import build_work
from app.models import ModelRegistry
from app.operation_journal import OperationJournal, receipts
from app.storage import Database
from app.usage import UsageRecorder
from app.web_search import search_tool
from app.work_artifacts import WorkArtifacts
from app.work_backend import WorkBackend
from app.work_budget import WorkBudget
from app.work_pause import WorkPauseMiddleware
from app.work_resources import WorkWorkflows
from app.work_worker import CapacityUnavailable, LeaseLost, WorkPaused, WorkWorker


class WorkEngine:
    """Connect one fenced run to a model, sandbox, checkpointer and artifacts."""

    def __init__(
        self,
        database: Database,
        models: ModelRegistry,
        sandboxes: DurableSandboxes,
        artifacts: WorkArtifacts,
        saver: BaseCheckpointSaver,
        model_factory: Callable[[ModelConfig], BaseChatModel],
        *,
        capabilities_factory: Callable[[dict], CapabilityService] | None = None,
        internal_api_url: str = DEFAULT_INTERNAL_API_URL,
    ) -> None:
        self.database, self.models, self.sandboxes = database, models, sandboxes
        self.artifacts, self.saver, self.model_factory = artifacts, saver, model_factory
        self.events = WorkWorker(database, self.execute)
        self.capabilities_factory = capabilities_factory
        self.internal_api_url = validate_internal_api_url(internal_api_url)

    async def model(self, run: dict) -> BaseChatModel:
        rows = await self.database.fetch("SELECT * FROM model_configs WHERE id=%s AND enabled", (run["model_id"],))
        if not rows or rows[0]["revision"] != run["model_revision"]:
            raise ValueError("Model configuration changed; create a new run")
        configuration = self.models.from_row(rows[0])
        configuration = configuration.model_copy(
            update={"max_output_tokens": min(configuration.max_output_tokens or 4096, 8192)}
        )
        model = self.model_factory(configuration)
        if hasattr(model, "stream_usage"):
            model.stream_usage = True
        if hasattr(model, "max_retries"):
            model.max_retries = 0
        return model

    async def execute(self, run: dict) -> dict:
        from app.automation_auth import authorize
        from app.workflow_work import WorkWorkflow, binding

        await authorize(self.database, run["owner_id"], run["id"], "work")
        workflow_binding = await binding(self.database, run)
        from app.repository_runs import binding as repository_binding

        repository = await repository_binding(self.database, run)
        if repository:
            run = {
                **run,
                "instructions": run["instructions"]
                + "\n代码位于 /repo（命令目录 /workspace/repo）。"
                + "来源GitHub "
                + repository["source"]["slug"]
                + "，固定提交 "
                + repository["source"]["sha"]
                + "；本地任务分支 "
                + repository["branch"]
                + "。初始提交为源码快照，未载入上游历史。"
                + "交付文件请放在 /outputs。远端写入只能由用户在差异审阅后单独批准，环境中没有连接凭证。",
            }
        if not workflow_binding and await self.database.fetch(
            "SELECT 1 FROM work_task_workflows WHERE task_id=%s", (run["task_id"],)
        ):
            from app.errors import fail

            fail(409, "WORKFLOW_BINDING_MISSING", "固定流程绑定已不可用，不能改为普通智能体执行")
        if workflow_binding and self.capabilities_factory is None:
            from app.errors import fail

            fail(503, "WORKFLOW_NOT_READY", "任务后台未配置工作流资源服务")
        if run["agent_snapshot"].get("team_pins") and self.capabilities_factory is None:
            from app.errors import fail

            fail(503, "TEAM_NOT_READY", "当前任务执行端尚未配置团队资源服务")
        model = await self.model(run)
        created = await self.sandboxes.create(run["id"], run["lease_token"], run["environment"])
        if created is None:
            raise CapacityUnavailable()
        session, sandbox = created
        closing = False

        async def guard():
            from app.automation_auth import authorize

            if not closing:
                await authorize(self.database, run["owner_id"], run["id"], "work")
                if repository:
                    await repository_binding(self.database, run)
            rows = await self.database.fetch(
                "SELECT cancel_requested FROM work_queue_runs WHERE id=%s AND status='RUNNING' "
                "AND lease_token=%s AND lease_until>now()",
                (run["id"], run["lease_token"]),
            )
            if not rows:
                raise LeaseLost("Work lease lost")
            if rows[0]["cancel_requested"] and not closing:
                raise RuntimeError("Work cancellation requested")
            snapshot = run["agent_snapshot"]
            if snapshot.get("id") and not closing:
                agents = await self.database.fetch(
                    "SELECT 1 FROM agents WHERE id=%s AND owner_id=%s AND enabled", (snapshot["id"], run["owner_id"])
                )
                if not agents:
                    raise RuntimeError("Agent permission revoked")

        backend = WorkBackend(
            sandbox,
            guard,
            journal=OperationJournal(self.database, run),
            repository_paths=([f["path"] for f in repository["source"]["files"]] if repository else None),
        )
        exported = False
        released = False
        keep_browser = False
        browser = None
        browser_saved = False
        try:
            await self.database.execute(
                "UPDATE work_sandbox_sessions SET retention_required=true WHERE id=%s",
                (session["id"],),
            )
            await backend.initialize()
            if run["environment"] == "browser":
                browser = BrowserBridge(self.database, self.models.vault, backend, run, session)
            if not session.get("reused"):
                await self.artifacts.restore(run, backend)
                await self.artifacts.seed_inputs(run, backend)
            await self.consume_approvals(run)
            workflow = WorkWorkflow(self, run, workflow_binding, backend, guard) if workflow_binding else None
            graph = await workflow.graph() if workflow else await self.agent_graph(run, model, backend, browser, guard)
            config = {
                "configurable": {"thread_id": "work:" + str(run["id"])},
                "recursion_limit": run["step_limit"] * 12 + 20,
                "callbacks": [
                    WorkBudget(self.database, run),
                    UsageRecorder(
                        self.database,
                        run["owner_id"],
                        run["id"],
                        run["model_id"],
                        "work",
                        route_selection_id=(run.get("agent_snapshot") or {}).get("route_selection_id"),
                        revision=run["model_revision"],
                        reserve=getattr(model, "max_tokens", None) or 4096,
                        lease_token=run["lease_token"],
                        team_budget=(
                            bool(run["agent_snapshot"].get("team_pins"))
                            or run.get("origin", "interactive") != "interactive"
                        ),
                    ),
                ],
            }
            argument = (
                Command(resume=run["resume_decision"])
                if run["resume_decision"]
                else {
                    "input": workflow_binding["input"],
                    "values": {},
                    "output": "",
                    "route": False,
                    "failed": False,
                    "last_output": "",
                }
                if workflow
                else {"messages": [{"role": "user", "content": run["instructions"] + "\n任务目标：" + run["goal"]}]}
            )
            if workflow:
                config["callbacks"] = []
                config["recursion_limit"] = workflow.definition.node_budget * 2 + 4
            interruptions = []
            async for mode, update in graph.astream(argument, config, stream_mode=["messages", "updates"]):
                await guard()
                if mode == "messages":
                    message, metadata = update
                    if metadata.get("platform_role"):
                        continue
                    if message.type == "AIMessageChunk" and message.text:
                        await self.events.emit(run, "message.delta", {"text": message.text[:2000]})
                else:
                    interruptions.extend(update.get("__interrupt__", []))
                    reported = [name for name in update if not name.startswith("__work_pause__")]
                    if reported:
                        await self.events.emit(run, "step.updated", {"nodes": reported})
                    for item in update.values():
                        if not isinstance(item, dict):
                            continue
                        if "todos" in item:
                            await self.events.emit(run, "plan.updated", {"todos": item["todos"]})
                        for message in item.get("messages", []):
                            if isinstance(message, ToolMessage):
                                output = str(message.content)
                                await self.events.emit(
                                    run,
                                    "tool.completed",
                                    {
                                        "name": message.name,
                                        "call_id": message.tool_call_id,
                                        "output": output[:8000],
                                        "truncated": len(output) > 8000,
                                    },
                                )
                            elif isinstance(message, AIMessage):
                                for call in message.tool_calls:
                                    await self.events.emit(
                                        run, "tool.requested", {"name": call["name"], "call_id": call["id"]}
                                    )
            state = await graph.aget_state(config)
            if not interruptions:
                interruptions = [item for task in state.tasks for item in task.interrupts]
            if browser:
                await browser.save()
                browser_saved = True
            snapshot = await self.artifacts.capture(run, backend)
            exported = True
            await self.database.execute(
                "UPDATE work_sandbox_sessions SET retention_required=false WHERE id=%s",
                (session["id"],),
            )
            if interruptions:
                # Keep the lease alive until sandbox cleanup finishes. Otherwise the
                # heartbeat can cancel cleanup after the approval state is saved.
                if browser and browser.started:
                    await self.database.execute(
                        "UPDATE work_sandbox_sessions SET browser_persistent=true,retention_required=true,"
                        "updated_at=now() "
                        "WHERE id=%s AND state='READY'",
                        (session["id"],),
                    )
                    keep_browser = True
                else:
                    await self.sandboxes.release(session["id"])
                    released = True
                await self.pause(run, interruptions)
                raise WorkPaused()
            messages = state.values.get("messages", [])
            output = (
                state.values.get("output", "")
                if workflow
                else next((message.text for message in reversed(messages) if isinstance(message, AIMessage)), "")
            )
            if workflow:
                await workflow.validate_result(output)
            evidence = await self.database.fetch(
                "SELECT count(*) FILTER(WHERE name='execute' AND status='SUCCEEDED') AS successful_commands,"
                "count(*) FILTER(WHERE name='workflow_code' AND status='SUCCEEDED') AS successful_code_nodes,"
                "count(*) FILTER(WHERE status='UNKNOWN') AS uncertain_operations "
                "FROM operation_executions WHERE run_id=%s",
                (run["id"],),
            )
            return {
                "text": output[:100000],
                "snapshot_id": str(snapshot),
                "todos": state.values.get("todos", []),
                "verification": evidence[0],
            }

        finally:
            if browser and not browser_saved:
                closing = True
                try:
                    await browser.save()
                    browser_saved = True
                except Exception:
                    await browser.review()
            if not exported:
                closing = True
                try:
                    await self.artifacts.capture(run, backend)
                    if not browser or browser_saved:
                        await self.database.execute(
                            "UPDATE work_sandbox_sessions SET retention_required=false WHERE id=%s",
                            (session["id"],),
                        )
                    exported = True
                except Exception:
                    # A failed export must retain the sandbox and its durable slot.
                    pass
            try:
                if exported and not released and not keep_browser and (not browser or browser_saved):
                    await self.sandboxes.release(session["id"])
            finally:
                if browser:
                    await browser.closed()
                # Failed export retains the sandbox reservation and its only file copy.
                await sandbox.close()

    async def pause(self, run: dict, interruptions: list) -> None:
        """Save approval payloads and relinquish the worker lease in one transaction."""
        pause_resume = {item.id: True for item in interruptions if item.value == {"kind": "work_pause"}}
        async with self.database.pool.connection() as connection:
            cursor = await connection.execute(
                "UPDATE work_queue_runs SET status=CASE WHEN pause_requested OR %s "
                "THEN 'PAUSED' ELSE 'WAITING_APPROVAL' END,"
                "lease_token=NULL,lease_until=NULL, "
                "resume_decision=%s,updated_at=now() WHERE id=%s AND status='RUNNING' "
                "AND lease_token=%s AND lease_until>now() RETURNING id",
                (bool(pause_resume), Jsonb(pause_resume) if pause_resume else None, run["id"], run["lease_token"]),
            )
            if not await cursor.fetchone():
                raise LeaseLost("Approval checkpoint rejected for stale worker")
            for interruption in interruptions:
                payload = interruption.value
                if payload == {"kind": "work_pause"}:
                    continue
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                await connection.execute(
                    "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,expires_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (
                        uuid4(),
                        run["id"],
                        run["owner_id"],
                        interruption.id,
                        Jsonb(payload),
                        digest,
                        datetime.now(UTC) + timedelta(minutes=30),
                    ),
                )

    async def consume_approvals(self, run: dict) -> None:
        """Consume the exact saved decisions once, immediately before graph resumption."""
        if not run["resume_decision"] or all(value is True for value in run["resume_decision"].values()):
            return
        async with self.database.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM work_approvals WHERE run_id=%s AND consumed_at IS NULL FOR UPDATE",
                (run["id"],),
            )
            rows = await cursor.fetchall()
            if not rows or any(not row["decision"] or row["expires_at"] <= datetime.now(UTC) for row in rows):
                raise ValueError("Approval expired or already consumed")
            for row in rows:
                if row["decision"] in {"approve", "edit"}:
                    await receipts(
                        connection,
                        row,
                        row["edited_actions"] if row["decision"] == "edit" else row["payload"]["action_requests"],
                    )
            await connection.execute(
                "UPDATE work_approvals SET consumed_at=now() WHERE run_id=%s AND consumed_at IS NULL",
                (run["id"],),
            )

    async def agent_graph(self, run: dict, model, backend, browser, guard):
        """Keep the existing Deep Agents path independent of fixed workflow graphs."""
        graph_backend, tools, middleware = await self.context(run, backend, browser_bridge=browser)
        if run["agent_snapshot"].get("team_pins"):
            from app.team_runtime import TeamRun

            team = TeamRun(
                self.database,
                self.models,
                self.capabilities_factory(run),
                self.model_factory,
                run["id"],
                run["owner_id"],
                "work",
                run["lease_token"],
                run["agent_snapshot"],
                guard,
            )
            middleware.extend(await team.middleware(workspace=backend))
        return build_work(
            model,
            self.saver,
            backend=graph_backend,
            tools=tools,
            middleware=[WorkPauseMiddleware(self.database, run), *middleware],
            instructions=run["instructions"]
            + (
                "\n本次从历史成果的文件副本重新开始；先核对已有成果，避免重复操作。"
                if run["restore_snapshot_id"]
                else ""
            )
            + ("\n此次任务用户授权的公网域名：" + ", ".join(run["network_domains"]) if run["network_domains"] else ""),
        )

    async def context(self, run: dict, backend: WorkBackend, *, browser_bridge=None) -> tuple:
        """Mount selected Skills read-only and load currently authorized tools."""
        browser = [browser_tool(backend, run, bridge=browser_bridge)] if run["environment"] == "browser" else []
        if self.capabilities_factory is None:
            return backend, browser, []
        capabilities = self.capabilities_factory(run)
        snapshot = run["agent_snapshot"] or None
        tools, _ = await capabilities.for_owner(run["owner_id"], snapshot, work_run=run)
        tools.extend(browser)
        selection = snapshot or await capabilities.bindings(run["owner_id"])
        if selection.get("web_enabled"):
            if selection.get("web_mode") == "openai_native":
                tools.append({"type": "web_search"})
            else:
                from uuid import UUID

                tools.append(search_tool(self.database, self.models.vault, run["owner_id"], UUID(selection["id"])))
        if selection.get("workflow_version_ids"):
            from app.config import Settings

            tools.append(WorkWorkflows(run, Settings.from_env().web_origin, self.internal_api_url).tool())
        if not selection.get("skill_ids"):
            return backend, tools, []
        rows = await self.database.fetch(
            "SELECT name,files FROM skills WHERE id=ANY(%s::uuid[]) AND owner_id=%s AND enabled",
            (selection["skill_ids"], run["owner_id"]),
        )
        files = {f"/{row['name']}/{path}": text for row in rows for path, text in row["files"].items()}
        skills = await capabilities.skill_backend(run["owner_id"], selection, files)
        composite = CompositeBackend(default=backend, routes={"/skills/": skills})

        async def copy_skill_resource(file_path: str, target_path: str) -> str:
            """复制 /skills/ 下已授权的技能文本资源到工作区；脚本须随后通过 execute 审批才能在任务环境运行。"""
            if not file_path.startswith("/skills/"):
                raise ValueError("Source must be an authorized skill resource")
            resource = (await composite.adownload_files([file_path]))[0]
            if resource.error or resource.content is None:
                raise ValueError("Skill resource is unavailable")
            result = (await backend.aupload_files([(target_path, resource.content)]))[0]
            if result.error:
                raise ValueError("Cannot copy skill resource to workspace")
            return "技能资源已复制到 " + target_path + "；执行脚本仍需命令审批。"

        tools.append(StructuredTool.from_function(coroutine=copy_skill_resource))
        return composite, tools, [SkillsMiddleware(backend=composite, sources=["/skills/"])]
