import asyncio
from types import SimpleNamespace
from uuid import uuid4

import evaluation_observers
import httpx
from app.resource_runtime import ResourceRun, active_resources
from evaluation_target import ProfileState, model_factory
from fastapi import FastAPI
from langchain_core.messages import HumanMessage


def test_observation_does_not_mix_same_uuid_from_another_mode(monkeypatch):
    owner, identifier, state = uuid4(), uuid4(), ProfileState()
    state.register_model("controlled", "AET-A02", "lead", owner)
    config = SimpleNamespace(id="controlled", model="eval::AET-A02::v1", name="lead")
    for mode in ("ask", "workflow"):
        token = active_resources.set(ResourceRun(None, owner, mode, identifier))
        try:
            model_factory(config, state).invoke([HumanMessage(content=mode + " scoped context")])
        finally:
            active_resources.reset(token)

    async def environment(request):
        return {"owner_id": owner}

    async def owned(db, actual_owner, actual_mode, actual_id):
        assert actual_owner == owner and actual_mode == "ask" and actual_id == identifier
        return {"status": "SUCCEEDED"}

    monkeypatch.setattr(evaluation_observers, "environment", environment)
    monkeypatch.setattr(evaluation_observers, "owned", owned)
    app = FastAPI()
    app.state.db = None
    app.state.evaluation_profiles = state
    evaluation_observers.attach_observers(app)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/agent-evaluations/profiles/runs/ask/{identifier}/observations")
            assert response.status_code == 200
            rows = response.json()["context_assertions"]
            assert len(rows) == 1 and rows[0]["mode"] == "ask"

    asyncio.run(request())
