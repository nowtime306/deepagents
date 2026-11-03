"""Fixed, owner-scoped observations for the test harness only; no scripts/SQL/path inputs."""

from typing import Literal
from uuid import UUID

from app.agent_evaluation_environment import environment
from app.resource_trace_api import owned
from fastapi import Request


def attach_observers(app):
    """Attach only to an already configured evaluation_target.build_app instance."""

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
                "observers": ["root-model-context-v1", "owned-sandbox-states-v1"],
                "sandbox_limits": service.capability() if service else None, "work_manager": manager}
