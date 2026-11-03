"""Persistent top-level approvals and immutable Python files inside an owned Work sandbox."""

import asyncio
import hashlib
import shlex

from langgraph.types import interrupt

from app.errors import APIError, fail
from app.operation_journal import OperationJournal
from app.permissions import require_account
from app.work_pause import WorkPauseMiddleware
from app.workflow_compiler import compile_workflow
from app.workflow_definition import Definition
from app.workflow_runtime import WorkflowRuntime
from app.workflow_schema import validate_text


class WorkflowStopped(APIError):
    """A known rejected approval or returned nonzero command, rather than an unknown effect."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(422, code, message)


class WorkWorkflow:
    def __init__(self, engine, run: dict, binding: dict, backend, guard) -> None:
        self.engine, self.run, self.binding, self.backend, self.guard = engine, run, binding, backend, guard
        self.database = engine.database
        self.snapshot = binding["snapshot"]
        self.definition = Definition.model_validate(self.snapshot["definition"])
        self.runtime = WorkflowRuntime(
            self.database, engine.models, engine.capabilities_factory(run), engine.model_factory
        )
        self.journal = OperationJournal(self.database, run)
        self.pause = WorkPauseMiddleware(self.database, run)
        self.execution = {"remaining": self.definition.node_budget, "parallel": asyncio.Semaphore(2)}

    async def authority(self) -> None:
        await self.guard()
        await require_account(self.database, self.run["owner_id"], "workflows.execute")
        rows = await self.database.fetch(
            "SELECT 1 FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
            "WHERE r.id=%s AND w.owner_id=%s AND r.status='RUNNING'",
            (self.binding["workflow_run_id"], self.run["owner_id"]),
        )
        if not rows:
            fail(409, "WORKFLOW_REVOKED", "流程运行已终止或不再属于当前账号")
        if self.binding["version_id"]:
            await self.runtime.published(str(self.binding["version_id"]), self.run["owner_id"])
        current = await self.runtime.pins(
            self.definition, self.run["owner_id"], self.snapshot["model_id"], allow_work=True
        )
        if current != self.snapshot["resource_pins"]:
            fail(409, "WORKFLOW_RESOURCE_CHANGED", "模型或资源已变化，本任务停止，请核对后创建新运行")

    async def observe(self, event: dict) -> None:
        from psycopg.types.json import Jsonb

        await self.authority()
        if event["event"] == "node.started":
            seen = await self.database.fetch(
                "SELECT 1 FROM workflow_node_events WHERE run_id=%s AND node_id=%s AND event='node.started' LIMIT 1",
                (self.binding["workflow_run_id"], event["node_id"]),
            )
            if not seen:
                if self.execution["remaining"] <= 0:
                    fail(422, "WORKFLOW_NODE_BUDGET_EXCEEDED", "持久流程根节点预算已用完")
                self.execution["remaining"] -= 1
        await self.database.execute(
            "INSERT INTO workflow_node_events(run_id,node_id,event,data) VALUES(%s,%s,%s,%s)",
            (self.binding["workflow_run_id"], event["node_id"], event["event"], Jsonb(event)),
        )
        await self.engine.events.emit(self.run, "workflow.node", {
            "node_id": event["node_id"], "event": event["event"], "kind": event["kind"],
            "node_name": next((node.name for node in self.definition.nodes if node.id == event["node_id"]), None),
            **({"error_code": event["error_code"]} if event.get("error_code") else {}),
        })

    async def approve(self, name: str, arguments: dict, description: str) -> None:
        answer = interrupt({"action_requests": [{"name": name, "args": arguments, "description": description}],
                            "review_configs": [{"action_name": name, "allowed_decisions": ["approve", "reject"]}]})
        await self.authority()
        decisions = answer.get("decisions", []) if isinstance(answer, dict) else []
        if len(decisions) != 1 or decisions[0].get("type") != "approve":
            raise WorkflowStopped("WORKFLOW_APPROVAL_REJECTED", "用户未批准此流程操作，已停止后续节点")

    async def execute(self, node, value: str, values: dict) -> str:
        await self.authority()
        if node.kind in {"approval", "code"}:
            arguments = {"node_id": node.id, "input_digest": hashlib.sha256(value.encode()).hexdigest(),
                         "input_preview": value[:2000], "input_characters": len(value)}
            if node.kind == "approval":
                arguments["prompt"] = node.parameters["prompt"]
                await self.approve("workflow_approval", arguments, "确认此节点后才继续流程")
                receipt = await self.journal.start("workflow_approval", arguments)
                await self.journal.finish(receipt, 0, "用户已确认流程节点")
                return value
            code, timeout = node.parameters["code"], node.parameters.get("timeout", 30)
            arguments.update({"code": code, "code_digest": hashlib.sha256(code.encode()).hexdigest(),
                              "timeout": timeout})
            await self.approve("workflow_code", arguments, "在任务环境执行所示固定Python代码；不允许修改后批准")
            # Restore/rewrite the exact approved bytes after approval. Old workspace files are not trusted.
            script, input_path = f"/workflow-{node.id}.py", f"/workflow-{node.id}.input.txt"
            for path, raw in ((script, code.encode()), (input_path, value.encode())):
                saved = (await self.backend.aupload_files([(path, raw)]))[0]
                if saved.error:
                    raise WorkflowStopped("WORKFLOW_CODE_FILE_UNSAFE", "代码或输入路径无法安全写入，未执行")
            command = shlex.join(["python3", "/workspace" + script]) + " < " + shlex.quote("/workspace" + input_path)
            result = await self.backend.aexecute_workflow(command, seconds=timeout, arguments=arguments)
            if result.exit_code != 0:
                raise WorkflowStopped("WORKFLOW_CODE_FAILED", f"流程代码返回退出码{result.exit_code}，已停止")
            if result.truncated or len(result.output) > 40000:
                raise WorkflowStopped("WORKFLOW_CODE_OUTPUT_LIMIT", "流程代码输出超出40000字符或已截断，未继续")
            return result.output
        context = {"id": self.run["id"], "mode": "work", "lease_token": self.run["lease_token"],
                   "guard": self.authority}
        if node.kind in {"subworkflow", "iteration", "parallel", "loop"}:
            return await self.runtime.nested(node, value, self.run["owner_id"], self.binding["workflow_run_id"],
                self.snapshot["resource_pins"], context, self.execution, "", self.authority, [])
        return await self.runtime.execute(
            node, value, values, self.run["owner_id"], self.snapshot["model_id"], self.snapshot["instructions"],
            self.binding["workflow_run_id"], expected_pins=self.snapshot["resource_pins"], usage_context=context,
        )

    async def graph(self):
        await self.authority()
        used = (await self.database.fetch(
            "SELECT count(DISTINCT node_id) AS count FROM workflow_node_events "
            "WHERE run_id=%s AND event='node.started'",
            (self.binding["workflow_run_id"],),
        ))[0]["count"]
        self.execution["remaining"] = self.definition.node_budget - used
        async def before_node(node):
            await self.authority()
            await self.pause.check()

        return compile_workflow(
            self.definition, self.execute, self.observe, checkpointer=self.engine.saver, before_node=before_node
        )

    async def validate_result(self, output: str) -> None:
        await self.authority()
        validate_text(output, self.definition.output_schema, output=True)


async def binding(database, run: dict) -> dict | None:
    rows = await database.fetch(
        "SELECT w.workflow_run_id,w.version_id,f.snapshot,f.input FROM work_run_workflows w "
        "JOIN workflow_runs f ON f.id=w.workflow_run_id JOIN work_queue_runs r ON r.id=w.run_id "
        "WHERE r.id=%s AND r.owner_id=%s AND r.lease_token=%s AND r.lease_until>now() AND r.status='RUNNING'",
        (run["id"], run["owner_id"], run["lease_token"]),
    )
    return rows[0] if rows else None
