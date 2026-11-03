"""Fixed A01 scheduler/worker hooks; no arbitrary schedule, SQL or execution inputs."""

import json
from uuid import UUID, uuid5

from app.agent_evaluation_environment import environment
from app.ask_worker import AskWorker
from app.automation_scheduler import AutomationScheduler
from app.errors import fail
from app.permissions import require_account
from fastapi import Request
from pydantic import BaseModel, ConfigDict, field_validator

A01_BUDGET = {"model_calls": 4, "token_limit": 10000}
GOAL = "整理虚拟日报"


def fixed_schedule(row):
    return (row["kind"] == "once" and row["mode"] == "ask" and row["goal"] == GOAL
            and type(row["budget"].get("model_calls")) is int and type(row["budget"].get("token_limit")) is int
            and row["budget"] == A01_BUDGET and row["notify_channel_ids"] == [] and row["network_domains"] == [])


class ControlledAskWorker(AskWorker):
    """Determine phase from the claimed genuine queue row, then execute the real worker."""

    def __init__(self, *args, profile_state, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile_state = profile_state

    async def execute(self, run, connection):
        with self.profile_state.lock:
            binding = self.profile_state.models.get(run["model_id"])
        if binding and binding["case_id"] == "AET-A01":
            rows = await self.database.fetch(
                "SELECT r.origin,r.automation_occurrence_id,c.owner_id,c.agent_version_id,"
                "o.ask_run_id,a.agent_version_id AS target_version,a.owner_id AS target_owner,"
                "a.kind,a.mode,a.goal,a.budget,a.notify_channel_ids,a.network_domains "
                "FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
                "LEFT JOIN automation_occurrences o ON o.id=r.automation_occurrence_id "
                "LEFT JOIN automations a ON a.id=o.automation_id "
                "WHERE r.id=%s AND r.lease_token=%s AND r.lease_until>now() AND r.status='RUNNING'",
                (run["id"], run["lease_token"]))
            row = rows[0] if rows else None
            valid = bool(row and binding["owner_id"] == str(row["owner_id"]) and row["agent_version_id"])
            phase = "draft"
            if valid and row["origin"] == "schedule":
                valid = (str(row["ask_run_id"]) == str(run["id"]) and row["target_owner"] == row["owner_id"]
                         and row["target_version"] == row["agent_version_id"] and fixed_schedule(row))
                phase = "occurrence"
            elif valid:
                valid = row["origin"] == "interactive" and row["automation_occurrence_id"] is None
            if not valid:
                await self.database.event(run["id"], "run.failed", {}, status="FAILED", error="EVALUATION_SOURCE_INVALID",
                                          lease_token=run["lease_token"])
                return
            self.profile_state.bind_a01_phase(row["owner_id"], run["id"], phase)
        await super().execute(run, connection)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_run_id: str

    @field_validator("source_run_id")
    @classmethod
    def identifier(cls, value):
        return str(UUID(value))


async def owned_source(request, automation_id, source_run_id, *, execute=False):
    proof = await environment(request)
    owner, db = proof["owner_id"], request.app.state.db
    for permission in ("automation.read", "chat.read", *(("evaluation.run", "automation.execute", "automation.manage", "chat.run") if execute else ())):
        await require_account(db, owner, permission)
    if request.app.state.settings.automation_enabled:
        fail(503, "EVALUATION_SCHEDULER_UNBOUNDED", "固定评测目标不得同时运行全局自动调度")
    rows = await db.fetch(
        "SELECT a.id,a.agent_version_id,a.kind,a.mode,a.goal,a.budget,a.notify_channel_ids,a.network_domains,"
        "a.enabled,a.next_due_at,a.completed_at,a.paused_at,a.revision,"
        "r.model_id,r.status AS source_status,r.origin,c.agent_version_id AS source_version,"
        "v.snapshot->>'model_id' AS target_model "
        "FROM automations a JOIN task_runs r ON r.id=%s JOIN conversations c "
        "ON c.id=r.conversation_id AND c.owner_id=a.owner_id "
        "JOIN agent_versions v ON v.id=a.agent_version_id AND v.owner_id=a.owner_id "
        "WHERE a.id=%s AND a.owner_id=%s AND a.deleted_at IS NULL",
        (UUID(source_run_id), automation_id, owner))
    if not rows:
        fail(404, "NOT_FOUND", "固定自动任务或来源对话不存在")
    row = rows[0]
    state = request.app.state.evaluation_profiles
    with state.lock:
        binding = state.models.get(row["model_id"])
        valid_model = (binding and binding["case_id"] == "AET-A01" and binding["role"] == "lead"
                       and binding["owner_id"] == str(owner) and row["target_model"] == row["model_id"])
    if (not valid_model or row["source_status"] != "SUCCEEDED" or row["origin"] != "interactive"
            or row["source_version"] != row["agent_version_id"] or not fixed_schedule(row)):
        fail(403, "EVALUATION_AUTOMATION_NOT_REGISTERED", "只允许真实固定对话工具创建的本人一次性任务")
    messages = await db.fetch("SELECT content FROM conversation_messages WHERE run_id=%s AND role='tool' ORDER BY created_at,id",
                              (UUID(source_run_id),))
    matches = []
    for message in messages:
        data = message["content"].get("data", {})
        if data.get("name") != "create_automation_draft":
            continue
        text = data.get("content")
        if not isinstance(text, str) or len(text) > 4096:
            fail(403, "EVALUATION_SOURCE_INVALID", "固定工具证据不完整")
        try:
            output = json.loads(text)
        except ValueError:
            fail(403, "EVALUATION_SOURCE_INVALID", "固定工具证据无效")
        if not isinstance(output, dict):
            fail(403, "EVALUATION_SOURCE_INVALID", "固定工具证据格式无效")
        if output.get("type") == "automation_draft" and output.get("id") == str(automation_id) and output.get("enabled") is False:
            if (not isinstance(data.get("tool_call_id"), str)
                    or str(uuid5(UUID(source_run_id), data["tool_call_id"])) != str(automation_id)):
                fail(403, "EVALUATION_SOURCE_INVALID", "工具调用与草稿标识不匹配")
            matches.append(output)
    root = state.root_summary(owner, "ask", source_run_id)
    if len(matches) != 1 or not root.get("recorded") or root.get("complete") is not True or root.get("provider_calls") != 3:
        fail(403, "EVALUATION_SOURCE_INVALID", "缺少完整的真实对话工具草稿关联")
    if execute:
        case = state.case_summary(owner, "AET-A01")
        if (case["complete"] is not True or case["calls"] != 3 or case["tokens"] != 45
                or case["roots"] != [{"mode": "ask", "run_id": source_run_id, "phase": "draft", "calls": 3, "tokens": 45}]):
            fail(409, "EVALUATION_AUTOMATION_BUDGET_UNKNOWN", "调度前必须核对整个用例的已知预算和唯一来源")
    return proof, row


async def project(request, automation_id, source_run_id):
    proof, row = await owned_source(request, automation_id, source_run_id)
    db, owner = request.app.state.db, proof["owner_id"]
    occurrences = await db.fetch(
        "SELECT id,status,ask_run_id,target_id,result_status,error_code FROM automation_occurrences "
        "WHERE automation_id=%s AND owner_id=%s ORDER BY created_at,id LIMIT 3", (automation_id, owner))
    if len(occurrences) > 1:
        fail(409, "EVALUATION_OCCURRENCE_LIMIT", "固定一次性任务出现多次执行")
    identifiers = [UUID(source_run_id), *[item["ask_run_id"] for item in occurrences if item["ask_run_id"]]]
    native = await db.fetch(
        "SELECT r.id AS run_id,r.status,count(u.call_id) AS calls,"
        "count(u.call_id) FILTER(WHERE u.status='KNOWN') AS reported_calls,"
        "count(u.call_id) FILTER(WHERE u.status='UNKNOWN') AS unknown_calls,"
        "count(u.call_id) FILTER(WHERE u.status='PENDING') AS pending_calls,"
        "coalesce(sum(u.input_tokens) FILTER(WHERE u.status='KNOWN'),0) AS input_tokens,"
        "coalesce(sum(u.output_tokens) FILTER(WHERE u.status='KNOWN'),0) AS output_tokens "
        "FROM task_runs r JOIN conversations c ON c.id=r.conversation_id LEFT JOIN model_usage u "
        "ON u.run_id=r.id AND u.owner_id=c.owner_id AND u.mode='ask' "
        "WHERE r.id=ANY(%s::uuid[]) AND c.owner_id=%s GROUP BY r.id,r.status ORDER BY r.id", (identifiers, owner))
    counts = (await db.fetch(
        "SELECT (SELECT count(*) FROM task_runs r JOIN conversations c ON c.id=r.conversation_id "
        "WHERE c.owner_id=%s AND r.status IN ('PENDING','RUNNING')) AS active_ask_runs,"
        "(SELECT count(*) FROM work_queue_runs WHERE owner_id=%s) AS work_runs,"
        "(SELECT count(*) FROM model_usage WHERE owner_id=%s AND status IN ('PENDING','UNKNOWN')) AS unsettled_usage",
        (owner, owner, owner)))[0]
    return {"schema_version": 1, "environment_id": proof["environment_id"], "owner_id": owner,
            "namespace": proof["sandbox_namespace"], "automation_id": automation_id, "source_run_id": source_run_id,
            "agent_version_id": row["agent_version_id"], "enabled": row["enabled"], "next_due_at": row["next_due_at"],
            "completed_at": row["completed_at"], "revision": row["revision"], "budget": row["budget"],
            "occurrences": occurrences, "native": [{**item, "input_tokens": int(item["input_tokens"]),
                                                      "output_tokens": int(item["output_tokens"])} for item in native],
            "provider": request.app.state.evaluation_profiles.case_summary(owner, "AET-A01"), "counts": counts}


def attach_automations(app):
    @app.get("/api/v1/agent-evaluations/profiles/automations/{automation_id}/observations")
    async def observe(automation_id: UUID, source_run_id: UUID, request: Request):
        return await project(request, automation_id, str(source_run_id))

    @app.post("/api/v1/agent-evaluations/profiles/automations/{automation_id}/step")
    async def step(automation_id: UUID, body: Source, request: Request):
        await owned_source(request, automation_id, body.source_run_id, execute=True)
        await AutomationScheduler(request.app.state).schedule(automation_id)
        return await project(request, automation_id, body.source_run_id)
