"""ASGI/FakeDB scope probes for the private one-value Literal extension, not real HTTP/PG."""
import asyncio
from uuid import uuid4

import app.resource_trace_api as trace
import evaluation_observers as observers
import httpx
import pytest
from app.errors import APIError
from evaluation_target import ProfileState
from fastapi import FastAPI
from test_knowledge_profile import reserve, scope


@pytest.mark.parametrize("denied",[None,"owner","permission"])
def test_resource_observer_uses_actual_owned_permission_and_genuine_root_summary(monkeypatch,denied):
    owner,foreign,run=uuid4(),uuid4(),uuid4();state=ProfileState();reserve(state,scope(owner,"resource",run),"embedding",7)
    checks=[]
    async def environment(request):return {"owner_id":foreign if denied=="owner" else owner}
    async def permission(database,identifier,name):
        checks.append((identifier,name))
        if denied=="permission":raise APIError(403,"DENIED","controlled permission probe")
    class Database:
        async def fetch(self,query,params):
            if "FROM resource_operations" in query:
                assert params[0]==run
                return [{"status":"SUCCEEDED","error_code":None,"resource_kind":"knowledge"}] if params[1]==owner else []
            if "FROM knowledge_video_jobs" in query:return []
            raise AssertionError("Unexpected SQL")
    monkeypatch.setattr(observers,"environment",environment);monkeypatch.setattr(trace,"require_account",permission)
    app=FastAPI();app.state.db=Database();app.state.evaluation_profiles=state;observers.attach_observers(app)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            response=await client.get(f"/api/v1/agent-evaluations/profiles/runs/resource/{run}/observations")
            if denied is None:
                capability=await client.get("/api/v1/agent-evaluations/profiles/capabilities")
                assert "root-model-context-resource-v1" in capability.json()["observers"]
            return response
    if denied:
        with pytest.raises(APIError) as captured:asyncio.run(request())
        assert captured.value.status==(404 if denied=="owner" else 403)
    else:
        response=asyncio.run(request());assert response.status_code==200;value=response.json()
        assert value["controlled_model"]=={"recorded":True,"complete":True,"case_id":"AET-A03","provider_calls":1,"tokens":7}
        assert value["mode"]=="resource" and value["run_id"]==str(run)
        assert value["context_assertions"][0]["role"]=="embedding"
        assert checks==[(owner,"knowledge.read")]
