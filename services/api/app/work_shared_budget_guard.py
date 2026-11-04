"""Private proposed Work guard: live native budget before model/tool effects."""

from langchain.agents.middleware import AgentMiddleware

from app.errors import fail

CONTRACT = "native-work-model-tool-v1"


async def require_budget(database, run):
    """Read authoritative same-root usage; do not reset or refund unknown calls."""
    rows = await database.fetch(
        "SELECT r.token_limit,r.used_tokens,r.usage_unknown,"
        "coalesce(sum(u.input_tokens+u.output_tokens) FILTER(WHERE u.status='KNOWN'),0) AS known_tokens,"
        "count(u.call_id) FILTER(WHERE u.status IN ('PENDING','UNKNOWN')) AS unsettled "
        "FROM work_queue_runs r LEFT JOIN model_usage u ON u.run_id=r.id AND u.owner_id=r.owner_id "
        "WHERE r.id=%s AND r.owner_id=%s AND r.lease_token=%s AND r.status='RUNNING' "
        "AND r.lease_until>now() AND NOT r.cancel_requested GROUP BY r.id",
        (run["id"], run["owner_id"], run["lease_token"]),
    )
    if not rows:
        fail(409, "WORK_LEASE_LOST", "任务已停止、取消或租约失效，未执行新操作")
    row = rows[0]
    if row["usage_unknown"] or row["unsettled"]:
        fail(409, "WORK_USAGE_UNAVAILABLE", "本轮用量存在待核对调用，未执行新操作")
    if max(row["used_tokens"], row["known_tokens"]) >= row["token_limit"]:
        fail(422, "WORK_BUDGET_EXCEEDED", "本轮共享 Token 预算已达限，未执行新操作")


class WorkSharedBudgetGuard(AgentMiddleware):
    """Check actual lead/member model and tool entry, not streaming/export callbacks."""

    def __init__(self, database, run):
        self.database, self.run = database, run

    async def abefore_model(self, state, runtime):
        await require_budget(self.database, self.run)

    async def awrap_tool_call(self, request, handler):
        try:
            await require_budget(self.database, self.run)
        except Exception as error:
            callback = getattr(self.database, "evaluation_tool_budget_denial", None)
            if callback is not None:
                callback(self.run, request.tool_call, error)
            raise
        return await handler(request)
