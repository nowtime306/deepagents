"""Synthetic metadata PG contracts only; never activates publication, worker, or Agent work."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from test_agent_evaluation_platform import definition, published

from app.agent_evaluation_platform_admission import Admission
from app.agent_evaluation_platform_queue import Queue
from app.agent_evaluation_platform_worker import Worker
from app.errors import APIError

pytest_plugins = ["test_agent_evaluation_platform"]


async def maintenance_open():
    return True


def seed(h):
    """Insert a synthetic queue row directly, with no admission/publication substitute."""
    assert (
        h.http.get("/api/v1/agent-evaluations/capabilities").json()["admission"][
            "status"
        ]
        == "BLOCKED"
    )
    dataset, _ = published(h)
    version = h.sql(
        "SELECT id,version FROM agent_evaluation_dataset_versions WHERE dataset_id=%s AND owner_id=%s",
        (dataset["id"], h.owner),
    )[0]
    specimen = definition()
    run = uuid4()
    row = h.sql(
        "INSERT INTO agent_evaluation_runs(id,owner_id,dataset_id,dataset_version_id,version,request_id,"
        "payload_digest,execution_mode,repeats,acceptance_target,snapshot,budgets,unique_task_count,total_samples) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,'engineering',1,'engineering',%s,%s,1,1) RETURNING *",
        (
            run,
            h.owner,
            dataset["id"],
            version["id"],
            version["version"],
            uuid4(),
            "f" * 64,
            Jsonb(
                {"definition": specimen, "fixture_scope": "synthetic PG metadata only"}
            ),
            Jsonb(
                {
                    "max_seconds": 300,
                    "max_model_calls": 20,
                    "max_tokens": 20000,
                    "max_sandboxes": 2,
                }
            ),
        ),
    )[0]
    case = specimen["cases"][0]
    h.execute(
        "INSERT INTO agent_evaluation_samples(id,owner_id,run_id,case_id,case_name,mode,repeat_index,case_snapshot) "
        "VALUES(%s,%s,%s,%s,%s,%s,1,%s)",
        (
            uuid4(),
            h.owner,
            run,
            case["case_id"],
            case["name"],
            case["mode"],
            Jsonb(case),
        ),
    )
    return row


def packet(row):
    """A typed import-contract fixture, not real facts, MAC proof, or an A20 publication."""
    case = row["snapshot"]["definition"]["cases"][0]
    sample = {
        "case_id": case["case_id"],
        "repeat_index": 1,
        "task_outcome": "PASS",
        "metric_status": "NOT_REQUIRED",
        "gate_verdict": "PASS",
        "cleanup_status": "CLEAN",
        "facts": [{"registered_id": "output_equals", "status": "PASS"}],
        "metrics": [],
        "evidence_refs": [],
        "root_refs": [],
        "usage": None,
    }
    files = {
        "json": json.dumps(
            {"scope": "synthetic PG import fixture only", "samples": [sample], "gate_verdict": "PASS"}
        ),
        "junit": '<testsuite name="synthetic-pg-fixture" tests="1"/>',
        "markdown": "Synthetic PG import fixture; no Agent execution or acceptance evidence.",
    }
    report = {
        "verified": True,
        "redacted": True,
        "files": files,
        "manifest": {
            "kind": "agent-platform-report-v1",
            "run_id": str(row["id"]),
            "owner_id": str(row["owner_id"]),
            "ready_verified": True,
            "fixture_scope": "synthetic import contract, never publication",
            "files_sha256": {
                key: hashlib.sha256(value.encode()).hexdigest()
                for key, value in files.items()
            },
        },
    }
    return {
        "schema_version": 1,
        "verified": True,
        "gate_verdict": "PASS",
        "samples": [sample],
        "cleanup_status": "CLEAN",
        "report": report,
    }


def actor(h):
    return Worker(h.db, Admission(), maintenance=maintenance_open)


def claim(h, worker):
    row = h.http.portal.call(worker.queue.claim, worker.identifier)
    assert row is not None and row["owner_id"] == h.owner and row["status"] == "RUNNING"
    return row


def state(h, row):
    args = (row["id"], h.owner)
    return {
        "run": h.sql(
            "SELECT * FROM agent_evaluation_runs WHERE id=%s AND owner_id=%s", args
        ),
        "samples": h.sql(
            "SELECT * FROM agent_evaluation_samples WHERE run_id=%s AND owner_id=%s ORDER BY id",
            args,
        ),
        "reports": h.sql(
            "SELECT * FROM agent_evaluation_reports WHERE run_id=%s AND owner_id=%s ORDER BY id",
            args,
        ),
        "events": h.sql(
            "SELECT * FROM agent_evaluation_events WHERE run_id=%s AND owner_id=%s ORDER BY id",
            args,
        ),
    }


def no_business_effects(h):
    counts = h.sql(
        "SELECT (SELECT count(*) FROM model_usage) AS model_calls,"
        "(SELECT count(*) FROM task_runs) AS ask_runs,(SELECT count(*) FROM work_tasks) AS work_tasks,"
        "(SELECT count(*) FROM work_queue_runs) AS work_runs,(SELECT count(*) FROM workflow_runs) AS workflow_runs,"
        "(SELECT count(*) FROM work_sandbox_sessions) AS sandbox_sessions,"
        "(SELECT count(*) FROM operation_executions) AS operations"
    )[0]
    assert counts == dict.fromkeys(counts, 0)


def test_real_claim_begin_renew_and_single_atomic_commit(h):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    assert h.http.portal.call(Queue(h.db).claim, uuid4()) is None
    assert h.http.portal.call(
        worker.queue.begin_dispatch, row["id"], h.owner, row["lease_token"]
    )
    assert h.http.portal.call(
        worker.queue.renew, row["id"], h.owner, row["lease_token"]
    )
    live = state(h, row)["run"][0]
    assert (
        live["dispatch_started"] is True and live["lease_until"] >= row["lease_until"]
    )
    h.http.portal.call(worker.commit, row, packet(row))
    committed = state(h, row)
    result = committed["run"][0]
    assert result["status"] == "COMPLETED" and result["gate_verdict"] == "PASS"
    assert result["counts"] == {"pass": 1, "fail": 0, "needs_review": 0, "not_run": 0}
    assert result["lease_token"] is result["lease_until"] is result["worker_id"] is None
    assert (
        committed["samples"][0]["status"] == "COMPLETED"
        and len(committed["reports"]) == 1
    )
    assert [event["event"] for event in committed["events"]] == ["result.imported"]
    with pytest.raises(ValueError, match="lease"):
        h.http.portal.call(worker.commit, row, packet(row))
    assert state(h, row) == committed
    no_business_effects(h)


def test_foreign_owner_and_expired_lease_import_nothing(h):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    before = state(h, row)
    foreign = {
        **row,
        "owner_id": h.other,
    }  # Even the separate fixture administrator cannot borrow this row.
    with pytest.raises(ValueError, match="lease"):
        h.http.portal.call(worker.commit, foreign, packet(foreign))
    assert state(h, row) == before
    h.execute(
        "UPDATE agent_evaluation_runs SET lease_until=now()-interval '1 second' WHERE id=%s AND owner_id=%s",
        (row["id"], h.owner),
    )
    before = state(h, row)
    assert (
        h.http.portal.call(worker.queue.renew, row["id"], h.owner, row["lease_token"])
        is False
    )
    assert (
        h.http.portal.call(
            worker.queue.begin_dispatch, row["id"], h.owner, row["lease_token"]
        )
        is False
    )
    with pytest.raises(ValueError, match="lease"):
        h.http.portal.call(worker.commit, row, packet(row))
    assert state(h, row) == before and before["samples"][0]["status"] == "PENDING"
    no_business_effects(h)


def test_report_unique_failure_rolls_back_prior_sample_update(h):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    # Exact owned synthetic duplicate triggers a real PG error after sample UPDATE, without changing schema.
    h.execute(
        "INSERT INTO agent_evaluation_reports(id,owner_id,run_id,manifest,files,verified,redacted) "
        "VALUES(%s,%s,%s,%s,%s,false,false)",
        (
            uuid4(),
            h.owner,
            row["id"],
            Jsonb({"fixture": "rollback blocker"}),
            Jsonb({}),
        ),
    )
    before = state(h, row)
    with pytest.raises(UniqueViolation):
        h.http.portal.call(worker.commit, row, packet(row))
    assert state(h, row) == before
    assert (
        before["run"][0]["status"] == "RUNNING"
        and before["samples"][0]["status"] == "PENDING"
    )
    no_business_effects(h)


def test_cancelled_row_denies_renew_dispatch_and_later_permit(h):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    h.execute(
        "UPDATE agent_evaluation_runs SET status='CANCEL_REQUESTED',cancel_requested=true "
        "WHERE id=%s AND owner_id=%s",
        (row["id"], h.owner),
    )
    before = state(h, row)
    # Unit-only admission stub isolates the real SQL cancel fence; never registered on the app or a worker loop.
    worker.admission = SimpleNamespace(inspect=lambda _: {"status": "READY"})
    assert (
        h.http.portal.call(worker.queue.renew, row["id"], h.owner, row["lease_token"])
        is False
    )
    assert (
        h.http.portal.call(
            worker.queue.begin_dispatch, row["id"], h.owner, row["lease_token"]
        )
        is False
    )
    assert (
        h.http.portal.call(worker.permitted, row, {"case_id": "AET-A02", "mode": "ask"})
        is False
    )
    assert state(h, row) == before
    no_business_effects(h)


def test_revoked_owner_permissions_deny_renew_and_permit_without_import(h):
    seed(h)
    worker = actor(h)
    row = claim(h, worker)
    h.execute(
        "UPDATE platform_roles SET permissions=%s WHERE id=%s",
        (Jsonb(["evaluation.read"]), h.role),
    )
    before = state(h, row)
    worker.admission = SimpleNamespace(
        inspect=lambda _: {"status": "READY"}
    )  # Test-only, no publication.
    with pytest.raises(APIError) as denied:
        h.http.portal.call(worker.queue.renew, row["id"], h.owner, row["lease_token"])
    assert denied.value.status == 403
    assert (
        h.http.portal.call(worker.permitted, row, {"case_id": "AET-A02", "mode": "ask"})
        is False
    )
    assert state(h, row) == before
    no_business_effects(h)
