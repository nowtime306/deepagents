"""Real SQL/HTTP projection of immutable Work inputs; no worker or model executes."""

from uuid import uuid4

from psycopg.types.json import Jsonb
from test_agent_evaluation_platform import h as h
from test_agent_evaluation_platform import migration as migration

from app.security import COOKIE
from app.work_run_api import router


def test_work_detail_retains_submitted_model_and_input_revision(h):
    h.app.include_router(router)
    h.execute("UPDATE platform_roles SET permissions=permissions || %s WHERE id=%s", (Jsonb(["work.read"]), h.role))
    task, run = uuid4(), uuid4()
    model = "projection-model-" + uuid4().hex
    h.execute(
        "INSERT INTO model_configs(id,name,model,base_url,revision) VALUES(%s,%s,'fixture','https://example.invalid/v1',7)",
        (model, model),
    )
    h.execute(
        "INSERT INTO work_tasks(id,owner_id,title,goal,revision) VALUES(%s,%s,'projection','fixture',4)",
        (task, h.owner),
    )
    h.execute(
        "INSERT INTO work_queue_runs(id,task_id,owner_id,title,goal,task_revision,model_id,model_revision,status) "
        "VALUES(%s,%s,%s,'projection','fixture',2,%s,3,'COMPLETED')",
        (run, task, h.owner, model),
    )
    response = h.http.get(f"/api/v1/work-runs/{run}")
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["model_id"] == model
    assert row["model_revision"] == 3  # Submitted revision, not current model revision7.
    assert row["task_revision"] == 2  # Submitted files, not the current task draft revision4.
    assert not {"lease_token", "lease_until", "api_key_ciphertext"}.intersection(row)
    h.http.cookies.set(COOKIE, h.tokens[1])
    assert h.http.get(f"/api/v1/work-runs/{run}").status_code == 404  # Foreign admin remains excluded.
    h.http.cookies.set(COOKIE, h.tokens[0])
    h.execute("DELETE FROM work_queue_runs WHERE id=%s AND owner_id=%s", (run, h.owner))
    h.execute("DELETE FROM work_tasks WHERE id=%s AND owner_id=%s", (task, h.owner))
    h.execute("DELETE FROM model_configs WHERE id=%s", (model,))
