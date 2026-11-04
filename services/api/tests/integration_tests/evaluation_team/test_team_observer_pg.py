"""Real observer SQL and FastAPI JSON with a proof substitute; not real environment authentication or Work."""

import hashlib
from uuid import uuid4

import evaluation_team_observer as observer
import pytest
from evaluation_target import ProfileState
from evaluation_team_audit import TeamAudit
from psycopg.types.json import Jsonb

pytest_plugins = ["test_agent_evaluation_platform", "test_team_pg_http"]
SECRET_GOAL = "SENSITIVE_PROMPT_DO_NOT_EXPORT_OBSERVER_PG"
SECRET_INSTRUCTION = "SENSITIVE_INSTRUCTION_DO_NOT_EXPORT_OBSERVER_PG"


@pytest.fixture
def observed(h, team, request, tmp_path, monkeypatch):
    failures = request.session.testsfailed
    team.lead_version = uuid4()
    team.reviewer_model = str(uuid4())
    team.reviewer_name = "synthetic-reviewer-" + team.reviewer_model
    h.execute(
        "INSERT INTO model_configs(id,name,model,base_url) VALUES(%s,%s,'synthetic-no-provider','http://127.0.0.1:29871/v1')",
        (team.reviewer_model, team.reviewer_name),
    )
    child = {"id": str(team.child), "model_id": team.reviewer_model, "knowledge_ids": [str(team.knowledge)]}
    snapshot = {
        "id": str(team.parent),
        "published_version_id": str(team.lead_version),
        "knowledge_ids": [str(team.knowledge)],
        "team_pins": [{"name": "reviewer", "version_id": str(team.version), "snapshot": child}],
    }
    h.execute(
        "INSERT INTO agent_versions(id,agent_id,owner_id,version,revision,snapshot) VALUES(%s,%s,%s,1,1,%s)",
        (team.lead_version, team.parent, h.owner, Jsonb(snapshot)),
    )
    h.execute(
        "UPDATE agent_versions SET snapshot=%s WHERE id=%s AND owner_id=%s", (Jsonb(child), team.version, h.owner)
    )
    h.execute("UPDATE agents SET model_id=%s WHERE id=%s AND owner_id=%s", (team.reviewer_model, team.child, h.owner))
    h.execute(
        "UPDATE agents SET team=%s WHERE id=%s AND owner_id=%s",
        (Jsonb([{"name": "reviewer", "version_id": str(team.version)}]), team.parent, h.owner),
    )
    h.execute(
        "UPDATE team_calls SET role_name='reviewer',model_id=%s WHERE id=%s AND owner_id=%s",
        (team.reviewer_model, team.call, h.owner),
    )
    h.execute(
        "UPDATE work_queue_runs SET model_id=%s,model_revision=1,agent_snapshot=%s,goal=%s,instructions=%s "
        "WHERE id=%s AND owner_id=%s",
        (team.model, Jsonb(snapshot), SECRET_GOAL, SECRET_INSTRUCTION, team.run, h.owner),
    )
    team.namespace = h.sql("SELECT id FROM work_sandbox_namespace WHERE singleton")[0]["id"]
    team.environment = str(uuid4())
    state = ProfileState(tmp_path / "profile-state.json")
    state.register_model(team.model, "AET-W06", "lead", h.owner)
    state.register_model(team.reviewer_model, "AET-W06", "reviewer", h.owner)
    assert state.models == {
        team.model: {"case_id": "AET-W06", "role": "lead", "owner_id": str(h.owner)},
        team.reviewer_model: {"case_id": "AET-W06", "role": "reviewer", "owner_id": str(h.owner)},
    }
    h.app.state.evaluation_profiles = state
    h.app.state.evaluation_team_audit = TeamAudit(tmp_path / "team-audit.json", state)
    observer.attach(h.app)
    team.proof = {"owner_id": h.owner, "environment_id": team.environment, "sandbox_namespace": str(team.namespace)}

    async def environment(_):
        return team.proof  # Explicit proof-only substitute; the complete actual observer SQL is unchanged.

    monkeypatch.setattr(observer, "environment", environment)
    yield team
    if request.session.testsfailed != failures:
        return
    h.execute("UPDATE agents SET model_id=%s WHERE id=%s AND owner_id=%s", (team.model, team.child, h.owner))
    h.execute("UPDATE team_calls SET model_id=%s WHERE id=%s AND owner_id=%s", (team.model, team.call, h.owner))
    h.execute("DELETE FROM agent_versions WHERE id=%s AND owner_id=%s", (team.lead_version, h.owner))
    h.execute("DELETE FROM model_configs WHERE id=%s AND name=%s", (team.reviewer_model, team.reviewer_name))


@pytest.mark.parametrize("fault", [None, "foreign_owner", "wrong_namespace"])
def test_observer_actual_pg_projection_json_binding_and_redaction(h, observed, fault):
    if fault == "foreign_owner":
        observed.proof["owner_id"] = h.other
    elif fault == "wrong_namespace":
        observed.proof["sandbox_namespace"] = str(uuid4())
    response = h.http.get(f"/api/v1/agent-evaluations/profiles/team-runs/{observed.run}/observations")
    assert SECRET_GOAL not in response.text and SECRET_INSTRUCTION not in response.text
    assert str(observed.lease) not in response.text
    if fault:
        assert response.status_code == (404 if fault == "foreign_owner" else 503)
        assert response.json()["error"]["code"] == (
            "EVALUATION_TEAM_RUN_NOT_FOUND" if fault == "foreign_owner" else "EVALUATION_TEAM_OBSERVER_MISSING"
        )
    else:
        assert response.status_code == 200, response.text
        value = response.json()
        assert (value["owner_id"], value["environment_id"], value["namespace"], value["case_id"]) == (
            str(h.owner),
            observed.environment,
            str(observed.namespace),
            "AET-W06",
        )
        assert value["binding"] == {
            "agent_id": str(observed.parent),
            "agent_version_id": str(observed.lead_version),
            "reviewer_agent_id": str(observed.child),
            "reviewer_version_id": str(observed.version),
            "reviewer_model_id": observed.reviewer_model,
        }
        assert value["run"]["id"] == str(observed.run) and value["run"]["task_id"] == str(observed.task)
        assert value["run"]["model_id"] == observed.model and value["run"]["model_revision"] == 1
        assert value["run"]["task_revision"] == 1
        assert value["run"]["goal_digest"] == hashlib.sha256(SECRET_GOAL.encode()).hexdigest()
        assert not {"goal", "instructions", "lease_token", "agent_snapshot"}.intersection(value["run"])
        assert len(value["members"]) == 1 and value["members"][0]["id"] == str(observed.call)
        assert value["members"][0]["version_id"] == str(observed.version)
        assert value["members"][0]["model_id"] == observed.reviewer_model
        assert value["ledger"] == [] and value["sessions"] == [] and value["audit"]["records"] == []
        assert value["provider"]["calls"] == value["provider"]["tokens"] == 0
        assert value["effects"]["owner_model_calls"] == 0
    assert h.sql("SELECT count(*) AS total FROM model_usage")[0]["total"] == 0
