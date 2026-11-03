import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.errors import APIError
from evaluation_target import ProfileState
from evaluation_work_consumer import ConsumerController, Source


def controller(tmp_path):
    owner, root, namespace, environment, checkpoint = [str(uuid4()) for _ in range(5)]
    state = ProfileState(tmp_path / "providers.json")
    state.register_model("sealed", "AET-W04", "lead", owner)
    state.roots[(owner, "work", root)] = {"case_id": "AET-W04", "calls": 1, "tokens": 15,
                                          "pending": 0, "unknown": False}
    row = {"model_id": "sealed", "status": "WAITING_APPROVAL", "lease_token": None, "lease_until": None,
           "cancel_requested": False, "namespace": namespace, "sessions": 1, "released": 1, "active": 0,
           "operations": 0, "calls": 1, "reported": 1}

    class Database:
        def __init__(self):
            self.workers, self.ticks = set(), 0

        async def fetch(self, sql, params=()):
            if "FROM work_queue_runs r" in sql:
                assert str(params[0]) == root and str(params[1]) == owner
                return [deepcopy(row)]
            if "work_worker_instances WHERE id=" in sql:
                return [{"id": value} for value in self.workers if value == str(params[0])]
            if "work_worker_instances" in sql:
                return [{"id": value} for value in self.workers]
            raise AssertionError(sql)

        async def execute(self, sql, params):
            identifier = str(params[0])
            if sql.startswith("INSERT INTO work_worker_instances"):
                self.workers.add(identifier)
            elif sql.startswith("DELETE FROM work_worker_instances"):
                self.workers.discard(identifier)
            else:
                raise AssertionError(sql)

    database = Database()

    class Saver:
        identifier = checkpoint

        async def aget_tuple(self, config):
            assert config == {"configurable": {"thread_id": "work:" + root}}
            return SimpleNamespace(checkpoint={"id": self.identifier})

    async def execute(run):
        raise AssertionError("No business execution is authorized while waiting for approval")

    engine = SimpleNamespace(database=database, execute=execute, saver=Saver())
    service = ConsumerController(engine, state, SimpleNamespace(storage_failed=False, attempts=1), tmp_path / "consumers.json")
    proof = {"owner_id": owner, "environment_id": environment, "sandbox_namespace": namespace}
    return service, proof, root, row


def run(coroutine):
    return asyncio.run(coroutine)


def test_real_serve_generations_preserve_original_checkpoint_without_business_dispatch(tmp_path, monkeypatch):
    # Only queue availability is controlled. serve/register/stop/delete are the actual WorkWorker methods.
    from evaluation_work_consumer import ObservedWorker

    async def tick(worker):
        worker.database.ticks += 1
        return False

    monkeypatch.setattr(ObservedWorker, "tick", tick)
    service, proof, root, _ = controller(tmp_path)

    async def scenario():
        service.start()
        try:
            before_object, original_engine = service.worker, service.engine
            await service.registered()
            result = await service.rebuild(proof, root)
            assert result["status"] == "REBUILT"
            assert result["before_worker_ids"] != result["after_worker_ids"]
            assert result["checkpoint_id_before"] == result["checkpoint_id_after"]
            assert service.worker is not before_object and service.engine is original_engine
            assert service.engine.events is service.worker
            assert service.db.workers == set(result["after_worker_ids"])
            with pytest.raises(APIError, match="只可读取"):
                await service.rebuild(proof, root)
            rebuilt = ConsumerController(service.engine, service.profiles, service.sandboxes, service.path)
            assert rebuilt.records == service.records
        finally:
            await service.close()
        assert not service.db.workers

    run(scenario())


@pytest.mark.parametrize("change", ["lease_token", "lease_until", "cancel_requested", "sessions", "released", "active", "operations", "reported", "status"])
def test_rebuild_rejects_not_quiescent_source_without_stopping_or_writing_intent(tmp_path, change):
    service, proof, root, row = controller(tmp_path)
    row[change] = {"lease_token": uuid4(), "lease_until": "active", "cancel_requested": True, "sessions": 2,
                   "released": 0, "active": 1, "operations": 1, "reported": 0, "status": "RUNNING"}[change]
    service.worker = SimpleNamespace(active_run_id=None)
    with pytest.raises(APIError):
        run(service.rebuild(proof, root))
    assert not service.records and not service.path.exists()


@pytest.mark.parametrize("change", ["unknown", "pending", "active", "binding", "sandbox", "namespace"])
def test_rebuild_cannot_bypass_provider_binding_or_namespace(tmp_path, change):
    service, proof, root, _ = controller(tmp_path)
    service.worker = SimpleNamespace(active_run_id=None)
    if change in {"unknown", "pending"}:
        service.profiles.roots[(proof["owner_id"], "work", root)][change] = True if change == "unknown" else 1
    elif change == "active":
        service.worker.active_run_id = str(uuid4())
    elif change == "binding":
        service.profiles.models["sealed"]["case_id"] = "AET-W03"
    elif change == "sandbox":
        service.sandboxes.attempts = 2
    else:
        proof["sandbox_namespace"] = str(uuid4())
    with pytest.raises(APIError):
        run(service.rebuild(proof, root))
    assert not service.records


def test_rebuilding_unknown_is_durable_and_never_repeated(tmp_path):
    service, proof, root, _ = controller(tmp_path)
    service.records[root] = {"status": "REBUILDING"}
    service.save()
    restored = ConsumerController(service.engine, service.profiles, service.sandboxes, service.path)
    with pytest.raises(APIError):
        run(restored.rebuild(proof, root))
    assert restored.worker is None


@pytest.mark.parametrize("payload", [{"source_run_id": 1}, {"source_run_id": "bad"}, {"source_run_id": str(uuid4()), "command": "x"}])
def test_only_a_typed_source_identifier_is_accepted(payload):
    with pytest.raises(ValueError):
        Source.model_validate(payload)


@pytest.mark.parametrize("authorized, owned, complete, expected", [(True, True, True, 200), (False, True, True, 403),
    (True, False, True, 404), (True, True, False, 409)])
def test_target_only_receipt_requires_current_attested_owner_and_complete_intent(tmp_path, monkeypatch, authorized, owned, complete, expected):
    import evaluation_work_consumer as module
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    service, proof, root, _ = controller(tmp_path)
    calls = []
    service.records[root] = {"owner_id": proof["owner_id"], "environment_id": proof["environment_id"],
        "namespace": proof["sandbox_namespace"], "status": "REBUILT" if complete else "REBUILDING"}

    async def environment(_):
        return proof

    async def require(_, owner, permission):
        assert owner == proof["owner_id"] and permission == "work.read"
        if not authorized:
            raise APIError(403, "DENIED", "forbidden")

    async def fetch(sql, params):
        calls.append(params)
        assert "owner_id=%s" in sql and params == (UUID(root), proof["owner_id"])
        return [{"id": root}] if owned else []

    from uuid import UUID

    monkeypatch.setattr(module, "environment", environment)
    monkeypatch.setattr(module, "require_account", require)
    service.db.fetch = fetch
    app = FastAPI()
    app.state.evaluation_work_consumer = service
    app.state.db = service.db

    @app.exception_handler(APIError)
    async def errors(_, error):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code}})

    module.attach_consumers(app)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            return await http.get(module.PATH + "/" + root)

    assert run(scenario()).status_code == expected
    assert len(calls) == int(authorized)
