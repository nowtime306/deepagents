"""Owned F05 observations and case-specific counting; no free commands or mutation endpoint."""

import json
from uuid import UUID

from evaluation_flow_code_contract import CASE, LIMIT, definition
from evaluation_sandbox_cap import BoundedSandboxes
from fastapi import Request

from app.agent_evaluation_environment import environment
from app.durable_sandboxes import DurableSandboxes
from app.errors import fail
from app.permissions import require_account


def bound(profiles, model, owner):
    with profiles.lock:
        return profiles.models.get(model) == {"case_id": CASE, "role": "lead", "owner_id": str(owner)}


class CodeSandboxes(BoundedSandboxes):
    maximum = LIMIT  # State reload may contain the F05-specific count; admission below remains per case.
    def __init__(self, database, config, path, profiles, *, enabled=False):
        self.enabled = False  # A bare boolean is not an authenticated fixed-target activation.
        self.deadline_guard = None
        self.profiles = profiles
        super().__init__(database, config, path)

    def activate(self, guard, execution_limits, *, reviewed=None):
        from evaluation_f05_deadline_guard import F05DeadlineGuard

        self.enabled, self.deadline_guard = False, None
        if not isinstance(guard, F05DeadlineGuard):
            return False
        capability = guard.capability()
        context = guard.identity["suite_context"]
        reviewed = reviewed or {}
        pins = reviewed.get("f05_deadline", {})
        expected = {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}
        if (context.get("case_id") != CASE
                or type(reviewed.get("sandbox_bounds", {}).get(CASE)) is not int or reviewed["sandbox_bounds"][CASE] != LIMIT
                or type(pins.get("definition_version")) is not int or pins["definition_version"] != 2
                or any(pins.get(key) != context[key] for key in ("catalog_digest", "case_definition_digest", "rules_id"))
                or type(execution_limits.get("schema_version")) is not int or execution_limits["schema_version"] != 1
                or execution_limits.get("enforcement") != "controlled-model-factory-v1"
                or execution_limits.get("scope") != "resource-run-root"
                or json.dumps(execution_limits.get("profiles", {}).get(CASE), sort_keys=True) != json.dumps(expected, sort_keys=True)
                or capability["installed"] is not True or capability["phase"] not in {"AWAITING_RECORD", "ACTIVE"}):
            return False
        self.deadline_guard, self.enabled = guard, True
        return True

    async def reserve(self, run_id, token, kind):
        if kind != "coding":
            raise ValueError("Only Coding is allowed")
        rows = await self.database.fetch(
            "SELECT r.owner_id,r.model_id,(SELECT f.snapshot FROM work_run_workflows w "
            "JOIN workflow_runs f ON f.id=w.workflow_run_id JOIN workflows b ON b.id=f.workflow_id "
            "WHERE w.run_id=r.id AND b.owner_id=r.owner_id) AS snapshot "
            "FROM work_queue_runs r WHERE r.id=%s AND r.lease_token=%s "
            "AND r.lease_until>now() AND r.status='RUNNING'", (run_id, token))
        with self.profiles.lock:
            binding = dict(self.profiles.models.get(rows[0]["model_id"], {})) if rows else {}
        fixed = binding.get("case_id") == CASE
        if fixed and not self.enabled:
            raise RuntimeError("F05 ten-creation profile remains blocked pending complete review")
        if fixed:
            self.deadline_guard.check("sandbox")
            if (binding != {"case_id": CASE, "role": "lead", "owner_id": str(rows[0]["owner_id"])}
                    or str(rows[0]["owner_id"]) != self.deadline_guard.identity["owner_id"]):
                raise ValueError("F05 sandbox owner differs from the authenticated target")
            roots = await self.database.fetch("SELECT count(*) AS count FROM work_queue_runs WHERE owner_id=%s", (rows[0]["owner_id"],))
            if not 1 <= roots[0]["count"] <= 3:
                raise ValueError("F05 fixed three-root target bound exceeded")
        if fixed and json.dumps((rows[0]["snapshot"] or {}).get("definition"), sort_keys=True) != json.dumps(definition(), sort_keys=True):
            raise ValueError("F05 sandbox bound requires its exact fixed graph")
        maximum = LIMIT if fixed else 2
        async with self.creation_lock:
            if fixed:
                active = await self.database.fetch("SELECT count(*) AS count FROM work_sandbox_sessions WHERE state<>'RELEASED'")
                if active[0]["count"]:
                    return None
            if self.storage_failed or self.attempts >= maximum:
                raise RuntimeError("Evaluation sandbox creation budget exhausted")
            self.attempts += 1
            self.save_bound()
            result = await DurableSandboxes.reserve(self, run_id, token, kind)
            if result is None:
                self.attempts -= 1
                self.save_bound()
            return result

    def capability(self):
        enabled = self.enabled and self.deadline_guard.capability()["phase"] in {"AWAITING_RECORD", "ACTIVE"}
        return {**super().capability(), "max_created": 2, "fixed_cases": {CASE: {"enabled": enabled, "max_created": LIMIT, "max_concurrent": 1,
                "material": "fixed-code-v2", "counterfactual": "reject-first-approval"}}}


async def observe(request, run_id):
    proof = await environment(request)
    await require_account(request.app.state.db, proof["owner_id"], "workflows.read")
    await require_account(request.app.state.db, proof["owner_id"], "work.read")
    rows = await request.app.state.db.fetch(
        "SELECT r.id,r.task_id,r.owner_id,r.model_id,r.status,r.lease_until,r.lease_token,r.cancel_requested,"
        "w.workflow_run_id,f.workflow_id,f.snapshot,f.status AS workflow_status,"
        "(SELECT id FROM work_snapshots s WHERE s.run_id=r.id ORDER BY created_at DESC,id DESC LIMIT 1) AS snapshot_id,"
        "(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace "
        "FROM work_queue_runs r JOIN work_run_workflows w ON w.run_id=r.id JOIN workflow_runs f ON f.id=w.workflow_run_id "
        "JOIN workflows b ON b.id=f.workflow_id WHERE r.id=%s AND r.owner_id=%s AND b.owner_id=r.owner_id",
        (run_id, proof["owner_id"]))
    if not rows:
        fail(404, "NOT_FOUND", "固定代码运行不存在或无权访问")
    row = rows[0]
    profiles = request.app.state.evaluation_profiles
    if not bound(profiles, row["model_id"], proof["owner_id"]) or json.dumps(row["snapshot"]["definition"], sort_keys=True) != json.dumps(definition(), sort_keys=True):
        fail(403, "EVALUATION_PROFILE_UNAVAILABLE", "仅允许封定的固定代码流程")
    sessions = await request.app.state.db.fetch(
        "SELECT id,run_id,state,sandbox_id FROM work_sandbox_sessions WHERE owner_id=%s ORDER BY created_at,id LIMIT 11",
        (proof["owner_id"],))
    counts = (await request.app.state.db.fetch(
        "SELECT (SELECT count(*) FROM work_queue_runs WHERE owner_id=%s) AS work_runs,"
        "(SELECT count(*) FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id WHERE w.owner_id=%s) AS workflow_runs,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=%s) AS model_calls,"
        "(SELECT count(*) FROM operation_executions e JOIN work_queue_runs r ON r.id=e.run_id WHERE r.owner_id=%s "
        "AND e.status IN ('STARTED','UNKNOWN')) AS unknown_operations,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s AND status IN ('PENDING','RUNNING','CLOSING')) AS active_runs",
        (proof["owner_id"],) * 5))[0]
    provider = profiles.case_summary(proof["owner_id"], CASE)
    service = request.app.state.evaluation_bounded_sandboxes
    from evaluation_java_observer import inventory as namespace_inventory

    inventory = await namespace_inventory(service, str(row["namespace"]))
    return {"schema_version": 2, "case_id": CASE, "environment_id": proof["environment_id"], "owner_id": proof["owner_id"],
            "namespace": row["namespace"], "run_id": row["id"], "task_id": row["task_id"], "workflow_id": row["workflow_id"],
            "workflow_run_id": row["workflow_run_id"], "workflow_status": row["workflow_status"], "snapshot_id": row["snapshot_id"],
            "lease_free": row["lease_token"] is None and row["lease_until"] is None, "cancel_requested": row["cancel_requested"],
            "status": row["status"], "counts": counts, "sessions_complete": len(sessions) <= LIMIT, "sessions": sessions,
            "provider": {key: provider[key] for key in ("observed", "complete", "calls", "tokens", "pending", "unknown_roots")},
            "sandbox": {"attempts": service.attempts, "storage_healthy": not service.storage_failed,
                        "inventory": inventory}}


def attach(app):
    @app.get("/api/v1/agent-evaluations/profiles/code-workflows/{run_id}/observations")
    async def observations(run_id: UUID, request: Request):
        return await observe(request, run_id)
