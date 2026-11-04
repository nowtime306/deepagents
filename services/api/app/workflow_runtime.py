"""Resource authorization and executable nodes for owner-scoped text workflows."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from uuid import UUID

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.types.json import Jsonb

from app.capabilities import CapabilityService
from app.config import ModelConfig
from app.errors import fail
from app.graphs import build_ask
from app.model_catalog import supports_chat
from app.models import ModelRegistry
from app.safe_http import destination, fetch_public_text
from app.storage import Database
from app.usage import UsageRecorder
from app.web_search import ensure_available, search_tool
from app.workflow_compiler import compile_workflow, condition
from app.workflow_definition import Definition, Node, render_template, validate
from app.workflow_schema import parse_json, validate_text


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def transform(node: Node, text: str) -> str:
    operation = node.parameters["operation"]
    if operation == "upper":
        return text.upper()
    if operation == "lower":
        return text.lower()
    if operation == "trim":
        return text.strip()
    if operation == "replace":
        old, new = node.parameters.get("old", ""), node.parameters.get("new", "")
        if not old or len(text) + text.count(old) * (len(new) - len(old)) > 40000:
            raise ValueError("Text replacement exceeds limits")
        return text.replace(old, new)
    if operation == "json_get":
        value = parse_json(text)
        keys = node.parameters.get("path", "").split(".")
        if len(keys) > 20:
            raise ValueError("JSON path exceeds limits")
        for key in keys:
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text

EVALUATION_EFFECT_GUARD_VERSION = 'platform-effect-permit-v1'


class WorkflowRuntime:
    """Use the same models, retrieval and read-only MCP policies as the rest of the platform."""

    def __init__(
        self,
        database: Database,
        models: ModelRegistry,
        capabilities: CapabilityService,
        model_factory: Callable[[ModelConfig], BaseChatModel],
    ) -> None:
        self.database, self.models, self.capabilities, self.model_factory = (
            database,
            models,
            capabilities,
            model_factory,
        )

    async def model_pin(self, identifier: str, pins: dict) -> None:
        rows = await self.database.fetch(
            "SELECT revision,enabled,kind,protocol FROM model_configs WHERE id=%s", (identifier,)
        )
        if not rows or not rows[0]["enabled"] or not supports_chat(rows[0]["kind"], rows[0]["protocol"]):
            fail(422, "WORKFLOW_MODEL_UNAVAILABLE", "工作流引用的聊天模型不存在或已停用")
        pins["models"][identifier] = rows[0]["revision"]

    async def agent(self, identifier: str, owner: UUID) -> dict:
        rows = await self.database.fetch(
            "SELECT id,name,instructions,model_id,revision,knowledge_ids,skill_ids,mcp_ids,"
            "web_enabled,web_mode,memory_enabled,model_routing "
            "FROM agents WHERE id=%s AND owner_id=%s AND enabled",
            (identifier, owner),
        )
        if not rows:
            fail(403, "WORKFLOW_AGENT_UNAVAILABLE", "工作流智能体不存在、已停用或无权访问")
        from app.resource_runtime import freeze_resource

        agent = json.loads(json.dumps(rows[0], default=str))
        return await freeze_resource("agent", identifier, agent["revision"], agent, vault=self.models.vault,
                                     snapshot={"id": identifier, "revision": agent["revision"]})

    async def server(self, identifier: str, pins: dict) -> dict:
        rows = await self.database.fetch("SELECT * FROM mcp_servers WHERE id=%s AND enabled", (identifier,))
        if not rows:
            fail(403, "WORKFLOW_MCP_UNAVAILABLE", "MCP 服务不存在或已停用")
        server = rows[0]
        pins["mcp"][identifier] = digest(
            {key: server[key] for key in ("url", "token_ciphertext", "tools", "allow_private")}
        )
        return server

    async def knowledge_pin(self, identifier: str, owner: UUID, pins: dict) -> None:
        knowledge = await self.capabilities.knowledge.owned(UUID(identifier), owner)
        pins["knowledge"][identifier] = digest(
            {key: value for key, value in knowledge.items() if "model" in key or "dimension" in key or "config" in key}
        )
        for key in ("embedding_model_id", "rerank_model_id"):
            model_id = knowledge.get(key)
            if model_id:
                rows = await self.database.fetch(
                    "SELECT revision FROM model_configs WHERE id=%s AND enabled", (model_id,)
                )
                if not rows:
                    fail(403, "WORKFLOW_RETRIEVAL_MODEL_UNAVAILABLE", "知识库引用的模型不存在或已停用")
                pins["retrieval_models"][model_id] = rows[0]["revision"]

    async def published(self, identifier: str, owner: UUID) -> dict:
        rows = await self.database.fetch(
            "SELECT v.* FROM workflow_versions v JOIN workflows w ON w.id=v.workflow_id "
            "WHERE v.id=%s AND w.owner_id=%s AND v.active",
            (identifier, owner),
        )
        if not rows:
            fail(403, "WORKFLOW_VERSION_UNAVAILABLE", "子流程版本已下线、删除或无权访问")
        return rows[0]

    async def pins(
        self, definition: Definition, owner: UUID, default_model: str, *, path: tuple = (), allow_work: bool = False
    ) -> dict:
        validate(definition)
        if definition.execution == "work" and not allow_work:
            fail(422, "WORKFLOW_WORK_REQUIRED", "此工作流需要持久任务与人工审批，不能通过只读或短流程入口执行")
        pins = {key: {} for key in ("models", "retrieval_models", "agents", "skills", "mcp", "knowledge")}
        await self.model_pin(default_model, pins)
        nested = [node for node in definition.nodes if node.kind in {"subworkflow", "iteration", "parallel", "loop"}]
        if nested:
            pins["workflows"] = {}
        if nested and len(path) >= 2:
            fail(422, "WORKFLOW_NESTING_LIMIT", "子流程最多嵌套两层")
        for node in definition.nodes:
            values = node.parameters
            if node.kind in {"subworkflow", "iteration", "parallel", "loop"}:
                identifiers = values["version_ids"] if node.kind == "parallel" else [values["version_id"]]
                for identifier in identifiers:
                    if identifier in path:
                        fail(422, "WORKFLOW_RECURSION_DENIED", "不能递归调用子流程")
                    published = await self.published(identifier, owner)
                    child = Definition.model_validate(published["definition"])
                    child_pins = await self.pins(child, owner, published["model_id"], path=(*path, identifier))
                    pins["workflows"][identifier] = {"digest": digest(published["definition"]), "pins": child_pins}
            elif node.kind == "llm":
                await self.model_pin(values.get("model_id") or default_model, pins)
                if values.get("model_routing"):
                    from app.model_routing import pin

                    pins.setdefault("routes", {})[node.id] = await pin(
                        self.database, owner, values.get("model_id") or default_model
                    )
            elif node.kind == "rag":
                await self.knowledge_pin(values["knowledge_id"], owner, pins)
            elif node.kind == "http":
                destination(values["url"])
            elif node.kind == "mcp":
                bindings = await self.capabilities.bindings(owner)
                if values["server_id"] not in bindings["mcp_ids"]:
                    fail(403, "WORKFLOW_MCP_UNBOUND", "请先在能力中心绑定此 MCP 服务")
                server = await self.server(values["server_id"], pins)
                if not any(
                    tool["name"] == values["tool"] and tool["enabled"] and tool["read_only"] for tool in server["tools"]
                ):
                    fail(403, "WORKFLOW_TOOL_DENIED", "工作流仅允许已启用的只读 MCP 工具")
            elif node.kind == "agent":
                agent = await self.agent(values["agent_id"], owner)
                await self.capabilities.validate(owner, agent)
                pins["agents"][values["agent_id"]] = agent["revision"]
                if agent.get("model_routing"):
                    from app.model_routing import pin

                    pins.setdefault("routes", {})[node.id] = await pin(self.database, owner, agent["model_id"])
                await self.model_pin(agent["model_id"], pins)
                for identifier in agent["knowledge_ids"]:
                    await self.knowledge_pin(identifier, owner, pins)
                for identifier in agent["skill_ids"]:
                    skill = await self.database.fetch(
                        "SELECT version FROM skills WHERE id=%s AND owner_id=%s AND enabled", (identifier, owner)
                    )
                    if not skill:
                        fail(403, "WORKFLOW_SKILL_UNAVAILABLE", "技能已停用或无权访问")
                    pins["skills"][identifier] = skill[0]["version"]
                for identifier in agent["mcp_ids"]:
                    await self.server(identifier, pins)
        return pins

    async def run(
        self,
        definition: Definition,
        owner: UUID,
        default_model: str,
        instructions: str,
        text: str,
        run_id: UUID,
        expected_pins: dict,
        *,
        guard=None,
        callbacks: list | None = None,
        usage_context: dict | None = None,
        execution: dict | None = None,
        prefix: str = "",
    ) -> str:
        from app.resource_runtime import resource_run

        resource_context = usage_context or {"mode": "workflow", "id": run_id}
        async with resource_run(self.database, owner, resource_context["mode"], resource_context["id"]):
            if definition.execution != "readonly":
                fail(422, "WORKFLOW_WORK_REQUIRED", "任务工作流必须使用持久任务入口")
            validate_text(text, definition.input_schema)
            context = usage_context or {"mode": "workflow", "id": run_id, "lease_token": None}
            execution = (
                execution
                if execution is not None
                else {"remaining": definition.node_budget, "parallel": asyncio.Semaphore(2)}
            )

            async def authority():
                from app.permissions import require_account

                await require_account(self.database, owner, "workflows.execute")
                if guard:
                    await guard()
                await self.pins(definition, owner, default_model)
                parent_guard = getattr(self.database, 'evaluation_effect_guard', None)
                if parent_guard is not None:
                    await parent_guard('tool')

            context = {**context, "guard": authority}

            async def observe(event):
                if event["event"] == "node.started":
                    await authority()
                    if execution["remaining"] <= 0:
                        fail(422, "WORKFLOW_NODE_BUDGET_EXCEEDED", "根流程节点执行预算已用完")
                    execution["remaining"] -= 1
                event = {**event, "node_id": prefix + event["node_id"]}
                await self.database.execute(
                    "INSERT INTO workflow_node_events(run_id,node_id,event,data) VALUES(%s,%s,%s,%s)",
                    (run_id, event["node_id"], event["event"], Jsonb(event)),
                )

            async def execute(node, value, values):
                await authority()
                if node.kind in {"subworkflow", "iteration", "parallel", "loop"}:
                    return await self.nested(
                        node, value, owner, run_id, expected_pins, context, execution, prefix, authority, callbacks
                    )
                return await self.execute(
                    node,
                    value,
                    values,
                    owner,
                    default_model,
                    instructions,
                    run_id,
                    callbacks=callbacks,
                    expected_pins=expected_pins,
                    usage_context=context,
                )

            graph = compile_workflow(definition, execute, observe)
            result = await graph.ainvoke(
                {"input": text, "values": {}, "output": "", "route": False},
                {"recursion_limit": definition.node_budget + 2}
            )
            validate_text(result["output"], definition.output_schema, output=True)
            await authority()
            return result["output"]

    async def nested(self, node, value, owner, run_id, pins, context, execution, prefix, guard, callbacks):
        async def child(identifier, source, index, *, parallel=False):
            published = await self.published(identifier, owner)
            return await self.run(
                Definition.model_validate(published["definition"]),
                owner,
                published["model_id"],
                published["instructions"],
                source,
                run_id,
                pins["workflows"][identifier]["pins"],
                guard=guard,
                callbacks=callbacks,
                usage_context={**context, "parallel_active": parallel or context.get("parallel_active", False)},
                execution=execution,
                prefix=f"{prefix}{node.id}.{index}.",
            )

        if node.kind == "subworkflow":
            return await child(node.parameters["version_id"], value, 0)
        if node.kind == "loop":
            maximum = node.parameters.get("max_iterations", 5)
            for index in range(maximum):
                await guard()
                value = await child(node.parameters["version_id"], value, index)
                matched = condition(node, value)
                await self.database.execute(
                    "INSERT INTO workflow_node_events(run_id,node_id,event,data) VALUES(%s,%s,'loop.progress',%s)",
                    (run_id, prefix + node.id, Jsonb({"iteration": index + 1, "maximum": maximum, "matched": matched})),
                )
                if matched:
                    return value
            fail(422, "WORKFLOW_LOOP_LIMIT", "条件循环已达到次数上限，仍未满足停止条件；未自动继续")
        if node.kind == "iteration":
            try:
                items = parse_json(value)
            except (ValueError, RecursionError):
                fail(422, "WORKFLOW_ITERATION_INPUT", "迭代输入必须为有效且不含无限值的 JSON 数组")
            if not isinstance(items, list) or len(items) > node.parameters.get("max_items", 10):
                fail(422, "WORKFLOW_ITERATION_LIMIT", "迭代输入必须为最多十项的 JSON 数组")
            outputs = []
            for index, item in enumerate(items):
                source = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                outputs.append(await child(node.parameters["version_id"], source, index))
        elif context.get("parallel_active"):
            # Nested fan-out is serial within an already bounded parallel branch.
            outputs = [
                await child(identifier, value, index) for index, identifier in enumerate(node.parameters["version_ids"])
            ]
        else:

            async def branch(identifier, index):
                async with execution["parallel"]:
                    return await child(identifier, value, index, parallel=True)

            tasks = [
                asyncio.create_task(branch(identifier, index))
                for index, identifier in enumerate(node.parameters["version_ids"])
            ]
            try:
                outputs = await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        return json.dumps(outputs, ensure_ascii=False)

    async def execute(
        self,
        node: Node,
        value: str,
        values: dict,
        owner: UUID,
        default_model: str,
        instructions: str,
        run_id: UUID,
        *,
        callbacks: list | None = None,
        expected_pins: dict | None = None,
        usage_context: dict | None = None,
    ) -> str:
        parameters = node.parameters
        if node.kind == "transform":
            if parameters["operation"] == "template":
                return render_template(parameters.get("template", ""), values)
            return transform(node, value)
        if node.kind == "http":
            return await asyncio.to_thread(fetch_public_text, parameters["url"])
        if node.kind == "rag":
            from app.permissions import require_account

            await require_account(self.database, owner, "knowledge.search")
            result = await self.capabilities.knowledge.search(
                UUID(parameters["knowledge_id"]), owner, value, usage_context=usage_context
            )
            return json.dumps(result, ensure_ascii=False, default=str)
        if node.kind == "mcp":
            server = (await self.database.fetch("SELECT * FROM mcp_servers WHERE id=%s", (parameters["server_id"],)))[0]
            metadata = next(tool for tool in server["tools"] if tool["name"] == parameters["tool"])
            arguments = {
                key: values[item["ref"]] if isinstance(item, dict) and set(item) == {"ref"} else item
                for key, item in parameters.get("arguments", {}).items()
            }
            return await self.capabilities.mcp.tool(
                server,
                metadata,
                owner_id=owner,
                expected_pin=(expected_pins or {}).get("mcp", {}).get(str(server["id"])),
            ).ainvoke(arguments)
        if node.kind == "agent":
            agent = await self.agent(parameters["agent_id"], owner)
            tools, middleware = await self.capabilities.for_owner(owner, agent, usage_context=usage_context)
            if agent["web_enabled"]:
                if agent["web_mode"] == "openai_native":
                    tools.append({"type": "web_search"})
                else:
                    await ensure_available(self.database)
                    tools.append(search_tool(self.database, self.models.vault, owner, UUID(agent["id"])))
            config = await self.models.get(agent["model_id"])
            config = await self.routed(
                config,
                owner,
                run_id,
                node,
                agent.get("model_routing"),
                expected_pins,
                usage_context,
                tools=True,
                native=bool(agent.get("web_enabled") and agent["web_mode"] == "openai_native"),
            )
            from app.model_capabilities import require_tools

            if tools or agent.get("skill_ids"):
                require_tools(config)
            model = self.model_factory(config)
            if hasattr(model, "max_retries"):
                model.max_retries = 0
            from app.harness import RunGuard
            from app.permissions import require_account

            async def permission_guard():
                await require_account(self.database, owner, "workflows.execute")
                if usage_context and usage_context.get("guard"):
                    await usage_context["guard"]()

            graph = build_ask(
                model,
                InMemorySaver(),
                tool_capable=config.supports_tools is True,
                tools=tools,
                middleware=[RunGuard(permission_guard), *middleware],
                instructions=agent["instructions"],
            )
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=value)]},
                {
                    "configurable": {"thread_id": f"workflow:{run_id}:{node.id}"},
                    "recursion_limit": 20,
                    "callbacks": [
                        self.recorder(owner, config, run_id, usage_context),
                        *(callbacks or []),
                    ],
                },
            )
            return result["messages"][-1].text
        config = await self.models.get(parameters.get("model_id") or default_model)
        config = await self.routed(
            config, owner, run_id, node, parameters.get("model_routing"), expected_pins, usage_context
        )
        config = config.model_copy(update={"max_output_tokens": min(config.max_output_tokens or 4096, 8192)})
        model = self.model_factory(config)
        if hasattr(model, "max_retries"):
            model.max_retries = 0
        prompt = parameters.get("instructions", instructions)
        response = await model.ainvoke(
            ([SystemMessage(content=prompt)] if prompt else []) + [HumanMessage(content=value)],
            {
                "callbacks": [
                    self.recorder(owner, config, run_id, usage_context),
                    *(callbacks or []),
                ]
            },
        )
        if not response.text:
            raise ValueError("Empty LLM output")
        return response.text

    async def routed(self, config, owner, run_id, node, enabled, pins, context, *, tools=False, native=False):
        if not enabled:
            return config
        from app.model_routing import resolve

        # run_id identifies this durable workflow invocation; nested runs get their own identity.
        root = (context or {}).get("id", run_id)
        return await resolve(
            self.models,
            owner,
            root,
            config.id,
            enabled=True,
            scope=f"workflow:{run_id}:{node.id}",
            expected=(pins or {}).get("routes", {}).get(node.id),
            tools=tools,
            openai_native=native,
        )

    def recorder(self, owner, config, run_id, context):
        from app.token_estimates import output_reserve

        context = context or {"id": run_id, "mode": "workflow", "lease_token": None}
        return UsageRecorder(
            self.database,
            owner,
            context["id"],
            config.id,
            context["mode"],
            revision=config.revision,
            route_selection_id=config.route_selection_id,
            reserve=output_reserve(config),
            lease_token=context["lease_token"],
            authority=context.get("guard"),
        )
