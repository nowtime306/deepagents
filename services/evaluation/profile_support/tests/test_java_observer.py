"""Private API-venv ASGI/SQL/manager substitutes; no real target, database or daemon."""

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import evaluation_java_observer as observer
import httpx
import pytest
from app.errors import APIError
from evaluation_target import ProfileState
from fastapi import FastAPI
from fastapi.responses import JSONResponse


@pytest.mark.parametrize("fault, status, complete", [(None, 200, True), ("permission", 403, None), ("foreign", 404, None),
    ("unsealed", 404, None), ("alias", 409, None), ("missing-provider", 200, False), ("unknown-provider", 200, False),
    ("running-phase", 200, False), ("missing-run", 200, False), ("unknown-native", 200, False), ("missing-token", 200, False),
    ("other-native-model", 200, False), ("active-work", 200, False), ("unknown-operation", 200, False),
    ("session-namespace", 200, False), ("active-session", 200, False), ("registered", 200, True),
    ("registered-with-run", 200, False), ("unknown-ledger", 200, False), ("manager-unknown", 200, False), ("manager-filter", 200, False),
    ("extra-terminal-root", 200, False), ("extra-task", 200, False), ("other-pending-native", 200, False),
    ("other-unknown-native", 200, False), ("known-extra-operation", 200, False)])
def test_fixed_observer_never_infers_confirmed_zero_from_absent_model_root(monkeypatch, fault, status, complete):
    ids = {key: str(uuid4()) for key in ("owner", "namespace", "environment", "source", "source-task", "snapshot", "task", "verifier", "artifact", "session1", "session2", "sandbox1", "sandbox2")}
    owner, task = UUID(ids["owner"]), UUID(ids["task"])
    state = ProfileState()
    state.register_model("sealed", "AET-W02", "lead", owner)
    if fault != "missing-provider":
        state.roots[(ids["owner"], "work", ids["source"])] = {"case_id": "AET-W02", "calls": 3, "tokens": 45, "pending": 0,
                                                               "unknown": fault == "unknown-provider"}
    report = {"private-canary": "not an actual protocol receipt"}
    job = {"owner_id": ids["owner"], "namespace": ids["namespace"], "source_run_id": ids["source"], "source_model_id": "sealed",
        "source_snapshot_id": ids["snapshot"], "artifact_id": ids["artifact"], "source_sha256": "b" * 64, "verifier_run_id": ids["verifier"],
        "phase": "RUNNING" if fault == "running-phase" else "COMPLETED", "report": report,
        "program_sha256": "a" * 64, "isolation_sha256": "c" * 64, "tests_sha256": "d" * 64}
    if fault in {"registered", "registered-with-run"}:
        job.update(phase="REGISTERED", verifier_run_id=None)
    elif fault == "unknown-ledger":
        job["phase"] = "UNKNOWN"
    source = {"id": ids["source"], "task_id": ids["source-task"], "model_id": "alias" if fault == "alias" else "sealed",
              "status": "COMPLETED", "result": {}, "namespace": ids["namespace"], "snapshot_id": ids["snapshot"]}
    result = {"case_id": "AET-W02", "verifier_run_id": ids["verifier"], "source_run_id": ids["source"],
              "artifact_id": ids["artifact"], "source_sha256": "b" * 64, "verification": report}
    target = {"id": ids["verifier"], "model_id": "sealed", "status": "COMPLETED", "result": result}
    queries = []

    class Database:
        async def fetch(self, sql, params=()):
            queries.append((sql, params))
            if "AS accepted_runs" in sql:
                expected = 1 if job["phase"] == "REGISTERED" else 2
                registered = [UUID(ids["source"]), *([UUID(job["verifier_run_id"])] if job["verifier_run_id"] else [])]
                assert params == (owner, owner, owner, registered, owner)
                return [{"accepted_runs": expected + int(fault == "extra-terminal-root"),
                         "accepted_tasks": 2 + int(fault == "extra-task"), "registered_runs": expected,
                         "unsettled_usage": int(fault in {"other-pending-native", "other-unknown-native"})}]
            if "AS namespace" in sql and "FROM work_queue_runs r" in sql:
                assert params == (UUID(ids["source"]), owner)
                return [source]
            if "SELECT id FROM work_tasks" in sql:
                assert params == (task, owner)
                return [] if fault == "foreign" else [{"id": task}]
            if "WHERE task_id=" in sql:
                assert params == (task, owner)
                return [] if fault in {"missing-run", "registered"} else [target]
            if "FROM model_usage" in sql:
                assert "mode='work'" in sql and params[0] == owner
                if str(params[1]) == ids["source"]:
                    return [{"call_id": str(uuid4()), "model_id": "alias" if fault == "other-native-model" else "sealed", "status": "KNOWN",
                             "input_tokens": None if fault == "missing-token" else 10, "output_tokens": 5} for _ in range(3)]
                return [{"call_id": str(uuid4()), "model_id": "sealed", "status": "UNKNOWN", "input_tokens": None, "output_tokens": None}] if fault == "unknown-native" else []
            if "FROM work_sandbox_sessions" in sql:
                assert params[1:] == (owner, owner)
                return [{"session_id": ids["session" + str(index)], "run_id": ids["source" if index == 1 else "verifier"],
                         "owner_id": ids["owner"], "namespace": str(uuid4()) if fault == "session-namespace" else ids["namespace"],
                         "sandbox_id": ids["sandbox" + str(index)], "state": "UNKNOWN" if fault == "active-session" else "RELEASED"}
                        for index in ((1,) if fault == "registered" else (1, 2))]
            if "FROM work_queue_runs WHERE owner_id=" in sql:
                return [{"count": int(fault == "active-work")}]
            if "FROM operation_executions" in sql:
                return [{"count": int(fault == "unknown-operation" or fault == "known-extra-operation" and "e.status IN" not in sql)}]
            raise AssertionError(sql)

    class Manager:
        async def list_sandbox_infos(self, filters):
            assert filters.metadata == {"namespace": ids["namespace"]}
            if fault == "manager-unknown":
                raise RuntimeError("private-canary")
            if fault == "manager-filter":
                return SimpleNamespace(sandbox_infos=[SimpleNamespace(metadata={"namespace": str(uuid4())})],
                                       pagination=SimpleNamespace(page=1, total_items=1, has_next_page=False))
            return SimpleNamespace(sandbox_infos=[], pagination=SimpleNamespace(page=1, total_items=0, has_next_page=False))

    async def manager():
        return Manager()

    async def environment(_):
        return {"owner_id": owner, "sandbox_namespace": ids["namespace"], "environment_id": ids["environment"]}

    async def permission(db, account, code):
        assert account == owner and code == "work.read"
        if fault == "permission":
            raise APIError(403, "DENIED", "forbidden")

    monkeypatch.setattr(observer, "environment", environment)
    monkeypatch.setattr(observer, "require_account", permission)
    app = FastAPI()
    app.state.db, app.state.evaluation_profiles = Database(), state
    app.state.evaluation_java_verifier = SimpleNamespace(storage_failed=False, ready=True, lock=asyncio.Lock(), jobs={} if fault == "unsealed" else {str(task): job},
        sandboxes=SimpleNamespace(storage_failed=False, _get_manager=manager))

    @app.exception_handler(APIError)
    async def errors(_, error):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code}})

    observer.attach_observer(app)

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://private-test") as http:
            return await http.get(observer.PATH + "/" + str(task) + "/observations")

    response = asyncio.run(invoke())
    assert response.status_code == status
    assert "private-canary" not in response.text
    if status == 200:
        body = response.json()
        assert body["complete"] is complete
        if fault is None:
            assert body["zero_verifier"] == {"observed": True, "confirmed": True}
            assert body["native"]["verifier"] == {"calls": 0, "reported_calls": 0, "unknown_calls": 0, "pending_calls": 0, "input_tokens": 0, "output_tokens": 0}
            assert len({row["session_id"] for row in body["sessions"]}) == 2
        elif fault == "registered":
            assert body["phase"] == "REGISTERED" and body["native"]["verifier"] is None
            assert body["zero_verifier"] == {"observed": False, "confirmed": False}
        else:
            assert body["complete"] is False
            assert body["zero_verifier"] == {"observed": False, "confirmed": False}
            if fault == "unknown-native":
                assert body["native"]["verifier"]["input_tokens"] is None
