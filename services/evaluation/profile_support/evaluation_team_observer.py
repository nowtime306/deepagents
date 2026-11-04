"""Closed target-only owner/run projection; no caller SQL, filesystem path or command."""

import hashlib
from uuid import UUID

from app.agent_evaluation_environment import environment
from app.errors import fail
from fastapi import Request

CONTRACT = "owned-team-native-effects-v2"


def capability(app, owner):
    import app.team_runtime as team
    import app.work_engine as engine
    import app.work_resources as resources

    audit = getattr(app.state, "evaluation_team_audit", None)
    if (
        audit is None
        or audit.failed
        or getattr(engine, "WORK_SHARED_BUDGET_GUARD", None)
        != "native-work-model-tool-v1"
        or getattr(team, "WORK_SHARED_BUDGET_GUARD", None)
        != "native-work-model-tool-v1"
        or getattr(resources, "TEAM_RESOURCE_CONTEXT_VERSION", None)
        != "work-member-resource-v1"
    ):
        return None
    return {
        "protocol": CONTRACT,
        "audit": "controlled-team-effects-v2",
        "native_guard": "native-work-model-tool-v1",
        "member_resource_context": "work-member-resource-v1",
        "definition_versions": {"AET-W05": 2, "AET-W06": 2},
        "case_state": {
            case: app.state.evaluation_profiles.case_summary(owner, case)
            for case in ("AET-W05", "AET-W06")
        },
    }


async def observe(request, identifier):
    proof = await environment(request)
    db, state = request.app.state.db, request.app.state.evaluation_profiles
    rows = await db.fetch(
        "SELECT r.id,r.task_id,r.owner_id,r.model_id,r.model_revision,r.task_revision,r.goal,r.status,r.error_code,"
        "r.token_limit,r.used_tokens,r.model_calls,r.usage_unknown,r.agent_snapshot,"
        "(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace,"
        "(SELECT coalesce(jsonb_agg(jsonb_build_object('id',t.id,'role_name',t.role_name,'version_id',t.version_id,"
        "'model_id',t.model_id,'status',t.status,'error_code',t.error_code,"
        "'tool_call_id',t.tool_call_id) ORDER BY t.created_at,t.id),"
        "'[]'::jsonb) FROM team_calls t WHERE t.owner_id=r.owner_id AND t.work_run_id=r.id) AS members,"
        "(SELECT coalesce(jsonb_agg(jsonb_build_object('call_id',u.call_id,'owner_id',u.owner_id,'run_id',u.run_id,"
        "'model_id',u.model_id,'model_revision',u.model_revision,'role_name',u.role_name,'team_call_id',u.team_call_id,"
        "'call_kind',u.call_kind,'status',u.status,'input_tokens',u.input_tokens,'output_tokens',u.output_tokens) "
        "ORDER BY u.created_at,u.call_id),'[]'::jsonb) FROM model_usage u "
        "WHERE u.owner_id=r.owner_id AND u.run_id=r.id) AS ledger,"
        "(SELECT coalesce(jsonb_agg(jsonb_build_object('id',s.id,'sandbox_id',s.sandbox_id,"
        "'state',s.state,'run_id',s.run_id) ORDER BY s.created_at,s.id),'[]'::jsonb) FROM work_sandbox_sessions s "
        "WHERE s.owner_id=r.owner_id AND s.run_id=r.id) AS sessions,"
        "(SELECT count(*) FROM operation_executions e WHERE e.run_id=r.id) AS operations,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=r.owner_id) AS owner_model_calls,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=r.owner_id) AS owner_work_runs,"
        "(SELECT count(*) FROM work_tasks WHERE owner_id=r.owner_id) AS owner_work_tasks,"
        "(SELECT count(*) FROM task_runs t JOIN conversations c ON c.id=t.conversation_id "
        "WHERE c.owner_id=r.owner_id) AS owner_ask_runs,"
        "(SELECT count(*) FROM workflow_runs f JOIN workflows w ON w.id=f.workflow_id "
        "WHERE w.owner_id=r.owner_id) AS owner_workflow_runs,"
        "(SELECT count(*) FROM work_sandbox_sessions WHERE owner_id=r.owner_id) AS owner_sandboxes,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=r.owner_id "
        "AND status IN ('PENDING','UNKNOWN')) AS unknown_usage,"
        "(SELECT count(*) FROM resource_operations WHERE owner_id=r.owner_id) AS owner_resource_runs "
        "FROM work_queue_runs r WHERE r.id=%s AND r.owner_id=%s",
        (identifier, proof["owner_id"]),
    )
    if not rows:
        fail(404, "EVALUATION_TEAM_RUN_NOT_FOUND", "固定评测任务不存在或无权访问")
    row = rows[0]
    row["goal_digest"] = hashlib.sha256(row.pop("goal").encode("utf-8")).hexdigest()
    snapshot = row.pop("agent_snapshot")
    pins = snapshot.get("team_pins", [])
    if len(pins) != 1 or pins[0].get("name") != "reviewer":
        fail(409, "EVALUATION_TEAM_BINDING_MISSING", "必须保留正式冻结的单成员团队")
    child = pins[0]["snapshot"]
    with state.lock:
        lead, member = (
            state.models.get(row["model_id"]),
            state.models.get(child["model_id"]),
        )
        if (
            not lead
            or not member
            or lead["case_id"] not in {"AET-W05", "AET-W06"}
            or lead
            != {
                "case_id": lead["case_id"],
                "role": "lead",
                "owner_id": str(proof["owner_id"]),
            }
            or member
            != {
                "case_id": lead["case_id"],
                "role": "reviewer",
                "owner_id": str(proof["owner_id"]),
            }
        ):
            fail(
                403,
                "EVALUATION_TEAM_MODEL_NOT_SEALED",
                "只允许同案例同所有者的已密封父子模型",
            )
    audit = getattr(request.app.state, "evaluation_team_audit", None)
    if audit is None or str(row["namespace"]) != str(proof["sandbox_namespace"]):
        fail(
            503, "EVALUATION_TEAM_OBSERVER_MISSING", "实际工具审计或命名空间证据未就绪"
        )
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "environment_id": proof["environment_id"],
        "owner_id": proof["owner_id"],
        "namespace": row.pop("namespace"),
        "case_id": lead["case_id"],
        "binding": {
            "agent_id": snapshot["id"],
            "agent_version_id": snapshot["published_version_id"],
            "reviewer_agent_id": child["id"],
            "reviewer_version_id": pins[0]["version_id"],
            "reviewer_model_id": child["model_id"],
        },
        "members": row.pop("members"),
        "ledger": row.pop("ledger"),
        "sessions": row.pop("sessions"),
        "effects": {
            key: row.pop(key)
            for key in (
                "operations",
                "owner_model_calls",
                "owner_work_runs",
                "owner_work_tasks",
                "owner_ask_runs",
                "owner_workflow_runs",
                "owner_sandboxes",
                "unknown_usage",
                "owner_resource_runs",
            )
        },
        "run": row,
        "audit": audit.projection(proof["owner_id"], identifier),
        "provider": state.case_summary(proof["owner_id"], lead["case_id"]),
    }
    return result


def attach(app):
    @app.get("/api/v1/agent-evaluations/profiles/team-runs/{run_id}/observations")
    async def observations(run_id: UUID, request: Request):
        return await observe(request, run_id)
