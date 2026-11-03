"""Atomic document replacement, immutable source revisions and bounded chunk policies."""

import asyncio
import hashlib
import zipfile
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pypdf.errors import PdfReadError

from app.errors import APIError, fail
from app.permissions import require_account
from app.security import identity

router = APIRouter(prefix="/api/v1/capabilities/knowledge", tags=["知识文档运营"])


class ChunkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_size: int = Field(default=800, ge=200, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=2000)
    revision: int = Field(ge=1)

    @model_validator(mode="after")
    def overlap(self):
        if self.chunk_overlap > self.chunk_size // 2:
            raise ValueError("Overlap must not exceed half the chunk size")
        return self


@router.put("/{identifier}/processing")
async def policy(identifier: UUID, body: ChunkPolicy, request: Request):
    owner = (await identity(request))["id"]
    rows = await request.app.state.db.fetch(
        "UPDATE knowledge_bases SET chunk_size=%s,chunk_overlap=%s,processing_revision=processing_revision+1 "
        "WHERE id=%s AND owner_id=%s AND processing_revision=%s RETURNING processing_revision",
        (body.chunk_size, body.chunk_overlap, identifier, owner, body.revision),
    )
    if not rows:
        fail(409, "PROCESSING_CONFLICT", "文档策略已变化或无权访问，请刷新后再保存")
    return rows[0]


@router.get("/{identifier}/processing")
async def processing(identifier: UUID, request: Request):
    owner = (await identity(request))["id"]
    knowledge = await request.app.state.knowledge.owned(identifier, owner)
    return {
        "chunk_size": knowledge["chunk_size"],
        "chunk_overlap": knowledge["chunk_overlap"],
        "revision": knowledge["processing_revision"],
    }


async def save(service, knowledge: dict, name: str, data: bytes, *, document_id=None, revision=None):
    from app.knowledge import parse_document

    await require_account(service.database, knowledge["owner_id"], "knowledge.manage")
    current = None
    if document_id:
        rows = await service.database.fetch(
            "SELECT * FROM documents WHERE id=%s AND knowledge_id=%s", (document_id, knowledge["id"])
        )
        if not rows or rows[0]["revision"] != revision:
            fail(409, "DOCUMENT_CONFLICT", "文档已更新或不存在，请刷新后再上传")
        current = rows[0]
    try:
        sections = await asyncio.to_thread(parse_document, name, data)
    except (UnicodeError, ValueError, zipfile.BadZipFile, PdfReadError, APIError) as error:
        if current:
            await service.database.execute(
                "UPDATE documents SET last_parse_error='本次更新未通过解析，原修订仍可使用' "
                "WHERE id=%s AND revision=%s",
                (current["id"], current["revision"]),
            )
        if isinstance(error, APIError):
            raise
        fail(422, "PARSE_FAILED", "文档格式不正确或编码不是 UTF-8，原有文档保持不变")
    return await persist(service, knowledge, name, data, sections, current=current)


async def persist(
    service, knowledge, name, data, sections, *, current=None, parse_method="text", ocr_job=None, ocr_revision=None
):
    from app.knowledge import split_sections

    digest = hashlib.sha256(data).hexdigest()
    size, overlap = knowledge["chunk_size"], knowledge["chunk_overlap"]
    chunks = [(uuid4(), location, text) for location, text in split_sections(sections, size=size, overlap=overlap)]
    identifier, revision = (current["id"], current["revision"] + 1) if current else (uuid4(), 1)
    async with service.database.pool.connection() as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(710029,hashtext(%s))", (str(knowledge["id"]),))
        owned = await (
            await connection.execute(
                "SELECT processing_revision FROM knowledge_bases WHERE id=%s AND owner_id=%s FOR KEY SHARE",
                (knowledge["id"], knowledge["owner_id"]),
            )
        ).fetchone()
        if not owned or owned["processing_revision"] != knowledge["processing_revision"]:
            fail(409, "PROCESSING_CONFLICT", "文档策略已变化或授权撤销，请刷新后重试")
        if ocr_job:
            approved = await (
                await connection.execute(
                    "SELECT id FROM knowledge_ocr_jobs WHERE id=%s AND knowledge_id=%s AND owner_id=%s "
                    "AND revision=%s AND status='SUCCEEDED' FOR UPDATE",
                    (ocr_job, knowledge["id"], knowledge["owner_id"], ocr_revision),
                )
            ).fetchone()
            if not approved:
                fail(409, "OCR_REVIEW_CONFLICT", "此识别已确认或授权变化，请刷新后再操作")
        if current:
            active = await (
                await connection.execute("SELECT revision FROM documents WHERE id=%s FOR UPDATE", (identifier,))
            ).fetchone()
            if not active or active["revision"] != current["revision"]:
                fail(409, "DOCUMENT_CONFLICT", "文档已更新，请刷新后再上传")
        same = await (
            await connection.execute(
                "SELECT id,revision FROM documents WHERE knowledge_id=%s AND source_digest=%s "
                "AND chunk_size=%s AND chunk_overlap=%s ORDER BY created_at LIMIT 1",
                (knowledge["id"], digest, size, overlap),
            )
        ).fetchone()
        if same:
            if ocr_job:
                await connection.execute(
                    "UPDATE knowledge_ocr_jobs SET status='IMPORTED',document_id=%s,revision=revision+1 WHERE id=%s",
                    (same["id"], ocr_job),
                )
            return {**same, "duplicate": True, "message": "同库已有相同内容和分块策略，没有重复导入或请求向量模型"}
        if current:
            await connection.execute(
                "UPDATE documents SET name=%s,size=%s,revision=%s,source_digest=%s,chunk_size=%s,chunk_overlap=%s,"
                "status='READY',vector_status='none',error=NULL,last_parse_error=NULL,updated_at=now(),"
                "vector_model_id=NULL,vector_model_revision=NULL WHERE id=%s",
                (name, len(data), revision, digest, size, overlap, identifier),
            )
        else:
            await connection.execute(
                "INSERT INTO documents(id,knowledge_id,name,size,status,source_digest,chunk_size,chunk_overlap) "
                "VALUES(%s,%s,%s,%s,'READY',%s,%s,%s)",
                (identifier, knowledge["id"], name, len(data), digest, size, overlap),
            )
        await connection.execute(
            "INSERT INTO document_revisions"
            "(document_id,revision,name,size,source_digest,chunk_size,chunk_overlap,parse_method) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (identifier, revision, name, len(data), digest, size, overlap, parse_method),
        )
        async with connection.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO document_chunks(id,document_id,position,location,content,document_revision) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                [(chunk, identifier, i, location, text, revision) for i, (chunk, location, text) in enumerate(chunks)],
            )
        if ocr_job:
            await connection.execute(
                "UPDATE knowledge_ocr_jobs SET status='IMPORTED',document_id=%s,revision=revision+1 WHERE id=%s",
                (identifier, ocr_job),
            )
    indexed = await service.index(knowledge, identifier, chunks, revision=revision)
    return {"id": identifier, "revision": revision, "chunks": len(chunks), "duplicate": False, "index": indexed}


@router.get("/{identifier}/operations")
async def operations(identifier: UUID, request: Request):
    from app.usage_api import summaries

    owner = (await identity(request))["id"]
    db = request.app.state.db
    await request.app.state.knowledge.owned(identifier, owner)
    await db.execute(
        "UPDATE resource_operations SET status='UNKNOWN',error_code='RESOURCE_RESULT_UNKNOWN',finished_at=now() "
        "WHERE owner_id=%s AND resource_id=%s AND status='RUNNING' AND lease_until<=now()",
        (owner, identifier),
    )
    rows = await db.fetch(
        "SELECT id,action,source_id,source_revision,status,error_code,created_at FROM resource_operations "
        "WHERE owner_id=%s AND resource_id=%s ORDER BY created_at DESC LIMIT 50",
        (owner, identifier),
    )
    usage = {row["run_id"]: row for row in await summaries(db, owner, [row["id"] for row in rows])}
    return {"items": [{**row, "usage": usage.get(row["id"])} for row in rows]}


@router.put("/{identifier}/documents/{document_id}")
async def replace(identifier: UUID, document_id: UUID, request: Request, file: UploadFile, revision: int = Form(ge=1)):
    from app.capability_api import read_upload

    owner = (await identity(request))["id"]
    service = request.app.state.knowledge
    knowledge = await service.owned(identifier, owner)
    name, data = await read_upload(file)
    return await save(service, knowledge, name, data, document_id=document_id, revision=revision)


@router.get("/{identifier}/documents/{document_id}/revisions")
async def revisions(identifier: UUID, document_id: UUID, request: Request):
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT v.*,(SELECT count(*)::int FROM document_chunks c WHERE c.document_id=v.document_id "
            "AND c.document_revision=v.revision) AS chunks FROM document_revisions v "
            "JOIN documents d ON d.id=v.document_id WHERE d.id=%s AND d.knowledge_id=%s ORDER BY v.revision DESC",
            (document_id, identifier),
        )
    }


@router.get("/{identifier}/documents/{document_id}/chunks")
async def chunks(identifier: UUID, document_id: UUID, request: Request, revision: int | None = None, offset: int = 0):
    if offset < 0 or offset > 10000 or (revision is not None and revision < 1):
        fail(422, "INVALID_PAGE", "修订号或页码不正确")
    owner = (await identity(request))["id"]
    await request.app.state.knowledge.owned(identifier, owner)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT c.id,c.position,c.location,c.content,c.document_revision FROM document_chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE d.id=%s AND d.knowledge_id=%s "
            "AND c.document_revision=coalesce(%s,d.revision) ORDER BY c.position LIMIT 20 OFFSET %s",
            (document_id, identifier, revision, offset),
        )
    }
