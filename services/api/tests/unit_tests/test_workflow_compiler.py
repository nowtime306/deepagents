"""Graph validation proves branch shape and variable availability before execution."""

import pytest

from app.workflow_compiler import compile_workflow
from app.workflow_definition import Definition, Edge, Node, default_definition, validate


async def test_persistent_approval_interrupt_is_not_a_node_failure_or_a_replayed_prior_step():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command, interrupt

    definition = Definition(
        execution="work",
        nodes=[Node(id="start", kind="start"),
               Node(id="before", kind="transform", parameters={"operation": "upper"}),
               Node(id="review", kind="approval", input="before", parameters={"prompt": "确认"}),
               Node(id="end", kind="end", input="review")],
        edges=[Edge(source="start", target="before"), Edge(source="before", target="review"),
               Edge(source="review", target="end")],
    )
    counts, events, saver = {"before": 0, "approved": 0}, [], InMemorySaver()

    async def execute(node, value, values):
        if node.kind == "approval":
            assert interrupt({"review": value}) is True
            counts["approved"] += 1
            return value
        counts["before"] += 1
        return value.upper()

    async def observe(event):
        events.append(event)

    config = {"configurable": {"thread_id": "fixed-workflow-approval"}}
    first = await compile_workflow(definition, execute, observe, checkpointer=saver).ainvoke(
        {"input": "once", "values": {}, "output": "", "route": False}, config)
    assert counts == {"before": 1, "approved": 0}
    assert first["__interrupt__"] and not any(event["event"] == "node.failed" for event in events)
    result = await compile_workflow(definition, execute, observe, checkpointer=saver).ainvoke(
        Command(resume={first["__interrupt__"][0].id: True}), config)
    assert result["output"] == "ONCE" and counts == {"before": 1, "approved": 1}


def branched():
    return Definition(
        nodes=[
            Node(id="start", kind="start"),
            Node(id="choose", kind="condition", parameters={"operator": "contains", "value": "yes"}),
            Node(id="yes", kind="transform", parameters={"operation": "upper"}),
            Node(id="no", kind="transform", parameters={"operation": "lower"}),
            Node(id="end_yes", kind="end", input="yes"),
            Node(id="end_no", kind="end", input="no"),
        ],
        edges=[
            Edge(source="start", target="choose"),
            Edge(source="choose", target="yes", case="true"),
            Edge(source="choose", target="no", case="false"),
            Edge(source="yes", target="end_yes"),
            Edge(source="no", target="end_no"),
        ],
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.edges.append(Edge(source="llm", target="start")),
        lambda d: d.edges.append(Edge(source="missing", target="end")),
        lambda d: setattr(d.nodes[-1], "input", "end"),
        lambda d: d.nodes.append(Node(id="llm", kind="llm")),
    ],
)
def test_invalid_graphs_are_rejected(mutation):
    definition = default_definition()
    mutation(definition)
    with pytest.raises(ValueError):
        validate(definition)


def test_branch_local_variables_cannot_be_read_from_other_branch():
    definition = branched()
    definition.nodes[-1].input = "yes"
    with pytest.raises(ValueError, match="尚不可用"):
        validate(definition)


def test_missing_default_and_code_expressions_are_rejected():
    definition = branched()
    definition.edges = [edge for edge in definition.edges if edge.case != "false"]
    with pytest.raises(ValueError):
        validate(definition)
    definition = branched()
    definition.nodes[2].parameters = {"operation": "eval", "expression": "1+1"}
    with pytest.raises(ValueError):
        validate(definition)


@pytest.mark.parametrize("value,expected,excluded", [("yes now", "YES NOW", "no"), ("NO now", "no now", "yes")])
async def test_real_langgraph_runs_only_the_selected_branch(value, expected, excluded):
    observed = []

    async def execute(node, text, values):
        return text.upper() if node.parameters["operation"] == "upper" else text.lower()

    async def observe(event):
        observed.append(event)

    definition = branched()
    graph = compile_workflow(definition, execute, observe)
    result = await graph.ainvoke({"input": value, "values": {}, "output": "", "route": False})
    assert result["output"] == expected
    assert excluded not in {event["node_id"] for event in observed}
    assert observed[-1]["event"] == "node.completed"


async def test_deterministic_error_branch_runs_without_repeating_failed_node():
    graph = Definition(
        nodes=[
            Node(id="start", kind="start"),
            Node(id="parse", kind="transform", parameters={"operation": "json_get", "path": "name"}),
            Node(id="normal", kind="end", input="parse"),
            Node(id="failed", kind="end", input="parse"),
        ],
        edges=[
            Edge(source="start", target="parse"),
            Edge(source="parse", target="normal"),
            Edge(source="parse", target="failed", case="error"),
        ],
    )
    called, observed = [], []

    async def execute(node, text, values):
        from app.workflow_runtime import transform

        called.append(node.id)
        return transform(node, text)

    async def observe(event):
        observed.append(event)

    result = await compile_workflow(graph, execute, observe).ainvoke(
        {"input": "bad json", "values": {}, "output": "", "route": False}
    )
    assert result["output"] == '{"error_code":"TRANSFORM_FAILED"}'
    assert called == ["parse"] and observed[-1]["node_id"] == "failed"
    assert any(event["event"] == "node.handled" for event in observed)
    graph.nodes[1].kind = "llm"
    graph.nodes[1].parameters = {}
    with pytest.raises(ValueError):
        validate(graph)
