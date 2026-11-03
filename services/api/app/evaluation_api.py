"""Private versioned retrieval datasets, immutable experiment snapshots and cancellation."""

import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from psycopg.types.json import Jsonb

from app.errors import fail
from app.evaluation_types import DatasetInput, ExperimentInput
from app.knowledge_pins import snapshot
from app.permissions import require_account
from app.security import identity
from app.usage_api import summaries

router = APIRouter(prefix="/api/v1/capabilities/knowledge", tags=["知识检索评测"])


async def evidence(database, knowledge, questions):
    for question in questions:
        for source in question.expected:
            documents = await database.fetch(
                "SELECT id FROM documents WHERE id=%s AND knowledge_id=%s AND revision=%s AND status='READY'",
                (source.document_id, knowledge, source.revision),
            )
            if not documents:
                fail(422, "EVALUATION_EVIDENCE_STALE", "标注文档不存在或修订已变化，请重新核对证据")
            chunks = await database.fetch(
                "SELECT id FROM document_chunks WHERE document_id=%s AND document_revision=%s AND id=ANY(%s::uuid[])",
                (source.document_id, source.revision, source.chunk_ids),
            )
            if len({row["id"] for row in chunks}) != len(set(source.chunk_ids)):
                fail(422, "EVALUATION_EVIDENCE_INVALID", "标注的分块不属于所选文档修订")


@router.get("/{identifier}/datasets")
async def datasets(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT * FROM knowledge_datasets WHERE knowledge_id=%s AND owner_id=%s ORDER BY updated_at DESC LIMIT 100",
            (identifier, owner),
        )
    }


@router.post("/{identifier}/datasets", status_code=201)
async def create(identifier: UUID, body: DatasetInput, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    if body.revision:
        fail(409, "DATASET_CONFLICT", "新题集的修订应为0")
    await evidence(request.app.state.db, identifier, body.questions)
    async with request.app.state.db.pool.connection() as connection:
        current = await (await connection.execute(
            "SELECT id FROM knowledge_bases WHERE id=%s AND owner_id=%s FOR UPDATE", (identifier, owner),
        )).fetchone()
        if not current:
            fail(404, "NOT_FOUND", "知识库不存在或无权访问")
        count = await (
            await connection.execute(
                "SELECT count(*) AS total FROM knowledge_datasets WHERE knowledge_id=%s", (identifier,)
            )
        ).fetchone()
        if count["total"] >= 100:
            fail(422, "DATASET_LIMIT", "每个知识库最多100份题集")
        result = await (
            await connection.execute(
                "INSERT INTO knowledge_datasets(id,owner_id,knowledge_id,name,dataset_type,questions) "
                "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id,revision",
                (
                    uuid4(),
                    owner,
                    identifier,
                    body.name,
                    body.dataset_type,
                    Jsonb([question.model_dump(mode="json") for question in body.questions]),
                ),
            )
        ).fetchone()
    return result


@router.post("/{identifier}/datasets/preview")
async def preview(identifier: UUID, body: DatasetInput, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    await evidence(request.app.state.db, identifier, body.questions)
    return body.model_dump(mode="json")


@router.put("/{identifier}/datasets/{dataset}")
async def update(identifier: UUID, dataset: UUID, body: DatasetInput, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    await evidence(request.app.state.db, identifier, body.questions)
    rows = await request.app.state.db.fetch(
        "UPDATE knowledge_datasets SET name=%s,dataset_type=%s,questions=%s,revision=revision+1,updated_at=now() "
        "WHERE id=%s AND knowledge_id=%s AND owner_id=%s AND revision=%s RETURNING revision",
        (
            body.name,
            body.dataset_type,
            Jsonb([question.model_dump(mode="json") for question in body.questions]),
            dataset,
            identifier,
            owner,
            body.revision,
        ),
    )
    if not rows:
        fail(409, "DATASET_CONFLICT", "题集已更新或无权访问，请刷新后再编辑")
    return rows[0]


@router.delete("/{identifier}/datasets/{dataset}")
async def delete(identifier: UUID, dataset: UUID, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    rows = await request.app.state.db.fetch(
        "DELETE FROM knowledge_datasets WHERE id=%s AND knowledge_id=%s AND owner_id=%s RETURNING id",
        (dataset, identifier, owner),
    )
    if not rows:
        fail(404, "NOT_FOUND", "题集不存在或无权访问")
    return {"ok": True}


@router.post("/{identifier}/experiments", status_code=202)
async def experiment(identifier: UUID, body: ExperimentInput, request: Request):
    from app.evaluation_types import Question

    owner = (await identity(request))["id"]
    db = request.app.state.db
    await require_account(db, owner, "knowledge.search")
    knowledge = await request.app.state.knowledge.owned(identifier, owner)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"knowledge": str(identifier), **body.model_dump(mode="json", exclude={"request_id"})}, sort_keys=True
        ).encode()
    ).hexdigest()
    previous = await db.fetch(
        "SELECT id,status,request_digest FROM knowledge_experiments "
        "WHERE owner_id=%s AND knowledge_id=%s AND request_id=%s",
        (owner, identifier, body.request_id),
    )
    if previous:
        if previous[0]["request_digest"] != fingerprint:
            fail(409, "EXPERIMENT_REQUEST_CONFLICT", "请求标识已用于不同实验参数，请刷新后重新发起")
        return {"id": previous[0]["id"], "status": previous[0]["status"], "duplicate": True}
    prepared = await db.fetch(
        "SELECT * FROM knowledge_datasets WHERE id=%s AND knowledge_id=%s AND owner_id=%s AND revision=%s",
        (body.dataset_id, identifier, owner, body.dataset_revision),
    )
    if not prepared:
        fail(409, "DATASET_CONFLICT", "题集已更新或无权访问，请刷新")
    dataset = prepared[0]
    await evidence(db, identifier, [Question.model_validate(question) for question in dataset["questions"]])
    for key in ("embedding_model_id", "rerank_model_id"):
        if knowledge[key]:
            await request.app.state.models.get(knowledge[key])
            if not body.acknowledge_model_costs:
                fail(422, "EVALUATION_COST_REVIEW", "此知识库会请求资源模型，请先确认本次实验费用和预算")
    frozen = await snapshot(db, knowledge)
    async with db.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710032,hashtext(%s))", (str(owner),))
        available = await (await connection.execute(
            "SELECT id FROM knowledge_bases WHERE id=%s AND owner_id=%s FOR KEY SHARE", (identifier, owner),
        )).fetchone()
        if not available:
            fail(404, "NOT_FOUND", "知识库不存在或无权访问")
        old = await (
            await connection.execute(
                "SELECT id,status,knowledge_id,request_digest FROM knowledge_experiments "
                "WHERE owner_id=%s AND request_id=%s",
                (owner, body.request_id),
            )
        ).fetchone()
        if old:
            if old["knowledge_id"] != identifier or old["request_digest"] != fingerprint:
                fail(409, "EXPERIMENT_REQUEST_CONFLICT", "请求标识已用于其他知识库")
            return {"id": old["id"], "status": old["status"], "duplicate": True}
        count = await (
            await connection.execute(
                "SELECT count(*) AS total FROM knowledge_experiments "
                "WHERE owner_id=%s AND status IN ('PENDING','RUNNING')",
                (owner,),
            )
        ).fetchone()
        if count["total"] >= 2:
            fail(409, "EXPERIMENT_BUSY", "最多保留两个待执行或运行中的实验，请先等待或取消")
        current = await (
            await connection.execute(
                "SELECT id FROM knowledge_datasets WHERE id=%s AND knowledge_id=%s "
                "AND owner_id=%s AND revision=%s FOR KEY SHARE",
                (body.dataset_id, identifier, owner, body.dataset_revision),
            )
        ).fetchone()
        if not current:
            fail(409, "DATASET_CONFLICT", "题集已更新或无权访问，请刷新")
        identifier_job = uuid4()
        await connection.execute(
            "INSERT INTO knowledge_experiments(id,request_id,request_digest,owner_id,knowledge_id,dataset_id,"
            "dataset_snapshot,resource_snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identifier_job,
                body.request_id,
                fingerprint,
                owner,
                identifier,
                body.dataset_id,
                Jsonb({key: dataset[key] for key in ("name", "dataset_type", "revision", "questions")}),
                Jsonb(frozen),
            ),
        )
    return {"id": identifier_job, "status": "PENDING", "duplicate": False}


@router.get("/{identifier}/experiments")
async def experiments(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    rows = await request.app.state.db.fetch(
        "SELECT id,status,operation_id,dataset_id,dataset_snapshot-'questions' AS dataset_snapshot,"
        "jsonb_array_length(dataset_snapshot->'questions') AS total_questions,"
        "jsonb_array_length(results) AS completed_questions,metrics,error_code,created_at,finished_at,"
        "encode(sha256(convert_to(resource_snapshot::text||dataset_snapshot::text,'UTF8')),'hex') AS comparison_key "
        "FROM knowledge_experiments WHERE knowledge_id=%s AND owner_id=%s ORDER BY created_at DESC LIMIT 50",
        (identifier, owner),
    )
    usage = {
        row["run_id"]: row
        for row in await summaries(
            request.app.state.db, owner, [row["operation_id"] for row in rows if row["operation_id"]]
        )
    }
    return {
        "items": [{**row, "usage": usage.get(row["operation_id"])} for row in rows],
        "worker_enabled": request.app.state.settings.evaluation_enabled,
    }


@router.get("/{identifier}/experiments/{experiment}")
async def detail(identifier: UUID, experiment: UUID, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    rows = await request.app.state.db.fetch(
        "SELECT * FROM knowledge_experiments WHERE id=%s AND knowledge_id=%s AND owner_id=%s",
        (experiment, identifier, owner),
    )
    if not rows:
        fail(404, "NOT_FOUND", "实验不存在或无权访问")
    row = rows[0]
    usage = await summaries(request.app.state.db, owner, [row["operation_id"]] if row["operation_id"] else [])
    return {**row, "usage": usage[0] if usage else None}


@router.post("/{identifier}/experiments/{experiment}/cancel")
async def cancel(identifier: UUID, experiment: UUID, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    async with request.app.state.db.pool.connection() as connection:
        row = await (
            await connection.execute(
                "UPDATE knowledge_experiments SET cancel_requested=true,"
                "finished_at=CASE WHEN status='PENDING' THEN now() ELSE finished_at END,"
                "status=CASE WHEN status='PENDING' THEN 'CANCELLED' ELSE status END "
                "WHERE id=%s AND knowledge_id=%s AND owner_id=%s "
                "AND status IN ('PENDING','RUNNING') RETURNING operation_id",
                (experiment, identifier, owner),
            )
        ).fetchone()
        if not row:
            fail(409, "EXPERIMENT_FINISHED", "实验已结束或无权访问")
        if row["operation_id"]:
            await connection.execute(
                "UPDATE resource_operations SET cancel_requested=true WHERE id=%s AND owner_id=%s",
                (row["operation_id"], owner),
            )
    return {"cancel_requested": True}
