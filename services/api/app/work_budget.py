"""Account for model calls from both the lead agent and delegated subagents."""

from langchain_core.callbacks import AsyncCallbackHandler

from app.permissions import require_account
from app.storage import Database
from app.usage_details import reported_usage


class WorkBudgetExceeded(RuntimeError):
    code = "WORK_BUDGET_EXCEEDED"


class WorkUsageUnavailable(RuntimeError):
    code = "WORK_USAGE_UNAVAILABLE"


class WorkBudget(AsyncCallbackHandler):
    """Stop further model/tool work when usage is unavailable or exceeds the budget."""

    raise_error = True

    def __init__(self, database: Database, run: dict) -> None:
        self.database, self.run = database, run

    async def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        await require_account(self.database, self.run["owner_id"], "work.execute")
        rows = await self.database.fetch(
            "UPDATE work_queue_runs SET model_calls=model_calls+1 WHERE id=%s AND lease_token=%s "
            "AND status='RUNNING' AND lease_until>now() AND NOT cancel_requested AND NOT usage_unknown "
            "AND model_calls<step_limit AND used_tokens<token_limit RETURNING id",
            (self.run["id"], self.run["lease_token"]),
        )
        if not rows:
            raise WorkBudgetExceeded("Model budget or run lease unavailable")

    async def on_llm_end(self, response, **kwargs) -> None:
        usage = reported_usage(response)
        if usage is None:
            await self.database.execute(
                "UPDATE work_queue_runs SET usage_unknown=true WHERE id=%s AND lease_token=%s AND status='RUNNING'",
                (self.run["id"], self.run["lease_token"]),
            )
            raise WorkUsageUnavailable("Provider must return token usage for Work execution")
        total = usage["total_tokens"]
        rows = await self.database.fetch(
            "UPDATE work_queue_runs SET used_tokens=CASE WHEN resource_budgeted THEN used_tokens "
            "ELSE used_tokens+%s END WHERE id=%s AND lease_token=%s "
            "AND status='RUNNING' AND lease_until>now() RETURNING used_tokens,token_limit",
            (total, self.run["id"], self.run["lease_token"]),
        )
        if not rows or rows[0]["used_tokens"] > rows[0]["token_limit"]:
            raise WorkBudgetExceeded("Observed token budget exceeded")
