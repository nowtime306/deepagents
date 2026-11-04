"""Actual PG types and internal HTTP JSON; synthetic Work rows, no consumer or real provider usage."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from app import work_resources as resources
from app.errors import APIError
from app.team_context import active_member
from app.work_shared_budget_guard import require_budget

pytest_plugins = ["test_agent_evaluation_platform"]


@pytest.fixture
def team(h, request):
    before = request.session.testsfailed
    assert h.http.get("/api/v1/agent-evaluations/capabilities").json()["admission"]["status"] == "BLOCKED"
    ids = SimpleNamespace(
        **{name: uuid4() for name in ("parent", "child", "version", "knowledge", "task", "run", "call", "lease")}
    )
    ids.model = "synthetic-team-pg-" + uuid4().hex
    ids.selection = {
        "id": str(ids.parent),
        "knowledge_ids": [str(ids.knowledge)],
        "team_pins": [
            {
                "name": "researcher",
                "version_id": str(ids.version),
                "snapshot": {"model_id": ids.model, "knowledge_ids": [str(ids.knowledge)]},
            }
        ],
    }
    h.execute(
        "INSERT INTO model_configs(id,name,model,base_url) VALUES(%s,%s,'synthetic-no-provider','http://127.0.0.1:29871/v1')",
        (ids.model, ids.model),
    )
    h.execute("INSERT INTO knowledge_bases(id,owner_id,name) VALUES(%s,%s,'Synthetic PG KB')", (ids.knowledge, h.owner))
    for agent, name in [(ids.parent, "parent"), (ids.child, "child")]:
        h.execute(
            "INSERT INTO agents(id,owner_id,name,model_id,knowledge_ids,team) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                agent,
                h.owner,
                "synthetic-" + name + "-" + str(agent),
                ids.model,
                Jsonb([str(ids.knowledge)]),
                Jsonb([{"name": "researcher", "version_id": str(ids.version)}] if name == "parent" else []),
            ),
        )
    h.execute(
        "INSERT INTO agent_versions(id,agent_id,owner_id,version,revision,snapshot) VALUES(%s,%s,%s,1,1,%s)",
        (ids.version, ids.child, h.owner, Jsonb(ids.selection["team_pins"][0]["snapshot"])),
    )
    h.execute(
        "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,'Synthetic metadata','No execution')",
        (ids.task, h.owner),
    )
    h.execute(
        "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,status,"
        "lease_token,lease_until,agent_snapshot,token_limit) "
        "VALUES(%s,%s,%s,'Synthetic metadata','No execution',1,'RUNNING',%s,now()+interval '10 minutes',%s,1000)",
        (ids.run, ids.task, h.owner, ids.lease, Jsonb(ids.selection)),
    )
    h.execute(
        "INSERT INTO team_calls(id,owner_id,work_run_id,lease_token,tool_call_id,input_digest,"
        "role_name,version_id,model_id) "
        "VALUES(%s,%s,%s,%s,'synthetic-tool','synthetic-digest','researcher',%s,%s)",
        (ids.call, h.owner, ids.run, ids.lease, ids.version, ids.model),
    )
    yield ids
    if request.session.testsfailed != before:
        return  # Keep the first failed fixture, including these exact synthetic Work rows.
    for table, identifier in [
        ("team_calls", ids.call),
        ("work_queue_runs", ids.run),
        ("work_tasks", ids.task),
        ("agent_versions", ids.version),
        ("agents", ids.parent),
        ("agents", ids.child),
        ("knowledge_bases", ids.knowledge),
    ]:
        h.execute("DELETE FROM " + table + " WHERE id=%s AND owner_id=%s", (identifier, h.owner))
    h.execute("DELETE FROM model_configs WHERE id=%s AND name=%s", (ids.model, ids.model))


@pytest.mark.parametrize(
    "fault", [None, "child_revoked_before", "child_revoked_during", "frozen_child_missing", "parent_version_changed"]
)
def test_real_uuid_rows_and_http_member_knowledge_authority(h, team, monkeypatch, fault):
    row = h.sql(
        "SELECT id,owner_id,lease_token,agent_snapshot FROM work_queue_runs WHERE id=%s AND owner_id=%s",
        (team.run, h.owner),
    )[0]
    assert isinstance(row["id"], UUID) and isinstance(row["lease_token"], UUID)
    native = h.sql("SELECT id,version_id,model_id FROM team_calls WHERE id=%s AND owner_id=%s", (team.call, h.owner))[0]
    assert isinstance(native["id"], UUID) and isinstance(native["version_id"], UUID) and type(native["model_id"]) is str
    frozen = deepcopy(row["agent_snapshot"])
    if fault == "child_revoked_before":
        h.execute("UPDATE agents SET knowledge_ids='[]' WHERE id=%s AND owner_id=%s", (team.child, h.owner))
    elif fault == "frozen_child_missing":
        frozen["team_pins"][0]["snapshot"]["knowledge_ids"] = []
        h.execute(
            "UPDATE work_queue_runs SET agent_snapshot=%s WHERE id=%s AND owner_id=%s",
            (Jsonb(frozen), team.run, h.owner),
        )
        row["agent_snapshot"] = frozen
    elif fault == "parent_version_changed":
        h.execute(
            "UPDATE agents SET team=%s WHERE id=%s AND owner_id=%s",
            (Jsonb([{"name": "researcher", "version_id": str(uuid4())}]), team.parent, h.owner),
        )
    witness = {"entered": 0, "provider": 0}

    class RetrievalWitness:
        async def search(self, knowledge_id, owner, query, *, usage_context):
            witness["entered"] += 1
            member = active_member.get()
            assert member["id"] == team.call and isinstance(member["id"], UUID)
            assert knowledge_id == team.knowledge and owner == h.owner and usage_context["id"] == team.run
            if fault == "child_revoked_during":
                await h.db.execute(
                    "UPDATE agents SET knowledge_ids='[]' WHERE id=%s AND owner_id=%s", (team.child, h.owner)
                )
            await usage_context["guard"]()  # The actual v4 SQL authority callback immediately before the witness.
            witness["provider"] += 1  # This is explicitly a local effect witness, not embedding or native usage.
            return {"knowledge_id": knowledge_id, "team_call_id": member["id"], "run_id": usage_context["id"]}

    class SeparateContextTransport(httpx.ASGITransport):
        async def handle_async_request(self, request):
            token = active_member.set(None)  # ASGI runs in process; real HTTP would not inherit the caller ContextVar.
            try:
                return await super().handle_async_request(request)
            finally:
                active_member.reset(token)

    h.app.state.knowledge = RetrievalWitness()
    h.app.include_router(resources.router)
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        return original(*args, **kwargs, transport=SeparateContextTransport(app=h.app))

    monkeypatch.setattr(resources.httpx, "AsyncClient", client)
    service = resources.WorkKnowledge(h.db, row, "http://127.0.0.1:29871", "http://127.0.0.1:29871")

    async def exercise():
        token = active_member.set({"id": team.call, "role_name": "researcher"})
        try:
            if fault is None:
                result = await service.search(team.knowledge, h.owner, "synthetic query")
                assert result == {
                    "knowledge_id": str(team.knowledge),
                    "team_call_id": str(team.call),
                    "run_id": str(team.run),
                }
            else:
                with pytest.raises(RuntimeError, match="permission revoked"):
                    await service.search(team.knowledge, h.owner, "synthetic query")
        finally:
            active_member.reset(token)

    h.http.portal.call(exercise)
    assert witness["provider"] == (fault is None)
    assert witness["entered"] == (fault in {None, "child_revoked_during"})
    stored = h.sql("SELECT agent_snapshot FROM work_queue_runs WHERE id=%s AND owner_id=%s", (team.run, h.owner))[0]
    assert stored["agent_snapshot"] == frozen
    assert h.sql("SELECT count(*) AS total FROM model_usage")[0]["total"] == 0


@pytest.mark.parametrize("fault,code", [("unknown", "WORK_USAGE_UNAVAILABLE"), ("exhausted", "WORK_BUDGET_EXCEEDED")])
def test_real_pg_budget_guard_denies_synthetic_flags_without_claiming_native_usage(h, team, fault, code):
    h.execute(
        "UPDATE work_queue_runs SET usage_unknown=%s,used_tokens=%s WHERE id=%s AND owner_id=%s",
        (fault == "unknown", 1000 if fault == "exhausted" else 0, team.run, h.owner),
    )
    run = {"id": team.run, "owner_id": h.owner, "lease_token": team.lease}
    with pytest.raises(APIError) as denied:
        h.http.portal.call(require_budget, h.db, run)
    assert denied.value.code == code
    assert h.sql("SELECT count(*) AS total FROM model_usage")[0]["total"] == 0
