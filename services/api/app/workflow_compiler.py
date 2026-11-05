"""Compile validated text workflows into an executable LangGraph DAG."""

import json
import time
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.errors import APIError
from app.workflow_definition import Definition, Node, validate
from app.workflow_pure_timeout import confirmed_pure_timeout


class State(TypedDict):
    input: str
    values: dict[str, str]
    output: str
    route: bool
    failed: bool
    last_output: str


class WorkflowNodeFailed(RuntimeError):
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        super().__init__(f"节点 {node_id} 执行失败")


def condition(node: Node, value: str) -> bool:
    operator = node.parameters["operator"]
    expected = node.parameters.get("value", "")
    return {
        "equals": lambda: value == expected,
        "not_equals": lambda: value != expected,
        "contains": lambda: expected in value,
        "not_empty": lambda: bool(value.strip()),
        "starts_with": lambda: value.startswith(expected),
    }[operator]()


def compile_workflow(
    definition: Definition,
    execute: Callable[[Node, str, dict[str, str]], Awaitable[str]],
    observe: Callable[[dict], Awaitable[None]],
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    before_node: Callable[[Node], Awaitable[None]] | None = None,
) -> CompiledStateGraph:
    """Compile single-path DAGs; bounded composition is an explicit runtime node."""
    validate(definition)
    graph = StateGraph(State)

    def target(identifier: str) -> str:
        return "__work_pause__" + identifier if before_node else identifier

    def boundary(node: Node):
        async def invoke(state: State) -> dict:
            await before_node(node)
            return {}

        return invoke

    def action(node: Node):
        async def invoke(state: State) -> dict:
            source = (
                state["last_output"]
                if node.kind == "end" and node.parameters.get("use_previous")
                else state["input"]
                if node.input == "input"
                else state["values"][node.input]
            )
            started = time.monotonic()
            handled = False
            await observe({"event": "node.started", "node_id": node.id, "kind": node.kind, "input": source})
            try:
                if node.kind == "start":
                    output = state["input"]
                elif node.kind == "end":
                    output = source
                elif node.kind == "condition":
                    output = "true" if condition(node, source) else "false"
                else:
                    output = await execute(node, source, {"input": state["input"], **state["values"]})
                if not isinstance(output, str) or len(output) > 40000:
                    raise ValueError("Node output exceeds text budget")
            except GraphInterrupt:
                raise
            except Exception as error:
                await observe(
                    {
                        "event": "node.failed",
                        "node_id": node.id,
                        "kind": node.kind,
                        "error_code": error.code if isinstance(error, APIError) else "NODE_EXECUTION_FAILED",
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                )
                deterministic = node.kind == "transform" and isinstance(
                    error, (ValueError, KeyError, IndexError, TypeError)
                )
                known = isinstance(error, APIError) and (
                    (node.kind == "transform" and confirmed_pure_timeout(error, node))
                    or
                    (
                        node.kind == "iteration"
                        and error.code in {"WORKFLOW_ITERATION_INPUT", "WORKFLOW_ITERATION_LIMIT"}
                    )
                    or (node.kind == "loop" and error.code == "WORKFLOW_LOOP_LIMIT")
                )
                has_handler = any(edge.source == node.id and edge.case == "error" for edge in definition.edges)
                if not ((deterministic or known) and has_handler):
                    raise WorkflowNodeFailed(node.id) from error
                output, handled = (
                    json.dumps({"error_code": error.code}) if known else '{"error_code":"TRANSFORM_FAILED"}',
                    True,
                )
            await observe(
                {
                    "event": "node.handled" if handled else "node.completed",
                    "node_id": node.id,
                    "kind": node.kind,
                    "output": output,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
            return {
                "values": {**state["values"], node.id: output},
                "failed": handled,
                "last_output": output,
                **({"route": output == "true"} if node.kind == "condition" else {}),
                **({"output": output} if node.kind == "end" else {}),
            }

        return invoke

    for node in definition.nodes:
        graph.add_node(node.id, action(node))
        if before_node:
            # A pause and a code approval must not share one node's positional interrupt values.
            graph.add_node(target(node.id), boundary(node))
            graph.add_edge(target(node.id), node.id)
    start = next(node.id for node in definition.nodes if node.kind == "start")
    graph.add_edge(START, target(start))
    for node in definition.nodes:
        outgoing = [edge for edge in definition.edges if edge.source == node.id]
        if node.kind == "end":
            graph.add_edge(node.id, END)
        elif node.kind == "condition":
            graph.add_conditional_edges(
                node.id, lambda state: state["route"], {edge.case == "true": target(edge.target) for edge in outgoing}
            )
        elif any(edge.case == "error" for edge in outgoing):
            graph.add_conditional_edges(
                node.id, lambda state: state["failed"], {edge.case == "error": target(edge.target) for edge in outgoing}
            )
        else:
            graph.add_edge(node.id, target(outgoing[0].target))
    return graph.compile(checkpointer=checkpointer)
