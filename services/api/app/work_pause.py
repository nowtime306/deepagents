"""Persist a pause at model boundaries instead of terminating in-flight effects."""

from langchain.agents.middleware import AgentMiddleware
from langgraph.types import interrupt

from app.work_worker import LeaseLost


class WorkPauseMiddleware(AgentMiddleware):
    def __init__(self, database, run: dict) -> None:
        self.database, self.run = database, run

    async def check(self) -> None:
        rows = await self.database.fetch(
            "SELECT pause_requested FROM work_queue_runs WHERE id=%s AND lease_token=%s "
            "AND status='RUNNING' AND lease_until>now() AND NOT cancel_requested",
            (self.run["id"], self.run["lease_token"]),
        )
        if not rows:
            raise LeaseLost("Work lease unavailable at model boundary")
        if rows[0]["pause_requested"]:
            interrupt({"kind": "work_pause"})

    async def abefore_model(self, state, runtime) -> None:
        await self.check()

    async def aafter_model(self, state, runtime) -> None:
        await self.check()
