"""One fixed W04 cooperative consumer rebuild; never a process restart or run replay."""

import asyncio
import json
import os
from uuid import UUID

from app.agent_evaluation_environment import environment
from app.errors import fail
from app.permissions import require_account
from app.work_worker import WorkWorker
from fastapi import Request
from pydantic import BaseModel, ConfigDict, field_validator

PATH = "/api/v1/agent-evaluations/profiles/work-counter-consumers"


class Source(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    source_run_id: str

    @field_validator("source_run_id")
    @classmethod
    def identifier(cls, value):
        return str(UUID(value))


class ObservedWorker(WorkWorker):
    """Keep the genuine serve/register/perform behavior observable to its fixed owner."""

    identifier = None
    active_run_id = None

    async def register(self, identifier):
        self.identifier = str(identifier)
        await super().register(identifier)

    async def perform(self, run):
        self.active_run_id = str(run["id"])
        try:
            await super().perform(run)
        finally:
            self.active_run_id = None


class ConsumerController:
    def __init__(self, engine, profiles, sandboxes, state_path, *, execute=None):
        self.engine, self.db = engine, engine.database
        self.execute = execute if execute is not None else engine.execute
        self.profiles, self.sandboxes, self.path = profiles, sandboxes, state_path
        self.lock, self.records, self.storage_failed = asyncio.Lock(), {}, False
        self.worker, self.stop, self.task = None, None, None
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > 16384:
                raise ValueError("Invalid consumer rebuild ledger")
            self.records = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.records, dict) or len(self.records) > 1:
                raise ValueError("Invalid consumer rebuild ledger")

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(self.records, stream, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            self.storage_failed = True
            raise

    def start(self):
        self.worker = ObservedWorker(self.db, self.execute)
        self.engine.events = self.worker
        self.stop = asyncio.Event()
        self.task = asyncio.create_task(self.worker.serve(self.stop))

    async def close(self):
        if self.task is not None:
            self.stop.set()
            self.task.cancel()  # Shutdown retains the real worker's conservative interruption handling.
            await asyncio.gather(self.task, return_exceptions=True)

    async def registered(self):
        async with asyncio.timeout(5):
            while True:
                if self.task.done():
                    self.task.result()
                    raise RuntimeError("Controlled Work consumer stopped")
                rows = await self.db.fetch("SELECT id FROM work_worker_instances WHERE heartbeat_at>now()-interval '30 seconds'")
                if self.worker.identifier and [str(row["id"]) for row in rows] == [self.worker.identifier]:
                    return [self.worker.identifier]
                await asyncio.sleep(0.02)

    async def checkpoint(self, run_id):
        value = await self.engine.saver.aget_tuple({"configurable": {"thread_id": "work:" + run_id}})
        if value is None:
            raise ValueError("The original durable Work checkpoint is missing")
        return str(UUID(value.checkpoint["id"]))

    async def ready_source(self, owner, run_id):
        rows = await self.db.fetch(
            "SELECT r.model_id,r.status,r.lease_token,r.lease_until,r.cancel_requested,"
            "(SELECT id FROM work_sandbox_namespace WHERE singleton) AS namespace,"
            "(SELECT count(*) FROM work_sandbox_sessions WHERE run_id=r.id) AS sessions,"
            "(SELECT count(*) FROM work_sandbox_sessions WHERE run_id=r.id AND state='RELEASED') AS released,"
            "(SELECT count(*) FROM work_queue_runs WHERE id<>r.id AND status NOT IN ('COMPLETED','FAILED','CANCELLED')) AS active,"
            "(SELECT count(*) FROM operation_executions WHERE run_id=r.id) AS operations,"
            "(SELECT count(*) FROM model_usage WHERE owner_id=r.owner_id AND mode='work' AND run_id=r.id) AS calls,"
            "(SELECT count(*) FROM model_usage WHERE owner_id=r.owner_id AND mode='work' AND run_id=r.id "
            "AND status='KNOWN' AND input_tokens=10 AND output_tokens=5) AS reported "
            "FROM work_queue_runs r WHERE r.id=%s AND r.owner_id=%s", (UUID(run_id), owner))
        if not rows:
            fail(404, "NOT_FOUND", "固定重建来源不存在")
        row = rows[0]
        with self.profiles.lock:
            binding = self.profiles.models.get(row["model_id"])
        if binding != {"case_id": "AET-W04", "role": "lead", "owner_id": str(owner)}:
            fail(403, "EVALUATION_CONSUMER_SOURCE_INVALID", "只允许本人已封存的W04模型来源")
        provider = self.profiles.root_summary(owner, "work", run_id)
        if (row["status"] != "WAITING_APPROVAL" or row["lease_token"] is not None or row["lease_until"] is not None
                or row["cancel_requested"] or row["sessions"] != 1 or row["released"] != 1
                or row["active"] or row["operations"] or row["calls"] != 1 or row["reported"] != 1
                or provider.get("recorded") is not True or provider.get("complete") is not True
                or provider.get("provider_calls") != 1 or provider.get("tokens") != 15
                or self.sandboxes.storage_failed or self.sandboxes.attempts != 1 or self.storage_failed
                or self.worker.active_run_id is not None):
            fail(409, "EVALUATION_CONSUMER_NOT_QUIESCENT", "原检查点尚未达到可重建的已知静止审批阶段")
        return row

    async def rebuild(self, proof, run_id):
        async with self.lock:
            if self.records:
                fail(409, "EVALUATION_CONSUMER_ALREADY_REQUESTED", "重建意图已存在，只可读取原收据")
            # WAITING can be visible just before the genuine perform() transport/finally cleanup returns.
            # Wait only for that same root, without cancelling it or changing the durable run.
            async with asyncio.timeout(5):
                while self.worker.active_run_id == run_id:
                    await asyncio.sleep(0.02)
            row = await self.ready_source(proof["owner_id"], run_id)
            if str(row["namespace"]) != str(proof["sandbox_namespace"]):
                fail(409, "EVALUATION_NAMESPACE_CHANGED", "原命名空间不一致")
            before, checkpoint = await self.registered(), await self.checkpoint(run_id)
            record = {"schema_version": 1, "case_id": "AET-W04", "source_run_id": run_id,
                      "owner_id": str(proof["owner_id"]), "environment_id": str(proof["environment_id"]),
                      "namespace": str(row["namespace"]), "status": "REBUILDING", "before_worker_ids": before,
                      "after_worker_ids": [], "checkpoint_id_before": checkpoint, "checkpoint_id_after": checkpoint}
            self.records[run_id] = record
            self.save()  # Unknown from this point never authorizes an automatic second rebuild.
            self.stop.set()
            await asyncio.wait_for(asyncio.shield(self.task), 5)  # Never force-cancel an in-flight operation here.
            if await self.db.fetch("SELECT id FROM work_worker_instances WHERE id=%s", (UUID(before[0]),)):
                raise ValueError("The old consumer registration was not removed")
            self.start()
            after = await self.registered()
            await self.ready_source(proof["owner_id"], run_id)
            checkpoint_after = await self.checkpoint(run_id)
            if before == after or checkpoint_after != checkpoint:
                raise ValueError("The rebuilt consumer did not preserve the original checkpoint")
            record.update(status="REBUILT", after_worker_ids=after, checkpoint_id_after=checkpoint_after)
            self.save()
            return record


def attach_consumers(app):
    async def access(request, run_id, *, write=False):
        proof = await environment(request)
        for permission in ("work.read", *(("evaluation.run", "work.execute") if write else ())):
            await require_account(request.app.state.db, proof["owner_id"], permission)
        service = getattr(request.app.state, "evaluation_work_consumer", None)
        if service is None:
            fail(503, "EVALUATION_CONSUMER_UNAVAILABLE", "固定消费者重建未启用")
        rows = await service.db.fetch("SELECT id FROM work_queue_runs WHERE id=%s AND owner_id=%s", (UUID(run_id), proof["owner_id"]))
        if not rows:
            fail(404, "NOT_FOUND", "固定来源不存在或无权访问")
        return proof, service

    @app.post(PATH + "/rebuild")
    async def rebuild(body: Source, request: Request):
        proof, service = await access(request, body.source_run_id, write=True)
        return await service.rebuild(proof, body.source_run_id)

    @app.get(PATH + "/{source_run_id}")
    async def receipt(source_run_id: UUID, request: Request):
        proof, service = await access(request, str(source_run_id))
        record = service.records.get(str(source_run_id))
        if (not record or record["owner_id"] != str(proof["owner_id"])
                or record["environment_id"] != str(proof["environment_id"])
                or record["namespace"] != str(proof["sandbox_namespace"])):
            fail(404, "NOT_FOUND", "重建收据不存在或身份已变化")
        if service.storage_failed or record["status"] != "REBUILT":
            fail(409, "EVALUATION_CONSUMER_REBUILD_UNKNOWN", "重建结果待核对，不可重复执行")
        return record
