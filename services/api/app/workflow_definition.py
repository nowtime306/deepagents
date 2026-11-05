"""Text workflow schema and DAG validation without executable expressions."""

import json
import re
from collections import deque
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.workflow_schema import validate_schema

TEMPLATE_TOKEN = re.compile(r"(?<!\{)\{\{([A-Za-z][A-Za-z0-9_-]{0,39})\}\}(?!\})")

KINDS = (
    "start",
    "end",
    "llm",
    "agent",
    "rag",
    "mcp",
    "http",
    "condition",
    "transform",
    "subworkflow",
    "iteration",
    "parallel",
    "loop",
    "approval",
    "code",
)
PARAMETERS = {
    "start": set(),
    "end": {"use_previous"},
    "llm": {"model_id", "instructions", "model_routing"},
    "agent": {"agent_id"},
    "rag": {"knowledge_id"},
    "mcp": {"server_id", "tool", "arguments"},
    "http": {"url"},
    "condition": {"operator", "value"},
    "transform": {"operation", "old", "new", "path", "template", "timeout_ms"},
    "subworkflow": {"version_id"},
    "iteration": {"version_id", "max_items"},
    "parallel": {"version_ids"},
    "loop": {"version_id", "max_iterations", "operator", "value"},
    "approval": {"prompt"},
    "code": {"code", "timeout"},
}


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
    name: str | None = Field(default=None, max_length=80)
    kind: Literal[
        "start",
        "end",
        "llm",
        "agent",
        "rag",
        "mcp",
        "http",
        "condition",
        "transform",
        "subworkflow",
        "iteration",
        "parallel",
        "loop",
        "approval",
        "code",
    ]
    input: str = "input"
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    target: str
    case: Literal["true", "false", "error"] | None = None


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    execution: Literal["readonly", "work"] = "readonly"
    input_schema: dict = Field(default_factory=lambda: {"type": "string"})
    output_schema: dict = Field(default_factory=lambda: {"type": "string"})
    nodes: list[Node] = Field(min_length=2, max_length=30)
    edges: list[Edge] = Field(max_length=60)
    node_budget: int = Field(default=30, ge=2, le=100)
    model_calls: int = Field(default=20, ge=1, le=100)
    token_limit: int = Field(default=128000, ge=1000, le=1000000)


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=-10000, le=10000, allow_inf_nan=False)
    y: float = Field(ge=-10000, le=10000, allow_inf_nan=False)


def default_definition() -> Definition:
    return Definition(
        nodes=[Node(id="start", kind="start"), Node(id="llm", kind="llm"), Node(id="end", kind="end", input="llm")],
        edges=[Edge(source="start", target="llm"), Edge(source="llm", target="end")],
    )


def validate_draft(definition: Definition) -> None:
    """Bound editable drafts without treating incomplete connections as executable."""
    if len(json.dumps(definition.model_dump())) > 65536:
        raise ValueError("工作流定义超过大小限制")
    identifiers = {node.id for node in definition.nodes}
    if len(identifiers) != len(definition.nodes) or "input" in identifiers:
        raise ValueError("节点 ID 重复或使用了保留名称 input")
    if sum(node.kind == "start" for node in definition.nodes) != 1 or not any(
        node.kind == "end" for node in definition.nodes
    ):
        raise ValueError("草稿需要一个开始节点和至少一个结束节点")
    if any(edge.source not in identifiers or edge.target not in identifiers for edge in definition.edges):
        raise ValueError("连线引用了不存在的节点")
    if any(node.parameters.keys() - PARAMETERS[node.kind] for node in definition.nodes):
        raise ValueError("节点存在不支持的参数")


def template_references(template: str) -> list[str]:
    """Parse a bounded placeholder language without evaluating expressions."""
    if not isinstance(template, str) or len(template) > 12000:
        raise ValueError("模板文本最多12000字符")
    matches = list(TEMPLATE_TOKEN.finditer(template))
    remainder = TEMPLATE_TOKEN.sub("", template)
    if len(matches) > 100 or "{{" in remainder or "}}" in remainder:
        raise ValueError("模板仅支持{{input}}和{{节点ID}}，最多100个占位符")
    return [match.group(1) for match in matches]


def render_template(template: str, values: dict[str, str]) -> str:
    """Substitute once, checking the final text budget before allocating output."""
    references = template_references(template)
    for reference in references:
        if reference not in values:
            raise ValueError(f"请提供变量 {reference} 的节点测试值")
    size = len(template) + sum(len(values[ref]) - len(ref) - 4 for ref in references)
    if size > 40000:
        raise ValueError("模板输出超过40000字符")
    return TEMPLATE_TOKEN.sub(lambda match: values[match.group(1)], template)


def node_preview(
    definition: Definition, identifier: str, *, text: str | None = None, variables: dict[str, str] | None = None
) -> Definition:
    """Isolate one node; Work nodes retain durable execution and approval requirements."""
    selected = next((node for node in definition.nodes if node.id == identifier), None)
    if selected is None:
        raise ValueError("节点不存在")
    if selected.kind == "mcp" and text is not None:
        values = {**(variables or {}), selected.input: text}
        configured = selected.parameters.get("arguments", {})
        if not isinstance(configured, dict):
            raise ValueError("MCP 工具参数必须为对象")
        arguments = dict(configured)
        for key, value in arguments.items():
            if isinstance(value, dict) and set(value) == {"ref"}:
                reference = str(value["ref"])
                if reference not in values:
                    raise ValueError(f"请提供变量 {reference} 的节点测试值")
                arguments[key] = values[reference]
        selected = selected.model_copy(update={"parameters": {**selected.parameters, "arguments": arguments}})
    if selected.kind == "transform" and selected.parameters.get("operation") == "template" and text is not None:
        values = {**(variables or {}), selected.input: text}
        rendered = render_template(selected.parameters.get("template"), values)
        # A literal projection prevents test values containing braces from becoming new references.
        selected = selected.model_copy(update={"parameters": {"operation": "replace", "old": text, "new": rendered}})
    selected = selected.model_copy(update={"input": "input"})
    suffix = uuid4().hex[:8]
    start = selected if selected.kind == "start" else Node(id="previewstart_" + suffix, kind="start")
    end = selected if selected.kind == "end" else Node(id="previewend_" + suffix, kind="end")
    nodes = [start, end] if selected.kind in {"start", "end"} else [start, selected, end]
    edges = [Edge(source=start.id, target=end.id)] if len(nodes) == 2 else [
        Edge(source=start.id, target=selected.id), Edge(source=selected.id, target=end.id)
    ]
    if selected.kind == "condition":
        other = Node(id="previewelse_" + suffix, kind="end", input=selected.id)
        nodes.append(other)
        edges = [edges[0], Edge(source=selected.id, target=end.id, case="true"),
                 Edge(source=selected.id, target=other.id, case="false")]
    if end.kind == "end" and selected.kind != "end":
        end.input = selected.id
    return Definition(execution="work" if selected.kind in {"code", "approval"} else "readonly",
                      nodes=nodes, edges=edges, node_budget=max(4, definition.node_budget),
                      model_calls=definition.model_calls, token_limit=definition.token_limit)


def parameters(node: Node) -> None:
    values = node.parameters
    if values.keys() - PARAMETERS[node.kind]:
        raise ValueError(f"节点 {node.id} 存在不支持的参数")
    if node.kind == "approval":
        prompt = values.get("prompt")
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
            raise ValueError("人工审批需要1–2000字的说明")
    if node.kind == "code":
        import ast

        code = values.get("code")
        if not isinstance(code, str) or not 1 <= len(code.strip()) <= 8000:
            raise ValueError("Python代码需要1–8000字符")
        try:
            ast.parse(code)
        except (ValueError, SyntaxError, RecursionError):
            raise ValueError("Python代码语法无效") from None
        if type(values.get("timeout", 30)) is not int or not 1 <= values.get("timeout", 30) <= 60:
            raise ValueError("代码执行时间必须为1–60秒的整数")
    if node.kind == "end" and type(values.get("use_previous", False)) is not bool:
        raise ValueError("结束节点的当前分支结果选项必须为布尔值")
    if node.kind in {"agent", "rag", "mcp"}:
        UUID(str(values.get({"agent": "agent_id", "rag": "knowledge_id", "mcp": "server_id"}[node.kind], "")))
    if node.kind == "llm":
        if not isinstance(values.get("model_routing", False), bool):
            raise ValueError("模型路由开关必须为布尔值")
        if not isinstance(values.get("instructions", ""), str) or len(values.get("instructions", "")) > 12000:
            raise ValueError("LLM 指令必须为不超过 12000 字的文本")
        if not isinstance(values.get("model_id", ""), str) or len(values.get("model_id", "")) > 120:
            raise ValueError("模型标识无效")
    if node.kind in {"condition", "loop"}:
        if values.get("operator") not in {"equals", "not_equals", "contains", "not_empty", "starts_with"}:
            raise ValueError("条件运算符无效")
        if not isinstance(values.get("value", ""), str):
            raise ValueError("条件比较值必须为文本")
    if node.kind == "transform":
        if "timeout_ms" in values and (type(values["timeout_ms"]) is not int or not 1 <= values["timeout_ms"] <= 5000):
            raise ValueError("纯文本转换时限必须为1–5000毫秒的整数")
        if values.get("operation") not in {"text", "upper", "lower", "trim", "replace", "json_get", "template"}:
            raise ValueError("仅支持预置文本转换，不允许代码或表达式")
        if values.get("operation") == "template":
            template_references(values.get("template"))
        if any(not isinstance(values.get(key, ""), str) for key in ("old", "new", "path")):
            raise ValueError("转换参数必须为文本")
    if node.kind == "http" and (not isinstance(values.get("url"), str) or not values["url"].strip()):
        raise ValueError("HTTP 节点需要固定的 HTTPS 地址")
    if node.kind == "mcp":
        if (not isinstance(values.get("tool"), str) or not values["tool"].strip()
                or not isinstance(values.get("arguments", {}), dict)):
            raise ValueError("MCP 工具名称或参数格式无效")
    if node.kind in {"subworkflow", "iteration", "loop"}:
        UUID(str(values.get("version_id", "")))
    if node.kind == "loop":
        maximum = values.get("max_iterations", 5)
        if type(maximum) is not int or not 1 <= maximum <= 10:
            raise ValueError("条件循环最多 1–10 轮")
    if node.kind == "iteration":
        maximum = values.get("max_items", 10)
        if type(maximum) is not int or not 1 <= maximum <= 10:
            raise ValueError("迭代最多 1–10 项")
    if node.kind == "parallel":
        identifiers = values.get("version_ids")
        if not isinstance(identifiers, list) or not 2 <= len(identifiers) <= 4:
            raise ValueError("并行节点需要 2–4 个发布版本，最多两路同时执行")
        for identifier in identifiers:
            UUID(str(identifier))


class NodeStructureError(ValueError):
    """Keep strict validation compatible while locating a known graph node."""

    def __init__(self, node_id: str, message: str) -> None:
        self.node_id = node_id
        super().__init__(message)


def inspect_definition(definition: Definition) -> tuple[list[str], list[dict]]:
    """Reject cycles, dangling edges, incomplete branches and unavailable variable sources."""
    if len(json.dumps(definition.model_dump())) > 65536:
        raise ValueError("工作流定义超过大小限制")
    validate_schema(definition.input_schema)
    validate_schema(definition.output_schema)
    if definition.execution != "work" and any(node.kind in {"approval", "code"} for node in definition.nodes):
        raise ValueError("人工审批和代码节点只能在持久任务模式运行")
    issues = []
    nodes = {node.id: node for node in definition.nodes}
    if len(nodes) != len(definition.nodes) or "input" in nodes:
        raise ValueError("节点 ID 重复或使用了保留名称 input")
    starts = [node.id for node in nodes.values() if node.kind == "start"]
    if len(starts) != 1 or not any(node.kind == "end" for node in nodes.values()):
        raise ValueError("流程必须包含一个开始节点和至少一个结束节点")
    incoming = {identifier: set() for identifier in nodes}
    outgoing = {identifier: [] for identifier in nodes}
    edges = set()
    for edge in definition.edges:
        if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
            if edge.source in nodes or edge.target in nodes:
                raise NodeStructureError(edge.source if edge.source in nodes else edge.target, "存在悬空连线或自循环")
            raise ValueError("存在悬空连线或自循环")
        identity = (edge.source, edge.target, edge.case)
        if identity in edges:
            raise NodeStructureError(edge.source, "连线重复")
        edges.add(identity)
        incoming[edge.target].add(edge.source)
        outgoing[edge.source].append(edge)
    for identifier, node in nodes.items():
        if node.kind == "start" and incoming[identifier]:
            raise NodeStructureError(identifier, "开始节点不能有输入连线")
        if node.kind != "start" and not incoming[identifier]:
            raise NodeStructureError(identifier, "存在无法从开始节点到达的节点")
        if node.kind == "end":
            if outgoing[identifier]:
                raise NodeStructureError(identifier, "结束节点不能有输出连线")
        elif node.kind == "condition":
            if len(outgoing[identifier]) != 2 or {edge.case for edge in outgoing[identifier]} != {"true", "false"}:
                raise NodeStructureError(identifier, "条件节点必须配置命中和默认两个出口")
        elif node.kind in {"transform", "iteration", "loop"} and any(
            edge.case == "error" for edge in outgoing[identifier]
        ):
            if len(outgoing[identifier]) != 2 or {edge.case for edge in outgoing[identifier]} != {None, "error"}:
                raise NodeStructureError(identifier, "确定性错误分支需要一个正常出口和一个错误出口")
        elif len(outgoing[identifier]) != 1 or outgoing[identifier][0].case is not None:
            raise NodeStructureError(
                identifier, "普通节点需要一个无条件出口；仅转换、迭代输入及循环上限支持确定性错误出口"
            )
    degrees = {identifier: len(parents) for identifier, parents in incoming.items()}
    pending = deque(identifier for identifier, count in degrees.items() if not count)
    order = []
    while pending:
        identifier = pending.popleft()
        order.append(identifier)
        for target in {edge.target for edge in outgoing[identifier]}:
            degrees[target] -= 1
            if not degrees[target]:
                pending.append(target)
    if len(order) != len(nodes) or len(order) > definition.node_budget:
        raise ValueError("流程包含环或超过节点预算")
    dominators = {starts[0]: {starts[0]}}
    for identifier in order:
        if identifier != starts[0]:
            dominators[identifier] = set.intersection(*(dominators[parent] for parent in incoming[identifier])) | {
                identifier
            }
        try:
            parameters(nodes[identifier])
        except (ValueError, TypeError) as error:
            issues.append({"code": "NODE_PARAMETERS", "node_id": identifier,
                           "field": "parameters", "message": str(error)})
            continue
        references = (
            []
            if nodes[identifier].kind == "end" and nodes[identifier].parameters.get("use_previous")
            else [nodes[identifier].input]
        )
        if nodes[identifier].kind == "mcp":
            references += [
                str(value["ref"])
                for value in nodes[identifier].parameters.get("arguments", {}).values()
                if isinstance(value, dict) and set(value) == {"ref"}
            ]
        if nodes[identifier].kind == "transform" and nodes[identifier].parameters.get("operation") == "template":
            references += template_references(nodes[identifier].parameters.get("template", ""))
        for source in dict.fromkeys(references):
            if source != "input" and source not in dominators[identifier] - {identifier}:
                field = ("input" if source == nodes[identifier].input else
                         "parameters.arguments" if nodes[identifier].kind == "mcp" else "parameters.template")
                issues.append({"code": "VARIABLE_UNAVAILABLE", "node_id": identifier, "field": field,
                               "message": f"节点 {identifier} 引用了尚不可用的变量 {source}"})
    return order, issues


def configuration_issues(definition: Definition) -> list[dict]:
    """Collect independent node errors, stopping safely at invalid graph structure."""
    try:
        return inspect_definition(definition)[1]
    except NodeStructureError as error:
        return [{"code": "WORKFLOW_STRUCTURE", "node_id": error.node_id, "field": "edges", "message": str(error)}]
    except ValueError as error:
        return [{"code": "WORKFLOW_STRUCTURE", "node_id": None, "field": "definition", "message": str(error)}]


def validate(definition: Definition) -> list[str]:
    """Reject the same configuration issues used by the read-only checking endpoint."""
    order, issues = inspect_definition(definition)
    if issues:
        raise ValueError(issues[0]["message"])
    return order
