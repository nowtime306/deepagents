"""Synthetic formal-HTTP fixture for engineering tests; never an acceptance artifact."""

from copy import deepcopy
from uuid import uuid4

from evaluation_runner.fixed_evidence import binding, text_digest
from evaluation_runner.journal import digest
from evaluation_runner.knowledge_protocol import DOCUMENT
from evaluation_runner.team_budget_protocol import ledger


def build(specimen):
    owner, environment, root, member, namespace, index = [
        str(uuid4()) for _ in range(6)
    ]
    fixture = {
        "id": str(uuid4()),
        "revision": 2,
        "model_id": "lead",
        "reviewer_model_id": "child",
        "agent_id": str(uuid4()),
        "agent_version_id": str(uuid4()),
        "reviewer_agent_id": str(uuid4()),
        "reviewer_version_id": str(uuid4()),
    }
    if specimen.case_id == "AET-W06":
        fixture.update(
            embedding_model_id="embedding",
            knowledge_id=str(uuid4()),
            document_id=str(uuid4()),
            document_revision=1,
        )
    attempt = {
        "attempt_id": str(uuid4()),
        "case_id": specimen.case_id,
        "repeat_index": 1,
        "owner_id": owner,
        "environment_id": environment,
        "sandbox_namespace": namespace,
        "fixture": fixture,
        "case_snapshot": specimen.model_dump(mode="json"),
        "platform_root": {
            "mode": "work",
            "id": root,
            "fixture_id": fixture["id"],
            "path": "/api/v1/work-runs/" + root,
        },
    }
    data = {
        "schema_version": 1,
        "owner_id": owner,
        "environment_id": environment,
        "target_digest": "a" * 64,
        "suite_context": {
            "suite_id": str(uuid4()),
            "catalog_digest": "b" * 64,
            "case_definition_digest": digest(specimen.model_dump(mode="json")),
            "rules_id": "controlled-a20-v1",
            "candidate_commit": "c" * 40,
            "repeat_index": 1,
        },
        "attempts": [attempt],
    }
    models = {
        "lead": {"id": "lead", "revision": 1},
        "reviewer": {"id": "child", "revision": 1},
    }
    sequence = [
        ("lead", "chat", 10, 5),
        ("reviewer", "chat", 10, 5),
        ("reviewer", "chat", None, None),
    ]
    if specimen.case_id == "AET-W06":
        models["embedding"] = {"id": "embedding", "revision": 1}
        sequence = [
            ("lead", "chat", 10, 5),
            ("reviewer", "chat", 10, 5),
            ("reviewer", "chat", 10, 5),
            ("reviewer", "embedding", 7, 0),
            ("reviewer", "chat", 10, 5),
            ("lead", "chat", 15000, 933),
        ]
    calls = [
        {
            "call_id": str(uuid4()),
            "owner_id": owner,
            "run_id": root,
            "model_id": "embedding" if kind == "embedding" else models[role]["id"],
            "model_revision": 1,
            "role_name": role,
            "team_call_id": member if role == "reviewer" else None,
            "call_kind": kind,
            "status": "UNKNOWN" if incoming is None else "KNOWN",
            "input_tokens": incoming,
            "output_tokens": outgoing,
        }
        for role, kind, incoming, outgoing in sequence
    ]
    native = ledger(calls, owner=owner, root=root, models=models, member=member)

    def model_snapshot(model, records):
        return {
            "resource_kind": "model",
            "resource_id": model,
            "revision": "1",
            "snapshot_id": str(uuid4()),
            "used_at": "2026-11-04T00:00:00+00:00",
            "snapshot": {
                "id": model,
                "revision": 1,
                "model": "eval::" + specimen.case_id + "::v1",
                "kind": "embedding" if model == "embedding" else "chat",
                "protocol": "openai",
            },
            "calls": records,
        }

    trace = {
        "mode": "work",
        "run_id": root,
        "status": "NEEDS_REVIEW",
        "business_version": {
            "published_version_id": fixture["agent_version_id"],
            "members": [
                {
                    "role": "reviewer",
                    "agent_id": fixture["reviewer_agent_id"],
                    "version_id": fixture["reviewer_version_id"],
                    "version": 1,
                }
            ],
        },
        "items": [],
    }
    for model in models.values():
        records = [
            {
                key: row[key]
                for key in (
                    "call_id",
                    "run_id",
                    "model_id",
                    "model_revision",
                    "status",
                    "input_tokens",
                    "output_tokens",
                )
            }
            | {"mode": "work"}
            for row in calls
            if row["model_id"] == model["id"]
        ]
        trace["items"].append(model_snapshot(model["id"], records))
    baseline = None
    if specimen.case_id == "AET-W06":
        chunk = str(uuid4())
        usage = {
            "calls": 1,
            "reported_calls": 1,
            "unknown_calls": 0,
            "pending_calls": 0,
            "input_tokens": 7,
            "output_tokens": 0,
        }
        call = {
            "call_id": str(uuid4()),
            "run_id": index,
            "mode": "resource",
            "model_id": "embedding",
            "model_revision": 1,
            "status": "KNOWN",
            "input_tokens": 7,
            "output_tokens": 0,
        }
        baseline = {
            "document_id": fixture["document_id"],
            "chunk_id": chunk,
            "document_revision": 1,
            "content": DOCUMENT,
            "operation_id": index,
            "usage": usage,
            "trace": {
                "mode": "resource",
                "run_id": index,
                "status": "SUCCEEDED",
                "business_version": {},
                "items": [model_snapshot("embedding", [call])],
            },
            "provider": {
                "mode": "resource",
                "run_id": index,
                "status": "SUCCEEDED",
                "controlled_model": {
                    "recorded": True,
                    "complete": True,
                    "case_id": specimen.case_id,
                    "provider_calls": 1,
                    "tokens": 7,
                },
            },
            "knowledge": {
                "id": fixture["knowledge_id"],
                "owner_id": owner,
                "embedding_model_id": "embedding",
                "rerank_model_id": None,
            },
            "document": {
                "id": fixture["document_id"],
                "knowledge_id": fixture["knowledge_id"],
                "revision": 1,
                "status": "READY",
                "source_digest": text_digest(DOCUMENT),
                "chunks": 1,
                "vector_status": "READY",
                "vector_model_id": "embedding",
                "vector_model_revision": 1,
                "vector_fingerprint": "d" * 64,
                "vector_dimensions": 4,
            },
            "revisions": [
                {
                    "document_id": fixture["document_id"],
                    "revision": 1,
                    "source_digest": text_digest(DOCUMENT),
                    "chunks": 1,
                }
            ],
            "operation": {
                "id": index,
                "action": "index",
                "source_id": fixture["document_id"],
                "source_revision": 1,
                "status": "SUCCEEDED",
                "error_code": None,
            },
        }

    def record(kind, result, status="SUCCEEDED", code=None):
        return {
            "id": str(uuid4()),
            "owner_id": owner,
            "run_id": root,
            "case_id": specimen.case_id,
            "kind": kind,
            "team_call_id": None if kind == "budget-write" else member,
            "status": status,
            "error_code": code,
            "result": result,
            "knowledge_id": fixture.get("knowledge_id") if kind == "query" else None,
        }

    raw = "team-evidence" if specimen.case_id == "AET-W05" else "bounded"
    records = [record("member-read", {"sha256": text_digest(raw), "size": len(raw)})]
    if baseline:
        retrieval = {
            "operation_id": None,
            "mode": "hybrid",
            "reranked": False,
            "warning": None,
            "evidence_status": "candidates",
            "guidance": "Validate original material",
            "keyword_candidates": 1,
            "keyword_filtered": 0,
            "keyword_min_coverage": 0.2,
            "score_type": "rrf",
            "items": [
                {
                    "chunk_id": baseline["chunk_id"],
                    "document_id": fixture["document_id"],
                    "document_revision": 1,
                    "name": "team-warranty.txt",
                    "location": "1",
                    "text": DOCUMENT,
                    "score": 0.1,
                    "keyword_score": 1.0,
                    "keyword_coverage": 1.0,
                    "vector_score": 1.0,
                }
            ],
        }
        records += [
            record(
                "query", {"query": "\u661f\u6865 \u4fdd\u4fee", "retrieval": retrieval}
            ),
            record(
                "budget-write",
                {
                    "tool_call_id": "ae-budget-denied-write",
                    "provider_calls_before": 6,
                    "tokens_before": 16000,
                },
                "DENIED",
                "WORK_BUDGET_EXCEEDED",
            ),
        ]
    provider = {
        "observed": True,
        "complete": baseline is not None,
        "calls": 7 if baseline else 3,
        "tokens": 16007 if baseline else 45,
        "pending": 0,
        "unknown_roots": 0 if baseline else 1,
        "roots": [
            {
                "mode": "work",
                "run_id": root,
                "phase": None,
                "calls": 6 if baseline else 3,
                "tokens": 16000 if baseline else 45,
            }
        ],
    }
    if baseline:
        provider["roots"].append(
            {
                "mode": "resource",
                "run_id": index,
                "phase": None,
                "calls": 1,
                "tokens": 7,
            }
        )
    observation = {
        "schema_version": 1,
        "contract": "owned-team-native-effects-v2",
        "environment_id": environment,
        "owner_id": owner,
        "namespace": namespace,
        "case_id": specimen.case_id,
        "binding": {
            "agent_id": fixture["agent_id"],
            "agent_version_id": fixture["agent_version_id"],
            "reviewer_agent_id": fixture["reviewer_agent_id"],
            "reviewer_version_id": fixture["reviewer_version_id"],
            "reviewer_model_id": "child",
        },
        "run": {
            "goal_digest": text_digest(specimen.input),
            "id": root,
            "owner_id": owner,
            "task_id": fixture["id"],
            "model_id": "lead",
            "model_revision": 1,
            "task_revision": 2,
            "status": "NEEDS_REVIEW",
            "error_code": "WORK_BUDGET_EXCEEDED" if baseline else "TEAM_RESULT_UNKNOWN",
            "token_limit": 16000 if baseline else 20000,
            "used_tokens": 16000 if baseline else 30,
            "model_calls": len(calls),
            "usage_unknown": baseline is None,
        },
        "members": [
            {
                "id": member,
                "role_name": "reviewer",
                "version_id": fixture["reviewer_version_id"],
                "model_id": "child",
                "status": "SUCCEEDED" if baseline else "UNKNOWN",
                "error_code": None if baseline else "TEAM_RESULT_UNKNOWN",
                "tool_call_id": "ae-team-delegate",
            }
        ],
        "ledger": calls,
        "sessions": [
            {
                "id": str(uuid4()),
                "sandbox_id": str(uuid4()),
                "state": "RELEASED",
                "run_id": root,
            }
        ],
        "effects": {
            "operations": 0,
            "owner_model_calls": len(calls) + (1 if baseline else 0),
            "owner_work_runs": 1,
            "owner_work_tasks": 1,
            "owner_ask_runs": 0,
            "owner_workflow_runs": 0,
            "owner_sandboxes": 1,
            "unknown_usage": 0 if baseline else 1,
            "owner_resource_runs": 1 if baseline else 0,
        },
        "audit": {
            "contract": "controlled-team-effects-v2",
            "complete": True,
            "records": records,
        },
        "provider": provider,
    }
    body = {
        "baseline": baseline,
        "observation": observation,
        "trace": trace,
        "events": [
            {"event_id": 1, "event": "run.started"},
            {"event_id": 2, "event": "tool.requested"},
        ],
        "native_work": native,
        "artifacts": [
            {
                "id": str(uuid4()),
                "path_sha256": text_digest("/input.txt"),
                "digest": text_digest(raw),
                "size": len(raw),
            }
        ],
    }
    usage = deepcopy(native)
    if baseline:
        for key in usage:
            usage[key] += baseline["usage"][key]
    snapshot = {
        "schema_version": 1,
        "kind": "team-facts",
        "binding": binding(data, attempt),
        "body": body,
        "usage": usage,
    }
    return data, attempt, snapshot
