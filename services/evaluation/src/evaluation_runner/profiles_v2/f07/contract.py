"""Separately versioned fixed F07 graphs; no dynamic expressions or external destinations."""

import hashlib
import json

CASE = "AET-F07"
BASE = "/api/v1/agent-evaluations/profiles/f07-v2"
INPUT = "seed"
OUTPUT = "已处理超时"
LIMITS = {"max_seconds": 120, "max_roots": 4, "max_model_calls": 1, "max_tokens": 15, "max_sandboxes": 0}

EXECUTION_LIMITS = {
    "schema_version": 1,
    "enforcement": "controlled-model-factory-v1",
    "scope": "resource-run-root",
    "profiles": {CASE: {"mode": "workflow", "max_model_calls": 1, "max_tokens": 15}},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def graph(scene):
    assert scene in {"json", "timer", "model"}
    action = {
        "id": "action",
        "kind": "llm",
        "input": "start",
        "parameters": {"instructions": "F07 fixed timeout companion"},
    }
    if scene != "model":
        action = {
            "id": "action",
            "kind": "transform",
            "input": "start",
            "parameters": (
                {"operation": "json_get", "path": "name"}
                if scene == "json"
                else {"operation": "upper", "timeout_ms": 5}
            ),
        }
    nodes = [{"id": "start", "kind": "start"}, action, {"id": "normal", "kind": "end", "input": "action"}]
    edges = [{"source": "start", "target": "action"}, {"source": "action", "target": "normal"}]
    if scene != "model":
        nodes += [
            {
                "id": "fallback",
                "kind": "transform",
                "input": "input",
                "parameters": {"operation": "template", "template": "已处理错误" if scene == "json" else OUTPUT},
            },
            {"id": "failed", "kind": "end", "input": "fallback"},
        ]
        edges += [
            {"source": "action", "target": "fallback", "case": "error"},
            {"source": "fallback", "target": "failed"},
        ]
    return {
        "schema_version": 1,
        "execution": "readonly",
        "input_schema": {"type": "string"},
        "output_schema": {"type": "string"},
        "node_budget": 8,
        "model_calls": 1,
        "token_limit": 1000,
        "nodes": [{"name": None, "input": "input", "parameters": {}, **node} for node in nodes],
        "edges": [{"case": None, **edge} for edge in edges],
    }


def scene_for(definition):
    for scene in ("json", "timer", "model"):
        if definition == graph(scene):
            return scene
    raise ValueError("Only exact fixed F07 graphs are supported")


CONTRACT = {
    "case_id": CASE,
    "version": 2,
    "limits": LIMITS,
    "graphs": {scene: graph(scene) for scene in ("json", "timer", "model")},
    "unknown_policy": "Keep model root FAILED and usage UNKNOWN/null, no fallback, later generation or refund",
}


def definition():
    return {
        "schema_version": 1,
        "dataset_id": "a20-f07-v2",
        "dataset_version": 2,
        "cases": [
            {
                "case_id": CASE,
                "name": "真实纯转换超时与未知模型失败传播",
                "mode": "workflow",
                "requirements": ["AE-03", "AE-11"],
                "tags": ["controlled-engineering"],
                "input": INPUT,
                "fixture_profile": {"id": "handled-transform-error", "version": 2},
                "fixture_parameters": {"definition_digest": digest(CONTRACT)},
                "validators": [
                    {"registered_id": "timeout_error_branch", "version": 1, "parameters": {}, "required": True}
                ],
                "metrics": [],
                "repeats": 1,
                "approval_policy": "none",
                "dependencies": [],
                "budgets": {"timeout_seconds": 120, "max_model_calls": 1, "max_tokens": 15},
            }
        ],
    }


def check_specimen(specimen):
    from evaluation_runner.contracts import Case

    expected = Case.model_validate(definition()["cases"][0])
    if specimen.model_dump() != expected.model_dump():
        raise ValueError("Exact separate F07 v2 definition is required")
