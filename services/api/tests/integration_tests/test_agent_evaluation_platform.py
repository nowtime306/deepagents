"""Explicit isolated PG + real session/RBAC metadata contracts; no worker, business or provider execution."""

import asyncio
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.agent_evaluation_platform_admission import EXPECTED, Admission, Publication
from app.agent_evaluation_platform_api import router as core
from app.agent_evaluation_platform_extension_api import router as extension
from app.agent_evaluation_platform_queue import Queue
from app.errors import APIError
from app.manage import migrate
from app.security import COOKIE, token_digest
from app.storage import Database
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/api/app").is_dir())
URL = os.getenv("TEST_DATABASE_URL", "")
DATA = os.getenv("TEST_DATA_DIR", "")
PREFIX = "/api/v1/agent-evaluations"
PERMISSIONS = ["evaluation.read", "evaluation.manage", "evaluation.run", "chat.read", "chat.run", "agents.manage"]


def require_isolation():
    """No default DB/data location, service credentials, CREATE/DROP DATABASE or broad cleanup."""
    try:
        config = conninfo_to_dict(URL)
        data = Path(DATA)
        safe = (
            bool(URL)
            and bool(DATA)
            and config.get("dbname", "").endswith("_test")
            and config.get("host") in {"127.0.0.1", "localhost", "::1"}
            and data.name.endswith("_test")
            and data.is_dir()
            and not any(p.is_symlink() or p.is_junction() for p in [data, *data.parents])
        )
    except Exception:
        safe = False
    if not safe:
        pytest.skip(
            "Explicit local TEST_DATABASE_URL ending _test and independent real TEST_DATA_DIR ending _test required"
        )


def sync(coroutine):
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    ) as runner:
        return runner.run(coroutine)


@pytest.fixture(scope="session", autouse=True)
def migration():
    require_isolation()
    sync(migrate(URL))


@pytest.fixture
def h(request):
    db = Database(URL)

    @asynccontextmanager
    async def lifespan(app):
        await db.pool.open()
        await db.pool.wait()
        yield
        await db.pool.close()

    app = FastAPI(lifespan=lifespan)
    app.state.db = db
    app.include_router(core)
    app.include_router(extension)

    @app.exception_handler(APIError)
    async def api_error(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with TestClient(app, raise_server_exceptions=False, backend_options={"loop_factory": factory}) as http:
        # Queue.claim and cardinality checks require an empty isolated metadata namespace.
        tables = [
            "events",
            "reviews",
            "comparisons",
            "failure_candidates",
            "reports",
            "samples",
            "runs",
            "version_sets",
            "dataset_versions",
            "datasets",
        ]
        existing = " UNION ALL ".join("SELECT 1 FROM agent_evaluation_" + suffix for suffix in tables)
        if http.portal.call(db.fetch, "SELECT 1 FROM (" + existing + ") AS existing LIMIT 1"):
            pytest.skip("An independent empty evaluation-test DB is required; existing metadata is preserved")
        failures_before = request.session.testsfailed
        role = "qa-" + uuid4().hex
        http.portal.call(
            db.execute,
            "INSERT INTO platform_roles(id,name,permissions) VALUES(%s,%s,%s)",
            (role, role, Jsonb(PERMISSIONS)),
        )
        owners, tokens = [uuid4(), uuid4()], [uuid4().hex, uuid4().hex]
        for index, (owner, token) in enumerate(zip(owners, tokens, strict=True)):
            http.portal.call(
                db.execute,
                "INSERT INTO users(id,username,password_hash,is_admin,role_id) VALUES(%s,%s,%s,%s,%s)",
                (
                    owner,
                    "qa-" + uuid4().hex,
                    "fixture-session-only-no-password-login",
                    index == 1,
                    "admin" if index else role,
                ),
            )
            http.portal.call(
                db.execute,
                "INSERT INTO auth_sessions(token_hash,owner_id,expires_at) VALUES(%s,%s,now()+interval '1 hour')",
                (token_digest(token), owner),
            )
        http.cookies.set(COOKIE, tokens[0])
        value = SimpleNamespace(
            http=http,
            app=app,
            db=db,
            owner=owners[0],
            other=owners[1],
            role=role,
            tokens=tokens,
            sql=lambda query, args=(): http.portal.call(db.fetch, query, args),
            execute=lambda query, args=(): http.portal.call(db.execute, query, args),
        )
        yield value
        if request.session.testsfailed != failures_before:
            return  # Retain this first failure's exact fixture rows; no later fixture erases them.
        for suffix in tables:
            http.portal.call(
                db.execute, "DELETE FROM agent_evaluation_" + suffix + " WHERE owner_id=ANY(%s)", (owners,)
            )
        http.portal.call(db.execute, "DELETE FROM auth_sessions WHERE owner_id=ANY(%s)", (owners,))
        http.portal.call(db.execute, "DELETE FROM users WHERE id=ANY(%s)", (owners,))
        http.portal.call(db.execute, "DELETE FROM platform_roles WHERE id=%s", (role,))


def definition():
    value = json.loads((ROOT / "services/evaluation/datasets/a20-fixed.json").read_text(encoding="utf-8"))
    value["cases"] = [next(c for c in value["cases"] if c["case_id"] == "AET-A02")]
    return value


def dataset(h, name="fixture dataset"):
    response = h.http.post(
        PREFIX + "/datasets",
        json={"name": name, "description": "preserve description", "revision": 0, "definition": definition()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def published(h):
    d = dataset(h)
    response = h.http.post(f"{PREFIX}/datasets/{d['id']}/versions", json={"revision": d["revision"]})
    assert response.status_code == 201, response.text
    return d, response.json()


def activate(h):
    # Explicit synthetic admission/driver substitute; never represents actual A20 evidence or a real worker.
    receipt = json.dumps(
        {
            "schema_version": 2,
            "kind": "agent-evaluation-stage-a-publication-v2",
            "acceptance_verdict": "PASS",
            "independent_review": "CLOSED",
            "cases": [
                {
                    "case_id": c,
                    "acceptance_verdict": "PASS",
                    "actual_task_outcome": "FAIL" if c == "AET-W01" else "PASS",
                    "actual_gate": "FAIL" if c == "AET-W01" else "PASS",
                    "expected_gate": "FAIL" if c == "AET-W01" else "PASS",
                    "actual_cleanup": "CLEAN",
                    "expected_cleanup": "CLEAN",
                    "definition_digest": "a" * 64,
                    "evidence_digest": "b" * 64,
                    "evidence_complete": True,
                    "cleanup_verified": True,
                }
                for c in sorted(EXPECTED)
            ],
        }
    ).encode()
    version = {
        "candidate_ref": str(uuid4()),
        "platform_commit": "1" * 40,
        "source_digest": "2" * 64,
        "runner_lock_digest": "3" * 64,
        "rules_id": "fixture-only",
        "rules_digest": "4" * 64,
        "business_refs": [],
        "model_refs": [],
        "judge_ref": None,
    }
    driver = SimpleNamespace(
        readiness=lambda owner: {
            k: True
            for k in [
                "ready",
                "source_verified",
                "lease_enforced",
                "owner_permissions_per_effect",
                "cooperative_cancel",
                "ready_reader",
                "precise_cleanup",
            ]
        },
        default_version_set=lambda owner: version,
        candidates=lambda owner: [],
        validate_definition=lambda *args: True,
    )
    h.app.state.agent_evaluation_platform_admission = Admission(
        Publication(receipt, hashlib.sha256(receipt).hexdigest(), lambda raw: raw == receipt), driver
    )


def body(d, version):
    return {
        "dataset_id": d["id"],
        "version": version["version"],
        "request_id": str(uuid4()),
        "repeats": 1,
        "execution_mode": "engineering",
        "budgets": {"max_seconds": 3600, "max_model_calls": 100, "max_tokens": 1000000, "max_sandboxes": 2},
    }


def queued(h):
    d, v = published(h)
    activate(h)
    request = body(d, v)
    response = h.http.post(PREFIX + "/runs", json=request)
    assert response.status_code == 202, response.text
    return response.json(), request


def test_default_admission_rejects_without_queue_write(h):
    assert h.http.get(PREFIX + "/capabilities").json()["admission"]["status"] == "BLOCKED"
    d, v = published(h)
    response = h.http.post(PREFIX + "/runs", json=body(d, v))
    assert response.status_code == 503
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_runs")[0]["n"] == 0
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_samples")[0]["n"] == 0


def test_owner_including_admin_and_live_readonly_revocation(h):
    d = dataset(h)
    h.http.cookies.set(COOKIE, h.tokens[1])
    assert h.http.get(f"{PREFIX}/datasets/{d['id']}").status_code == 404
    assert h.http.get(PREFIX + "/datasets").json()["items"] == []
    h.http.cookies.set(COOKIE, h.tokens[0])
    h.sql("UPDATE platform_roles SET permissions=%s WHERE id=%s RETURNING id", (Jsonb(["evaluation.read"]), h.role))
    assert h.http.get(f"{PREFIX}/datasets/{d['id']}").status_code == 200
    assert (
        h.http.post(
            PREFIX + "/datasets", json={"name": "denied", "revision": 0, "definition": definition()}
        ).status_code
        == 403
    )
    h.sql("UPDATE users SET enabled=false WHERE id=%s RETURNING id", (h.owner,))
    assert h.http.get(f"{PREFIX}/datasets/{d['id']}").status_code == 403


def test_draft_cas_and_published_version_immutable(h):
    d, v = published(h)
    current = h.http.get(f"{PREFIX}/datasets/{d['id']}").json()
    request = {
        "name": "changed",
        "description": current["description"],
        "revision": current["revision"],
        "definition": deepcopy(current["definition"]),
    }
    request["definition"]["cases"][0]["input"] += "\nmetadata-only revision"
    assert h.http.put(f"{PREFIX}/datasets/{d['id']}", json=request).status_code == 200
    assert h.http.put(f"{PREFIX}/datasets/{d['id']}", json=request).status_code == 409
    old = h.http.get(f"{PREFIX}/datasets/{d['id']}/versions/{v['version']}").json()
    assert old["definition"] == v["definition"] and old["definition_digest"] == v["definition_digest"]


def test_concurrent_draft_cas_single_winner(h):
    d = dataset(h)
    requests = [
        {"name": name, "description": d["description"], "revision": d["revision"], "definition": d["definition"]}
        for name in ("one", "two")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda b: h.http.put(f"{PREFIX}/datasets/{d['id']}", json=b).status_code, requests))
    assert sorted(statuses) == [200, 409]


def test_publish_rollback_when_version_insert_fails(h):
    d = dataset(h)
    name = "qa_block_" + uuid4().hex
    h.execute(
        f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN "
        "RAISE EXCEPTION 'controlled metadata insert failure'; END$$"
    )
    h.execute(
        f"CREATE TRIGGER {name} BEFORE INSERT ON agent_evaluation_dataset_versions "
        f"FOR EACH ROW WHEN (NEW.owner_id='{h.owner}'::uuid) EXECUTE FUNCTION {name}()"
    )
    response = h.http.post(f"{PREFIX}/datasets/{d['id']}/versions", json={"revision": d["revision"]})
    assert response.status_code == 500
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_dataset_versions")[0]["n"] == 0
    row = h.sql("SELECT revision,current_version FROM agent_evaluation_datasets WHERE id=%s", (UUID(d["id"]),))[0]
    assert row == {"revision": d["revision"], "current_version": None}
    h.execute(f"DROP TRIGGER {name} ON agent_evaluation_dataset_versions")
    h.execute(f"DROP FUNCTION {name}()")


def test_run_idempotency_atomic_plan_and_no_fabricated_usage(h):
    run, request = queued(h)
    replay = h.http.post(PREFIX + "/runs", json=request)
    assert replay.status_code == 202 and replay.json()["id"] == run["id"]
    conflict = {**request, "budgets": {**request["budgets"], "max_tokens": request["budgets"]["max_tokens"] - 1}}
    assert h.http.post(PREFIX + "/runs", json=conflict).status_code == 409
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_runs")[0]["n"] == 1
    rows = h.http.get(f"{PREFIX}/runs/{run['id']}/samples").json()["items"]
    assert len(rows) == 1 and rows[0]["task_outcome"] == "NOT_RUN" and rows[0]["gate_verdict"] == "PENDING"
    sample = h.http.get(f"{PREFIX}/runs/{run['id']}/samples/{rows[0]['id']}").json()
    assert isinstance(sample["revision"], int) and sample["usage"] == {
        "calls": None,
        "input_tokens": None,
        "output_tokens": None,
        "completeness": "NOT_RUN",
    }
    assert sample["facts"] == sample["metrics"] == sample["root_refs"] == []


def test_concurrent_request_id_creates_exactly_one_plan(h):
    d, v = published(h)
    activate(h)
    request = body(d, v)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: h.http.post(PREFIX + "/runs", json=request), range(2)))
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_samples")[0]["n"] == 1


def test_enqueue_rollback_when_child_insert_fails(h):
    d, v = published(h)
    activate(h)
    name = "qa_block_" + uuid4().hex
    h.execute(
        f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN "
        "RAISE EXCEPTION 'controlled metadata child failure'; END$$"
    )
    h.execute(
        f"CREATE TRIGGER {name} BEFORE INSERT ON agent_evaluation_samples "
        f"FOR EACH ROW WHEN (NEW.owner_id='{h.owner}'::uuid) EXECUTE FUNCTION {name}()"
    )
    assert h.http.post(PREFIX + "/runs", json=body(d, v)).status_code == 500
    assert h.sql(
        "SELECT (SELECT count(*) FROM agent_evaluation_runs) AS runs,"
        "(SELECT count(*) FROM agent_evaluation_samples) AS samples,"
        "(SELECT count(*) FROM agent_evaluation_events) AS events"
    )[0] == {"runs": 0, "samples": 0, "events": 0}
    h.execute(f"DROP TRIGGER {name} ON agent_evaluation_samples")
    h.execute(f"DROP FUNCTION {name}()")


@pytest.mark.parametrize(
    "result", [None, False, {}, {"status": "BLOCKED", "parameters_schema_verified": False}, "unavailable", 0, 1, []]
)
def test_unverified_profile_hook_never_enqueues(h, result):
    d, v = published(h)
    activate(h)
    h.app.state.agent_evaluation_platform_admission.driver.validate_definition = lambda *args: result
    response = h.http.post(PREFIX + "/runs", json=body(d, v))
    assert response.status_code == 503 and response.json()["error"]["code"] == "EVALUATION_PROFILE_UNAVAILABLE"
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_runs")[0]["n"] == 0


def test_cancel_cas_and_terminal_repeat_preserve_not_run(h):
    run, _ = queued(h)
    assert h.http.post(f"{PREFIX}/runs/{run['id']}/cancel", json={"revision": 999}).status_code == 409
    response = h.http.post(f"{PREFIX}/runs/{run['id']}/cancel", json={"revision": run["revision"]})
    assert response.status_code == 200 and response.json()["status"] == "CANCELLED"
    assert response.json()["gate_verdict"] == "NEEDS_REVIEW"
    again = h.http.post(f"{PREFIX}/runs/{run['id']}/cancel", json={"revision": run["revision"]})
    assert again.status_code == 200 and again.json()["revision"] == response.json()["revision"]
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_events WHERE event='cancel.requested'")[0]["n"] == 1


def test_queue_fences_permission_deadline_and_dispatched_unknown_capacity(h):
    run, _ = queued(h)
    queue = Queue(h.db)
    claimed = h.http.portal.call(queue.claim, uuid4())
    token = claimed["lease_token"]
    assert str(claimed["id"]) == run["id"]
    assert h.http.portal.call(queue.begin_dispatch, claimed["id"], h.owner, uuid4()) is False
    h.sql("UPDATE platform_roles SET permissions=%s WHERE id=%s RETURNING id", (Jsonb(["evaluation.read"]), h.role))
    with pytest.raises(APIError):
        h.http.portal.call(queue.begin_dispatch, claimed["id"], h.owner, token)
    assert (
        h.sql("SELECT dispatch_started FROM agent_evaluation_runs WHERE id=%s", (claimed["id"],))[0]["dispatch_started"]
        is False
    )
    h.sql("UPDATE platform_roles SET permissions=%s WHERE id=%s RETURNING id", (Jsonb(PERMISSIONS), h.role))
    h.sql(
        "UPDATE agent_evaluation_runs SET lease_until=now()-interval '1 second' WHERE id=%s RETURNING id",
        (claimed["id"],),
    )
    assert h.http.portal.call(queue.begin_dispatch, claimed["id"], h.owner, token) is False
    h.http.portal.call(queue.expire)
    renewed = h.http.portal.call(queue.claim, uuid4())
    assert renewed["lease_token"] != token
    assert h.http.portal.call(queue.begin_dispatch, renewed["id"], h.owner, renewed["lease_token"]) is True
    h.sql(
        "UPDATE agent_evaluation_runs SET lease_until=now()-interval '1 second' WHERE id=%s RETURNING id",
        (claimed["id"],),
    )
    h.http.portal.call(queue.expire)
    observed = h.sql(
        "SELECT status,gate_verdict,cleanup_status FROM agent_evaluation_runs WHERE id=%s", (claimed["id"],)
    )[0]
    assert observed == {"status": "NEEDS_REVIEW", "gate_verdict": "NEEDS_REVIEW", "cleanup_status": "RETAINED"}
    assert h.http.portal.call(queue.claim, uuid4()) is None


def test_report_attachment_owner_digest_format_and_unknown_gate(h):
    run, _ = queued(h)
    assert h.http.get(f"{PREFIX}/runs/{run['id']}/report").status_code == 409
    files = {
        "json": '{"gate":"NEEDS_REVIEW","scope":"synthetic metadata fixture"}',
        "junit": '<testsuite name="fixture"/>',
        "markdown": "# 仅测试元数据\nNEEDS_REVIEW",
    }
    manifest = {
        "kind": "agent-platform-report-v1",
        "run_id": run["id"],
        "owner_id": str(h.owner),
        "ready_verified": True,
        "files_sha256": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in files.items()},
    }
    h.sql(
        "INSERT INTO agent_evaluation_reports(id,owner_id,run_id,manifest,files,verified,redacted) "
        "VALUES(%s,%s,%s,%s,%s,true,true) RETURNING id",
        (uuid4(), h.owner, UUID(run["id"]), Jsonb(manifest), Jsonb(files)),
    )
    for format, expected in files.items():
        response = h.http.get(f"{PREFIX}/runs/{run['id']}/report", params={"format": format})
        assert response.status_code == 200 and response.content == expected.encode()
        assert response.headers["content-disposition"].startswith("attachment;")
    assert h.http.get(f"{PREFIX}/runs/{run['id']}/report", params={"format": "../private"}).status_code == 422
    h.http.cookies.set(COOKIE, h.tokens[1])
    assert h.http.get(f"{PREFIX}/runs/{run['id']}/report").status_code == 404
    h.http.cookies.set(COOKIE, h.tokens[0])
    files["json"] += "tampered"
    h.sql("UPDATE agent_evaluation_reports SET files=%s WHERE run_id=%s RETURNING id", (Jsonb(files), UUID(run["id"])))
    assert h.http.get(f"{PREFIX}/runs/{run['id']}/report").status_code == 409


def test_sample_review_is_append_only_and_cas(h):
    run, _ = queued(h)
    sample = h.http.get(f"{PREFIX}/runs/{run['id']}/samples").json()["items"][0]
    path = f"{PREFIX}/runs/{run['id']}/samples/{sample['id']}"
    before = h.http.get(path).json()
    request = {
        "revision": before["revision"],
        "decision": "CONFIRMED",
        "reason": "metadata-only evidence remains not-run",
        "rules_version": "fixture-v1",
    }
    assert h.http.post(path + "/reviews", json=request).status_code == 201
    assert h.http.post(path + "/reviews", json=request).status_code == 409
    after = h.http.get(path).json()
    assert all(after[key] == before[key] for key in ("facts", "metrics", "gate_verdict", "task_outcome", "usage"))
    assert after["review"]["original_gate"] == "PENDING" and after["revision"] == before["revision"] + 1


def test_candidate_redaction_and_review_never_execute(h):
    request = {
        "name": "synthetic authorized candidate",
        "authorization_note": "only fixture material",
        "redaction_confirmed": True,
        "material": {
            "input": "Authorization: Basic Zm9vOmJhcg==",
            "expected_behavior": "password=fixture-secret absent",
        },
    }
    preview = h.http.post(PREFIX + "/failure-candidates/preview", json=request)
    assert preview.status_code == 200
    assert "Zm9vOmJhcg==" not in preview.text and "fixture-secret" not in preview.text
    created = h.http.post(PREFIX + "/failure-candidates", json=request)
    assert created.status_code == 201, created.text
    value = created.json()
    stored = h.sql(
        "SELECT redacted_material FROM agent_evaluation_failure_candidates WHERE id=%s", (UUID(value["id"]),)
    )[0]
    assert "Zm9vOmJhcg==" not in json.dumps(stored)
    response = h.http.post(
        f"{PREFIX}/failure-candidates/{value['id']}/review",
        json={"revision": value["revision"], "decision": "APPROVED", "reason": "only reviewed metadata"},
    )
    assert response.status_code == 201 and response.json()["status"] == "APPROVED"
    assert h.sql("SELECT count(*) AS n FROM agent_evaluation_runs")[0]["n"] == 0


def test_metadata_pagination_owner_and_invalid_cursor(h):
    for i in range(3):
        dataset(h, "page-" + str(i))
    first = h.http.get(PREFIX + "/datasets", params={"limit": 2}).json()
    second = h.http.get(PREFIX + "/datasets", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1 and second["next_cursor"] is None
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    assert h.http.get(PREFIX + "/datasets", params={"cursor": "invalid"}).status_code == 422
