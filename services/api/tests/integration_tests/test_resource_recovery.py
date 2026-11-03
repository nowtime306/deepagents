"""Configuration confirmation seals a new stage without replaying old approvals."""

from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from test_api import ORIGIN
from test_api import client as client

from app.resource_recovery_api import router
from app.resource_runtime import resource_run


@pytest.fixture
def paused(client):
    connection, app = client
    app.include_router(router)
    app.state.settings.work_enabled = True
    connection.portal.call(app.state.db.execute, "INSERT INTO work_worker_instances(id) VALUES(%s)", (uuid4(),))
    task = connection.post("/api/v1/work-tasks", headers=ORIGIN,
                           json={"title": "recover configuration", "goal": "keep checkpoint"}).json()["id"]
    response = connection.post(f"/api/v1/work-tasks/{task}/runs", headers=ORIGIN,
                               json={"revision": 1, "model_id": "test"})
    assert response.status_code == 202, response.text
    identifier = response.json()["run_id"]

    async def seed():
        run = (await app.state.db.fetch("SELECT * FROM work_queue_runs WHERE id=%s", (identifier,)))[0]
        async with resource_run(app.state.db, run["owner_id"], "work", identifier):
            await app.state.models.get("test")
        graph = StateGraph(dict)
        graph.add_node("model", lambda state: state)
        graph.add_edge(START, "model")
        graph.add_edge("model", END)
        pending = graph.compile(checkpointer=app.state.runtime.saver, interrupt_before=["model"])
        await pending.ainvoke(
            {"messages": ["original input"]}, {"configurable": {"thread_id": "work:" + identifier}},
        )
        await app.state.db.execute(
            "UPDATE model_configs SET revision=revision+1,base_url='http://new-approved-target' WHERE id='test'"
        )
        await app.state.db.execute(
            "UPDATE work_queue_runs SET status='PAUSED',error_code='RESOURCE_RECOVERY_CONFIRMATION_REQUIRED' "
            "WHERE id=%s",
            (identifier,),
        )
        return run

    run = connection.portal.call(seed)
    yield connection, app, identifier, run
    connection.portal.call(app.state.db.execute, "DELETE FROM work_queue_runs WHERE id=%s", (identifier,))
    connection.delete(f"/api/v1/work-tasks/{task}", headers=ORIGIN)


def test_confirmation_is_compare_and_swap_and_seals_current_configuration(paused):
    connection, app, identifier, run = paused
    endpoint = f"/api/v1/resource-runs/work/{identifier}/configuration-recovery"
    first = connection.get(endpoint)
    assert first.status_code == 200, first.text
    assert first.json()["can_confirm"] and first.json()["phase"] == 1
    assert "test-only" not in first.text and "api_key" not in first.text
    ordinary = connection.post(f"/api/v1/work-runs/{identifier}/resume", headers=ORIGIN)
    assert ordinary.status_code == 409 and ordinary.json()["error"]["code"] == "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED"
    body = {"expected_phase": 1, "confirmation_digest": first.json()["confirmation_digest"]}
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET temperature=0.6,revision=revision+1 WHERE id='test'")
    assert connection.post(endpoint, headers=ORIGIN, json=body).status_code == 409
    current = connection.get(endpoint).json()
    confirmed = connection.post(endpoint, headers=ORIGIN,
                                json={**body, "confirmation_digest": current["confirmation_digest"]})
    assert confirmed.status_code == 202 and confirmed.json()["phase"] == 2, confirmed.text
    assert connection.post(endpoint, headers=ORIGIN,
                           json={**body, "confirmation_digest": current["confirmation_digest"]}).status_code == 409
    state = connection.portal.call(app.state.db.fetch,
        "SELECT status,resource_resume_pending,resume_decision FROM work_queue_runs WHERE id=%s", (identifier,))[0]
    assert state["status"] == "PENDING" and state["resource_resume_pending"] and state["resume_decision"] is None
    stages = connection.portal.call(app.state.db.fetch,
        "SELECT phase,confirmed_by FROM run_resource_stages WHERE run_id=%s ORDER BY phase", (identifier,))
    assert len(stages) == 2 and str(stages[1]["confirmed_by"]) == str(run["owner_id"])
    snapshots = connection.portal.call(app.state.db.fetch,
        "SELECT phase,execution_config FROM run_resource_snapshots WHERE run_id=%s ORDER BY phase", (identifier,))
    assert len(snapshots) == 2
    frozen = app.state.models.vault.decrypt(snapshots[1]["execution_config"]).get_secret_value()
    assert '"temperature": 0.6' in frozen and "new-approved-target" in frozen and "test-only" not in frozen
    connection.portal.call(app.state.db.execute,
                           "UPDATE model_configs SET temperature=0.9,revision=revision+1 WHERE id='test'")

    async def actual_start():
        async with resource_run(app.state.db, run["owner_id"], "work", identifier):
            return await app.state.models.get("test")

    actual = connection.portal.call(actual_start)
    assert actual.temperature == 0.6 and actual.base_url == "http://new-approved-target"


def test_old_approvals_or_absent_checkpoint_require_manual_review(paused):
    connection, app, identifier, run = paused
    endpoint = f"/api/v1/resource-runs/work/{identifier}/configuration-recovery"
    connection.portal.call(app.state.db.execute,
        "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,expires_at,decision) "
        "VALUES(%s,%s,%s,'old','{}','old',now()+interval '1 hour','approve')", (uuid4(), identifier, run["owner_id"]))
    preview = connection.get(endpoint).json()
    assert not preview["can_confirm"] and any("旧审批" in reason for reason in preview["manual_review"])
    rejected = connection.post(endpoint, headers=ORIGIN,
        json={"expected_phase": preview["phase"], "confirmation_digest": preview["confirmation_digest"]})
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "RESOURCE_RECOVERY_MANUAL_REVIEW"
    connection.portal.call(app.state.db.execute, "DELETE FROM work_approvals WHERE run_id=%s", (identifier,))
    connection.portal.call(app.state.db.execute, "DELETE FROM checkpoints WHERE thread_id=%s", ("work:" + identifier,))
    preview = connection.get(endpoint).json()
    assert not preview["can_confirm"] and not preview["checkpoint_recorded"]


def test_cancelled_or_foreign_run_cannot_be_confirmed(paused):
    connection, app, identifier, _ = paused
    endpoint = f"/api/v1/resource-runs/work/{identifier}/configuration-recovery"
    assert connection.get(f"/api/v1/resource-runs/work/{uuid4()}/configuration-recovery").status_code == 404
    connection.portal.call(app.state.db.execute,
                           "UPDATE work_queue_runs SET cancel_requested=true WHERE id=%s", (identifier,))
    response = connection.get(endpoint)
    assert response.status_code == 409 and response.json()["error"]["code"] == "RESOURCE_RECOVERY_NOT_SAFE"


@pytest.mark.parametrize("status", ["APPROVED", "STARTED", "UNKNOWN", "SUCCEEDED"])
def test_operation_receipts_cannot_be_reused_for_new_configuration(paused, status):
    connection, app, identifier, run = paused
    approval = uuid4()
    connection.portal.call(app.state.db.execute,
        "INSERT INTO work_approvals(id,run_id,owner_id,interrupt_id,payload,digest,expires_at,decision) "
        "VALUES(%s,%s,%s,'operation','{}','old',now()+interval '1 hour','approve')",
        (approval, identifier, run["owner_id"]))
    connection.portal.call(app.state.db.execute,
        "INSERT INTO operation_executions(id,run_id,approval_id,action_index,name,args,digest,status) "
        "VALUES(%s,%s,%s,0,'execute','{}','old',%s)", (uuid4(), identifier, approval, status))
    endpoint = f"/api/v1/resource-runs/work/{identifier}/configuration-recovery"
    preview = connection.get(endpoint).json()
    assert not preview["can_confirm"] and any("操作执行凭据" in reason for reason in preview["manual_review"])
    rejected = connection.post(endpoint, headers=ORIGIN,
        json={"expected_phase": preview["phase"], "confirmation_digest": preview["confirmation_digest"]})
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "RESOURCE_RECOVERY_MANUAL_REVIEW"
    stored = connection.portal.call(app.state.db.fetch,
                                    "SELECT status FROM operation_executions WHERE approval_id=%s", (approval,))[0]
    assert stored["status"] == status
