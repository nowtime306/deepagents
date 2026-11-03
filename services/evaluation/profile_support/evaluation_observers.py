"""Fixed, owner-scoped observations for the test harness only; no scripts/SQL/path inputs."""

from typing import Literal
from uuid import UUID

from app.agent_evaluation_environment import environment
from app.errors import fail
from app.resource_trace_api import owned
from fastapi import Request

WORKFLOW_COUNTS = ("workflow_runs", "workflow_events", "ask_runs", "work_tasks", "work_runs", "model_calls", "operations",
                   "sandbox_sessions", "active_workflow_runs", "active_ask_runs", "active_work_runs", "active_sandboxes",
                   "unknown_operations", "unknown_model_usage")


async def workflow_effects(request, identifier):
    """Only sealed readonly engineering cases can observe their own test owner counters."""
    proof = await environment(request)
    rows = await request.app.state.db.fetch(
        "WITH o AS (SELECT id,owner_id,model_id FROM workflows WHERE id=%s AND owner_id=%s) "
        "SELECT o.model_id,(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace,"
        "(SELECT coalesce(jsonb_agg(id ORDER BY id),'[]'::jsonb) FROM workflow_runs WHERE workflow_id=o.id) AS run_ids,"
        "(SELECT count(*) FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE w.owner_id=o.owner_id) AS workflow_runs,"
        "(SELECT count(*) FROM workflow_node_events e JOIN workflow_runs r ON r.id=e.run_id "
        "JOIN workflows w ON w.id=r.workflow_id WHERE w.owner_id=o.owner_id) AS workflow_events,"
        "(SELECT count(*) FROM task_runs r JOIN conversations c ON c.id=r.conversation_id WHERE c.owner_id=o.owner_id) AS ask_runs,"
        "(SELECT count(*) FROM work_tasks WHERE owner_id=o.owner_id) AS work_tasks,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=o.owner_id) AS work_runs,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=o.owner_id) AS model_calls,"
        "(SELECT count(*) FROM operation_executions e JOIN work_queue_runs r ON r.id=e.run_id WHERE r.owner_id=o.owner_id) AS operations,"
        "(SELECT count(*) FROM work_sandbox_sessions WHERE owner_id=o.owner_id) AS sandbox_sessions,"
        "(SELECT count(*) FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id "
        "WHERE w.owner_id=o.owner_id AND r.status IN ('PENDING','RUNNING')) AS active_workflow_runs,"
        "(SELECT count(*) FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
        "WHERE c.owner_id=o.owner_id AND r.status IN ('PENDING','RUNNING')) AS active_ask_runs,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=o.owner_id "
        "AND status NOT IN ('COMPLETED','FAILED','CANCELLED')) AS active_work_runs,"
        "(SELECT count(*) FROM work_sandbox_sessions WHERE owner_id=o.owner_id AND state<>'RELEASED') AS active_sandboxes,"
        "(SELECT count(*) FROM operation_executions e JOIN work_queue_runs r ON r.id=e.run_id "
        "WHERE r.owner_id=o.owner_id AND e.status IN ('STARTED','UNKNOWN')) AS unknown_operations,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=o.owner_id AND status IN ('PENDING','UNKNOWN')) AS unknown_model_usage "
        "FROM o", (identifier, proof["owner_id"]))
    if not rows:
        fail(404, "NOT_FOUND", "固定评测工作流不存在或无权访问")
    row = rows[0]
    state = request.app.state.evaluation_profiles
    with state.lock:
        binding = state.models.get(row["model_id"])
        if (not binding or binding["owner_id"] != str(proof["owner_id"])
                or binding["case_id"] not in {"AET-F02", "AET-F04"} or binding["role"] != "lead"):
            fail(403, "EVALUATION_WORKFLOW_NOT_REGISTERED", "仅允许固定只读评测工作流")
        roots = [value for key, value in state.roots.items() if key[0] == str(proof["owner_id"])]
        provider = {"observed": True, "complete": not state.storage_failed and not any(
            value["unknown"] or value["pending"] for value in roots),
            "calls": sum(value["calls"] for value in roots), "pending": sum(value["pending"] for value in roots),
            "unknown_roots": sum(bool(value["unknown"]) for value in roots)}
    service = getattr(request.app.state, "evaluation_bounded_sandboxes", None)
    sandbox = ({"observed": True, "complete": not request.app.state.settings.work_enabled, "attempts": 0}
               if service is None else {"observed": True, "complete": not service.storage_failed, "attempts": service.attempts})
    return {"schema_version": 1, "environment_id": proof["environment_id"], "owner_id": proof["owner_id"],
            "namespace": row["namespace"], "case_id": binding["case_id"], "workflow_id": identifier,
            "complete": True, "run_ids": row["run_ids"], "provider": provider, "sandbox": sandbox,
            "counts": {key: row[key] for key in WORKFLOW_COUNTS}}


def attach_observers(app):
    """Attach only to an already configured evaluation_target.build_app instance."""

    @app.get("/api/v1/agent-evaluations/profiles/workflows/{workflow_id}/observations")
    async def readonly_effects(workflow_id: UUID, request: Request):
        return await workflow_effects(request, workflow_id)

    @app.get("/api/v1/agent-evaluations/profiles/runs/{mode}/{run_id}/observations")
    async def observations(mode: Literal["ask", "work", "workflow"], run_id: UUID, request: Request):
        proof = await environment(request)
        owner = proof["owner_id"]
        run = await owned(request.app.state.db, owner, mode, run_id)
        state = request.app.state.evaluation_profiles
        result = {"mode": mode, "run_id": run_id, "status": run["status"],
                  "controlled_model": state.root_summary(owner, mode, run_id),
                  "context_assertions": state.summary(owner, mode, run_id)}
        if mode == "work":
            # A bounded projection of this owned root, not a daemon-wide inventory or file observer.
            rows = await request.app.state.db.fetch(
                "SELECT s.state,count(*)::int AS count FROM work_sandbox_sessions s "
                "JOIN work_queue_runs r ON r.id=s.run_id WHERE s.run_id=%s AND r.owner_id=%s GROUP BY s.state",
                (run_id, owner))
            result["sandbox_sessions"] = {"complete": True, "states": {row["state"]: row["count"] for row in rows}}
        return result

    @app.get("/api/v1/agent-evaluations/profiles/capabilities")
    async def capabilities(request: Request):
        await environment(request)
        service = getattr(request.app.state, "evaluation_bounded_sandboxes", None)
        manager = {"ready": False}
        if service is not None:
            try:
                manager = await service.readiness()
            except Exception:  # noqa: BLE001 -- A read-only health failure blocks readiness without leaking manager credentials.
                manager = {"ready": False}
        return {"schema_version": 1, "fixture_profile": {"id": "controlled-v1", "version": 1},
                "observers": ["root-model-context-v1", "owned-sandbox-states-v1", "readonly-workflow-effects-v1"],
                "sandbox_limits": service.capability() if service else None, "work_manager": manager}
