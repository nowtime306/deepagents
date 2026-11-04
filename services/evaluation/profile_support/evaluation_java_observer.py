"""Private target-only draft: registered-task observations, never arbitrary SQL/path/run inputs."""

import asyncio
import copy
import hashlib
import json
from uuid import UUID

from app.agent_evaluation_environment import environment
from app.errors import fail
from app.permissions import require_account
from evaluation_java_verifier import ORIGINAL_SHA, validate_baseline_report, validate_pair
from fastapi import Request
from opensandbox.models.sandboxes import SandboxFilter

PATH = "/api/v1/agent-evaluations/profiles/java-verifiers"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


async def native(database, owner, run_id, model_id):
    rows = await database.fetch(
        "SELECT call_id,model_id,status,input_tokens,output_tokens FROM model_usage "
        "WHERE owner_id=%s AND run_id=%s AND mode='work' ORDER BY call_id LIMIT 7", (owner, UUID(run_id)))
    known = [row for row in rows if row["status"] == "KNOWN"]
    valid = len(rows) <= 6 and all(row["model_id"] == model_id for row in rows)
    tokens = all(type(row[key]) is int and row[key] >= 0 for row in known for key in ("input_tokens", "output_tokens"))
    counts = {"calls": len(rows), "reported_calls": len(known),
              "unknown_calls": sum(row["status"] == "UNKNOWN" for row in rows),
              "pending_calls": sum(row["status"] == "PENDING" for row in rows),
              "input_tokens": sum(row["input_tokens"] for row in known) if tokens and (known or not rows) else None,
              "output_tokens": sum(row["output_tokens"] for row in known) if tokens and (known or not rows) else None}
    valid = valid and tokens and all(row["status"] == "KNOWN" for row in rows)
    return counts, valid


async def inventory(sandboxes, namespace):
    try:
        manager = await sandboxes._get_manager()
        page = await asyncio.wait_for(manager.list_sandbox_infos(SandboxFilter(
            metadata={"namespace": namespace}, page=1, page_size=3)), timeout=5)
        pagination = page.pagination
        complete = (pagination.page == 1 and type(pagination.total_items) is int and pagination.total_items >= 0
                    and pagination.has_next_page is False and len(page.sandbox_infos) == pagination.total_items
                    and all((item.metadata or {}).get("namespace") == namespace for item in page.sandbox_infos))
        return {"complete": complete, "count": pagination.total_items if complete else None}
    except Exception:  # noqa: BLE001 -- No credentials, manager addresses or raw error bodies enter observations.
        return {"complete": False, "count": None}


async def observe(request, task_id):
    proof = await environment(request)
    db, owner = request.app.state.db, proof["owner_id"]
    await require_account(db, owner, "work.read")
    service = getattr(request.app.state, "evaluation_java_verifier", None)
    if service is None or service.storage_failed or not service.ready:
        fail(503, "EVALUATION_VERIFIER_UNAVAILABLE", "固定验证器状态不可观察")
    async with service.lock:
        job = copy.deepcopy(service.jobs.get(str(task_id)))
    if not job or job["owner_id"] != str(owner) or job["namespace"] != str(proof["sandbox_namespace"]):
        fail(404, "NOT_FOUND", "本人已封存的验证任务不存在")
    source = await db.fetch(
        "SELECT r.id,r.task_id,r.model_id,r.status,r.result,(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace,"
        "(SELECT id FROM work_snapshots WHERE run_id=r.id ORDER BY created_at DESC,id DESC LIMIT 1) AS snapshot_id "
        "FROM work_queue_runs r JOIN work_tasks t ON t.id=r.task_id AND t.owner_id=r.owner_id "
        "WHERE r.id=%s AND r.owner_id=%s", (UUID(job["source_run_id"]), owner))
    task = await db.fetch("SELECT id FROM work_tasks WHERE id=%s AND owner_id=%s", (task_id, owner))
    if not source or not task:
        fail(404, "NOT_FOUND", "已封存来源或验证任务不存在")
    source = source[0]
    if (str(source["namespace"]) != job["namespace"] or str(source["task_id"]) == str(task_id)
            or source["model_id"] != job["source_model_id"] or str(source["snapshot_id"]) != job["source_snapshot_id"]):
        fail(409, "EVALUATION_VERIFIER_BINDING_CHANGED", "封存来源或快照关联已变化")
    targets = await db.fetch("SELECT id,model_id,status,result FROM work_queue_runs WHERE task_id=%s AND owner_id=%s ORDER BY created_at,id LIMIT 3",
                             (task_id, owner))
    verifier = targets[0] if len(targets) == 1 else None
    run_id = job["verifier_run_id"]
    source_usage, source_known = await native(db, owner, job["source_run_id"], job["source_model_id"])
    target_usage, target_known = (await native(db, owner, run_id, job["source_model_id"])) if run_id else (None, False)
    profiles = request.app.state.evaluation_profiles
    with profiles.lock:
        sealed_model = profiles.models.get(job["source_model_id"]) == {"owner_id": str(owner), "case_id": "AET-W02", "role": "lead"}
        zero_provider_root = run_id is not None and (str(owner), "work", run_id) not in profiles.roots
    case = profiles.case_summary(owner, "AET-W02")
    case_provider = {key: case[key] for key in ("observed", "complete", "calls", "tokens", "pending", "unknown_roots")}
    expected_source_usage = {"calls": 3, "reported_calls": 3, "unknown_calls": 0, "pending_calls": 0, "input_tokens": 30, "output_tokens": 15}
    expected_roots = [{"mode": "work", "run_id": job["source_run_id"], "phase": None, "calls": 3, "tokens": 45}]
    case_provider["complete"] = bool(case_provider["complete"] and digest(case["roots"]) == digest(expected_roots))
    valid_source = (source["status"] == "COMPLETED" and source_known and sealed_model
                    and digest(source_usage) == digest(expected_source_usage))
    complete = valid_source and case["complete"]
    if job["phase"] == "REGISTERED":
        complete = complete and not targets and run_id is None
    else:
        expected_result = {"case_id": "AET-W02", "verifier_run_id": run_id, "source_run_id": job["source_run_id"],
                           "artifact_id": job["artifact_id"], "source_sha256": job["source_sha256"],
                           "baseline_verification": job.get("baseline_report"), "verification": job.get("report")}
        try:
            validate_baseline_report(job.get("baseline_report"))
            validate_pair(job["baseline_report"], job["report"], job["source_sha256"])
            paired = job.get("baseline_source_sha256") == ORIGINAL_SHA
        except (ValueError, TypeError, KeyError):
            paired = False
        complete = (complete and paired and job["phase"] == "COMPLETED" and verifier is not None
                    and str(verifier["id"]) == run_id and verifier["model_id"] == job["source_model_id"] and verifier["status"] == "COMPLETED"
                    and target_known and isinstance(job.get("report"), dict) and digest(verifier["result"]) == digest(expected_result))
    identifiers = [UUID(job["source_run_id"]), *([UUID(run_id)] if run_id else [])]
    sessions = await db.fetch(
        "SELECT s.id AS session_id,s.run_id,s.owner_id,(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace,"
        "s.sandbox_id,s.state FROM work_sandbox_sessions s JOIN work_queue_runs r ON r.id=s.run_id "
        "WHERE s.run_id=ANY(%s::uuid[]) AND s.owner_id=%s AND r.owner_id=%s ORDER BY s.run_id,s.id LIMIT 3", (identifiers, owner, owner))
    active = await db.fetch("SELECT count(*) AS count FROM work_queue_runs WHERE owner_id=%s AND status NOT IN ('COMPLETED','FAILED','CANCELLED')", (owner,))
    operations = await db.fetch("SELECT count(*) AS count FROM operation_executions e JOIN work_queue_runs r ON r.id=e.run_id "
                                "WHERE r.owner_id=%s", (owner,))
    scope = (await db.fetch(
        "SELECT (SELECT count(*) FROM work_queue_runs WHERE owner_id=%s) AS accepted_runs,"
        "(SELECT count(*) FROM work_tasks WHERE owner_id=%s) AS accepted_tasks,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s AND id=ANY(%s::uuid[])) AS registered_runs,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=%s AND status IN ('PENDING','UNKNOWN')) AS unsettled_usage",
        (owner, owner, owner, identifiers, owner)))[0]
    expected_runs = {job["source_run_id"]} | ({run_id} if run_id else set())
    released = (len(sessions) == len(expected_runs) and {str(row["run_id"]) for row in sessions} == expected_runs
                and len({str(row["session_id"]) for row in sessions}) == len(sessions)
                and len({row["sandbox_id"] for row in sessions}) == len(sessions)
                and all(row["state"] == "RELEASED" and row["sandbox_id"] and str(row["owner_id"]) == str(owner)
                        and str(row["namespace"]) == job["namespace"] for row in sessions))
    namespace_inventory = await inventory(service.sandboxes, job["namespace"])
    closed_scope = (scope["accepted_tasks"] == 2 and scope["accepted_runs"] == scope["registered_runs"] == len(identifiers)
                    and scope["unsettled_usage"] == 0)
    complete = bool(complete and closed_scope and released and case_provider["complete"] and active[0]["count"] == 0 and operations[0]["count"] == 0
                    and not service.sandboxes.storage_failed and namespace_inventory["complete"] is True)
    zero = bool(complete and run_id and zero_provider_root and target_usage and target_usage["calls"] == 0)
    return {"schema_version": 1, "case_id": "AET-W02", "environment_id": proof["environment_id"], "owner_id": owner,
            "namespace": job["namespace"], "phase": job["phase"], "complete": complete,
            "binding": {"source_run_id": job["source_run_id"], "source_task_id": source["task_id"],
                "source_snapshot_id": job["source_snapshot_id"], "source_model_id": job["source_model_id"],
                "artifact_id": job["artifact_id"], "source_sha256": job["source_sha256"], "verifier_task_id": str(task_id),
                "verifier_run_id": run_id, "baseline_source_sha256": job["baseline_source_sha256"], "program_sha256": job["program_sha256"], "launcher_sha256": job["isolation_sha256"], "tests_sha256": job["tests_sha256"]},
            "zero_verifier": {"observed": zero, "confirmed": zero}, "native": {"source": source_usage, "verifier": target_usage},
            "case_provider": case_provider, "sessions": sessions,
            "namespace_inventory": namespace_inventory}


def attach_observer(app):
    @app.get(PATH + "/{task_id}/observations")
    async def owned_observation(task_id: UUID, request: Request):
        return await observe(request, task_id)
