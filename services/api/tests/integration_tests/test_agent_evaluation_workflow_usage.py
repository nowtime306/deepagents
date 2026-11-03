"""HTTP contracts over isolated persisted ledger fixtures, not A20 workflow execution."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_agent_evaluation_environment import evaluation_environment as evaluation_environment
from test_api import DATABASE, add_user
from test_api import client as client
from workflow_fixtures import ledger_run

ENVIRONMENT = "/api/v1/agent-evaluations/environment"


def workflow(evaluation_environment, status="SUCCEEDED"):
    connection, app, marker, _, role = evaluation_environment
    connection.portal.call(app.state.db.execute,
        "UPDATE platform_roles SET permissions=%s WHERE id=%s", (Jsonb(["evaluation.read", "workflows.read"]), role))
    identifier = connection.portal.call(ledger_run, app.state.db, marker["owner_id"])
    connection.portal.call(app.state.db.execute, "UPDATE workflow_runs SET status=%s WHERE id=%s", (status, identifier))
    return identifier


def usage(evaluation_environment, run, status, incoming=None, outgoing=None, *, mode="workflow", owner=None):
    connection, app, marker, _, _ = evaluation_environment
    connection.portal.call(app.state.db.execute,
        "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status,"
        "input_tokens,output_tokens) "
        "VALUES(%s,%s,%s,%s,'test',1,%s,%s,%s)",
        (uuid4(), run, owner or marker["owner_id"], mode, status, incoming, outgoing))


def endpoint(run):
    return f"/api/v1/agent-evaluations/workflow-runs/{run}/usage"


def test_optional_execution_proof_and_typed_bounds(evaluation_environment):
    connection, app, _, _, _ = evaluation_environment
    assert "execution_limits" not in connection.get(ENVIRONMENT).json()
    proof = {"schema_version": 1, "enforcement": "controlled-model-factory-v1", "scope": "resource-run-root",
             "profiles": {"AET-A02": {"mode": "ask", "max_model_calls": 2, "max_tokens": 1000},
                          "AET-F01": {"mode": "workflow", "max_model_calls": 0, "max_tokens": 0}}}
    app.state.evaluation_execution_limits = proof
    response = connection.get(ENVIRONMENT)
    assert response.status_code == 200 and response.json()["execution_limits"] == proof
    assert all(value not in response.text for value in ("api_key", "ciphertext", "model.key", "postgresql://"))


@pytest.mark.parametrize("mutation", ["schema_bool", "calls_bool", "tokens_negative", "mode", "scope", "extra_secret"])
def test_invalid_execution_proof_fails_closed(evaluation_environment, mutation):
    connection, app, _, _, _ = evaluation_environment
    proof = {"schema_version": 1, "enforcement": "controlled-model-factory-v1", "scope": "resource-run-root",
             "profiles": {"AET-F01": {"mode": "workflow", "max_model_calls": 1, "max_tokens": 1000}}}
    if mutation == "schema_bool":
        proof["schema_version"] = True
    elif mutation == "calls_bool":
        proof["profiles"]["AET-F01"]["max_model_calls"] = True
    elif mutation == "tokens_negative":
        proof["profiles"]["AET-F01"]["max_tokens"] = -1
    elif mutation == "mode":
        proof["profiles"]["AET-F01"]["mode"] = "arbitrary"
    elif mutation == "scope":
        proof["scope"] = "unbounded"
    else:
        proof["api_key"] = "PROOF_SECRET_MUST_NOT_APPEAR"
    app.state.evaluation_execution_limits = proof
    result = connection.get(ENVIRONMENT)
    assert result.status_code == 503 and result.json()["error"]["code"] == "EVALUATION_LIMITS_UNVERIFIED"
    assert "PROOF_SECRET_MUST_NOT_APPEAR" not in result.text


def test_workflow_usage_counts_only_owned_workflow_root_and_stale_pending(evaluation_environment):
    connection, app, _, _, _ = evaluation_environment
    run = workflow(evaluation_environment, "RUNNING")
    usage(evaluation_environment, run, "KNOWN", 3, 2)
    usage(evaluation_environment, run, "KNOWN", 7, 4)
    usage(evaluation_environment, run, "UNKNOWN")
    usage(evaluation_environment, run, "PENDING")
    # UUID collisions in another mode or account must not inflate this root's ledger.
    usage(evaluation_environment, run, "KNOWN", 999, 999, mode="ask")
    other = "usage-other-" + uuid4().hex
    connection.portal.call(add_user, DATABASE, other, "integration-password")
    foreign = connection.portal.call(app.state.db.fetch, "SELECT id FROM users WHERE username=%s", (other,))[0]["id"]
    usage(evaluation_environment, run, "KNOWN", 888, 888, owner=foreign)
    response = connection.get(endpoint(run))
    assert response.status_code == 200, response.text
    original = response.json()
    assert original["scope"] == {"mode": "workflow", "run_id": str(run)} and not original["complete"]
    assert original["total"] == {"calls": 4, "reported_calls": 2, "unknown_calls": 1, "pending_calls": 1,
                                 "input_tokens": 10, "output_tokens": 6}
    assert type(original["total"]["input_tokens"]) is int
    connection.portal.call(app.state.db.execute, "UPDATE workflow_runs SET status='SUCCEEDED' WHERE id=%s", (run,))
    finished = connection.get(endpoint(run)).json()
    assert finished["complete"] and finished["total"]["unknown_calls"] == 2
    assert finished["total"]["pending_calls"] == 0 and finished["total"]["calls"] == 4
    # A projection cannot rewrite an uncertain durable call into KNOWN.
    assert connection.portal.call(app.state.db.fetch,
        "SELECT count(*) AS total FROM model_usage WHERE run_id=%s AND status='PENDING'", (run,))[0]["total"] == 1


@pytest.mark.parametrize("has_unknown", [False, True])
def test_zero_call_and_unknown_usage_are_distinct(evaluation_environment, has_unknown):
    connection, _, _, _, _ = evaluation_environment
    run = workflow(evaluation_environment)
    if has_unknown:
        usage(evaluation_environment, run, "UNKNOWN")
    result = connection.get(endpoint(run))
    assert result.status_code == 200
    total = result.json()["total"]
    assert total["calls"] == int(has_unknown) and total["unknown_calls"] == int(has_unknown)
    assert total["input_tokens"] == (None if has_unknown else 0)
    assert total["output_tokens"] == (None if has_unknown else 0)


def test_workflow_usage_live_permissions_owner_and_isolation(evaluation_environment):
    connection, app, marker, directory, role = evaluation_environment
    run = workflow(evaluation_environment)
    assert connection.get(endpoint(run)).status_code == 200
    for permission in ("evaluation.read", "workflows.read"):
        connection.portal.call(app.state.db.execute,
            "UPDATE platform_roles SET permissions=%s WHERE id=%s", (Jsonb([permission]), role))
        assert connection.get(endpoint(run)).status_code == 403
    connection.portal.call(app.state.db.execute,
        "UPDATE platform_roles SET permissions=%s WHERE id=%s", (Jsonb(["evaluation.read", "workflows.read"]), role))
    other = "foreign-flow-" + uuid4().hex
    connection.portal.call(add_user, DATABASE, other, "integration-password")
    foreign = connection.portal.call(app.state.db.fetch, "SELECT id FROM users WHERE username=%s", (other,))[0]["id"]
    foreign_run = connection.portal.call(ledger_run, app.state.db, foreign)
    assert connection.get(endpoint(foreign_run)).status_code == 404
    invalid = deepcopy(marker)
    invalid["sandbox_namespace"] = str(uuid4())
    (directory / "evaluation-instance.json").write_text(json.dumps(invalid), encoding="utf-8")
    assert connection.get(endpoint(run)).status_code == 503


@pytest.mark.parametrize("parent", ["ask", "work", "external_work"])
def test_embedded_workflow_usage_requires_parent_scope(evaluation_environment, parent):
    connection, app, marker, _, _ = evaluation_environment
    run = workflow(evaluation_environment)
    if parent == "ask":
        connection.portal.call(app.state.db.execute, "UPDATE workflow_runs SET snapshot=%s WHERE id=%s",
                               (Jsonb({"parent_ask_run": str(uuid4())}), run))
    else:
        task, work = uuid4(), uuid4()
        connection.portal.call(app.state.db.execute,
            "INSERT INTO work_tasks(id,owner_id,title,goal) VALUES(%s,%s,'usage scope','controlled')",
            (task, marker["owner_id"]))
        connection.portal.call(app.state.db.execute,
            "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,status) "
            "VALUES(%s,%s,%s,'usage scope','controlled',1,'test','COMPLETED')", (work, task, marker["owner_id"]))
        if parent == "work":
            connection.portal.call(app.state.db.execute,
                "INSERT INTO work_run_workflows(run_id,workflow_run_id) VALUES(%s,%s)", (work, run))
        else:
            version, credential = uuid4(), uuid4()
            flow = connection.portal.call(app.state.db.fetch,
                "SELECT workflow_id FROM workflow_runs WHERE id=%s", (run,))[0]["workflow_id"]
            connection.portal.call(app.state.db.execute,
                "INSERT INTO workflow_versions(id,workflow_id,version,draft_revision,model_id,model_revision,"
                "name,instructions) VALUES(%s,%s,1,1,'test',1,'controlled version','')", (version, flow))
            connection.portal.call(app.state.db.execute,
                "INSERT INTO api_credentials(id,owner_id,name,version_id,token_hash,token_hint,expires_at,"
                "rpm,concurrency,parent_work_run) VALUES(%s,%s,'controlled',%s,%s,'test',"
                "now()+interval '1 hour',1,1,%s)", (credential, marker["owner_id"], version, uuid4().hex, work))
            connection.portal.call(app.state.db.execute,
                "INSERT INTO external_workflow_jobs(run_id,credential_id,idempotency_key,request_hash) "
                "VALUES(%s,%s,%s,'controlled')", (run, credential, uuid4().hex))
    result = connection.get(endpoint(run))
    assert result.status_code == 422 and result.json()["error"]["code"] == "EVALUATION_PARENT_USAGE_REQUIRED"
