"""Pure fixed F05 v2 material, shared by the target profile and HTTP client."""

import hashlib

CASE = "AET-F05"
INPUT = "seed"
OUTPUT = "seed-code"
CODE = ("import sys\nfrom pathlib import Path\np=Path('counter.txt')\n"
        "p.write_text((p.read_text() if p.exists() else '')+'1')\n"
        "sys.stdout.write(sys.stdin.read()+'-code')")
CODE_SHA = hashlib.sha256(CODE.encode()).hexdigest()
LIMIT = 10
PROMPTS = {"before": "确认执行固定代码", "after": "确认固定成果"}


def definition():
    nodes = [("start", "start", "input", {}), ("before", "approval", "start", {"prompt": PROMPTS["before"]}),
             ("code", "code", "before", {"code": CODE, "timeout": 5}),
             ("after", "approval", "code", {"prompt": PROMPTS["after"]}), ("end", "end", "after", {})]
    return {"schema_version": 1, "execution": "work", "input_schema": {"type": "string"}, "output_schema": {"type": "string"},
            "node_budget": 5, "model_calls": 1, "token_limit": 1000,
            "nodes": [{"id": identifier, "name": None, "kind": kind, "input": source, "parameters": params}
                      for identifier, kind, source, params in nodes],
            "edges": [{"source": nodes[index][0], "target": nodes[index + 1][0], "case": None} for index in range(4)]}


def arguments(node):
    if node not in {"before", "code", "after"}:
        raise ValueError("Unknown fixed code approval")
    value = OUTPUT if node == "after" else INPUT
    result = {"node_id": node, "input_digest": hashlib.sha256(value.encode()).hexdigest(),
              "input_preview": value, "input_characters": len(value)}
    result.update({"code": CODE, "code_digest": CODE_SHA, "timeout": 5} if node == "code" else {"prompt": PROMPTS[node]})
    return result
