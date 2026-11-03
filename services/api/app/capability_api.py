"""User-owned knowledge and skills, plus administrator-managed HTTP MCP tools."""

import asyncio
import zipfile
from pathlib import PurePath
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, UploadFile
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, SecretStr

from app.errors import fail
from app.knowledge import MAX_UPLOAD
from app.mcp_service import validate_target
from app.model_catalog import supports_embedding
from app.security import administrator, identity
from app.skill_library import parse_skill

router = APIRouter(prefix="/api/v1/capabilities", tags=["能力中心"])


@router.get("/embedding-models")
async def embedding_models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]} for row in rows if supports_embedding(row["kind"], row["protocol"])
        ]
    }


@router.get("/rerank-models")
async def rerank_models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "rerank" and row["protocol"] == "openai"
        ]
    }


class RerankSelection(BaseModel):
    rerank_model_id: str | None = None


class RetrievalSelection(BaseModel):
    keyword_min_coverage: float = Field(ge=0, le=1, allow_inf_nan=False)


@router.put("/knowledge/{identifier}/retrieval")
async def knowledge_retrieval(identifier: UUID, body: RetrievalSelection, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    await request.app.state.db.execute(
        "UPDATE knowledge_bases SET keyword_min_coverage=%s WHERE id=%s AND owner_id=%s",
        (body.keyword_min_coverage, identifier, user["id"]),
    )
    return {"ok": True}


@router.put("/knowledge/{identifier}/rerank")
async def knowledge_rerank(identifier: UUID, body: RerankSelection, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    if body.rerank_model_id:
        config = await request.app.state.models.get(body.rerank_model_id)
        if config.kind != "rerank" or config.protocol != "openai":
            fail(422, "MODEL_KIND_INVALID", "请选择兼容的重排模型")
    await request.app.state.db.execute(
        "UPDATE knowledge_bases SET rerank_model_id=%s WHERE id=%s AND owner_id=%s",
        (body.rerank_model_id, identifier, user["id"]),
    )
    return {"ok": True}


class KnowledgeInput(BaseModel):
    """A private knowledge base with optional vector indexing."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    embedding_model_id: str | None = None
    rerank_model_id: str | None = None


class SearchInput(BaseModel):
    """Bounded retrieval debug input."""

    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class ReindexInput(BaseModel):
    acknowledge_duplicate: bool = False


class MCPInput(BaseModel):
    """Server configuration with explicit private-network intent and write-only token."""

    name: str = Field(min_length=1, max_length=120)
    url: str = Field(default="", max_length=2000)
    token: SecretStr | None = Field(default=None, max_length=8192)
    clear_token: bool = False
    allow_private: bool = False
    enabled: bool = True
    transport: Literal["streamable_http", "sse", "stdio", "acp"] = "streamable_http"
    command: list[str] = Field(default_factory=list, max_length=32)


class ToolsInput(BaseModel):
    """Explicitly enabled read-only tools discovered from a server."""

    names: list[str] = Field(default_factory=list, max_length=100)
    allow_write_tools: bool = False


async def validate_mcp_input(body):
    if body.transport in {"stdio", "acp"}:
        from app.tool_transports import validate_command

        validate_command(body.command)
        if body.url or body.token and body.token.get_secret_value():
            fail(422, "ISOLATED_CONFIG_INVALID", "隔离程序不使用HTTP地址或Bearer Token")
    else:
        if body.command:
            fail(422, "MCP_COMMAND_INVALID", "HTTP传输不能配置执行命令")
        await validate_target(body.url, body.allow_private)


class BindingInput(BaseModel):
    """Current-user capability selection for the general assistant."""

    memory_enabled: bool | None = None
    knowledge_ids: list[UUID] = Field(default_factory=list, max_length=20)
    skill_ids: list[UUID] = Field(default_factory=list, max_length=20)
    mcp_ids: list[UUID] = Field(default_factory=list, max_length=10)


async def read_upload(file: UploadFile) -> tuple[str, bytes]:
    """Read a limited upload and never interpret the client filename as a host path."""
    try:
        data = await file.read(MAX_UPLOAD + 1)
    finally:
        await file.close()
    if not data or len(data) > MAX_UPLOAD:
        fail(413, "UPLOAD_TOO_LARGE", "文件不能为空，且不得超过 4MB")
    return PurePath((file.filename or "file").replace("\\", "/")).name[:200], data


@router.get("/knowledge")
async def knowledge_list(request: Request) -> dict:
    """List only the authenticated user's knowledge bases."""
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT k.*,count(d.id)::int AS documents FROM knowledge_bases k "
        "LEFT JOIN documents d ON d.knowledge_id=k.id WHERE k.owner_id=%s GROUP BY k.id ORDER BY k.created_at DESC",
        (user["id"],),
    )
    return {"items": rows}


@router.post("/knowledge", status_code=201)
async def knowledge_create(body: KnowledgeInput, request: Request) -> dict:
    """Create a private collection after validating any selected embedding model."""
    user = await identity(request)
    if body.embedding_model_id:
        config = await request.app.state.models.get(body.embedding_model_id)
        if not supports_embedding(config.kind, config.protocol):
            fail(422, "MODEL_KIND_INVALID", "请选择 Embedding 类型的模型")
    if body.rerank_model_id:
        config = await request.app.state.models.get(body.rerank_model_id)
        if config.kind != "rerank" or config.protocol != "openai":
            fail(422, "MODEL_KIND_INVALID", "请选择兼容的重排模型")
    identifier = uuid4()
    await request.app.state.db.execute(
        "INSERT INTO knowledge_bases(id,owner_id,name,description,embedding_model_id,rerank_model_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (identifier, user["id"], body.name, body.description, body.embedding_model_id, body.rerank_model_id),
    )
    return {"id": identifier}


@router.delete("/knowledge/{identifier}")
async def knowledge_delete(identifier: UUID, request: Request) -> dict:
    """Revoke access and remove source chunks before removing the disposable vector index."""
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    async with request.app.state.db.pool.connection() as connection:
        await connection.execute("SELECT id FROM knowledge_bases WHERE id=%s FOR UPDATE", (identifier,))
        active = await (await connection.execute(
            "SELECT 1 FROM knowledge_experiments WHERE knowledge_id=%s AND status IN ('PENDING','RUNNING') LIMIT 1",
            (identifier,),
        )).fetchone()
        if active:
            fail(409, "EVALUATION_ACTIVE", "此知识库仍有待执行或运行中的实验，请先取消并等待停止后删除")
        videos = await (await connection.execute(
            "SELECT * FROM knowledge_video_jobs WHERE knowledge_id=%s FOR UPDATE", (identifier,))).fetchall()
        if any(job["holds_memory"] or job["status"] in {"PENDING", "RUNNING", "UNKNOWN"} for job in videos):
            fail(409, "VIDEO_STILL_ACTIVE", "请先取消视频处理并等待执行环境确认停止，再删除知识库")
        from app.media_retrieval_video import directory as video_directory

        for job in videos:
            folder = video_directory(request.app.state.settings, job["id"])
            paths = list(folder.rglob("*")) if folder.exists() else []
            if folder.is_symlink() or folder.is_junction() or any(p.is_symlink() or p.is_junction() for p in paths):
                fail(409, "VIDEO_PATH_UNSAFE", "资料路径异常，未删除知识库")
            for path in sorted(paths, key=lambda p: len(p.parts), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            if folder.exists():
                folder.rmdir()
        from app.knowledge_ocr import purge

        ocr_attachments = await purge(connection, identifier, request.app.state.runtime.saver)
        cursor = await connection.execute("SELECT id FROM knowledge_images WHERE knowledge_id=%s", (identifier,))
        images = await cursor.fetchall()
        await connection.execute("DELETE FROM knowledge_bases WHERE id=%s", (identifier,))
    vectors = request.app.state.knowledge.vectors
    collections = await vectors.get_collections()
    for collection in collections.collections:
        if collection.name == f"kb_{identifier}" or collection.name.startswith(f"kb_{identifier}_"):
            await vectors.delete_collection(collection.name)
    if await vectors.collection_exists(f"kb_images_{identifier}"):
        await vectors.delete_collection(f"kb_images_{identifier}")
    for image in images:
        (request.app.state.settings.data_dir / "knowledge-images" / str(image["id"])).unlink(missing_ok=True)
    for attachment in ocr_attachments:
        (request.app.state.settings.data_dir / "attachments" / str(attachment)).unlink(missing_ok=True)
    return {"ok": True}


@router.get("/knowledge/{identifier}/documents")
async def document_list(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    return {
        "items": await request.app.state.db.fetch(
            "SELECT d.*,count(c.id)::int AS chunks FROM documents d "
            "LEFT JOIN document_chunks c ON c.document_id=d.id AND c.document_revision=d.revision "
            "WHERE d.knowledge_id=%s "
            "GROUP BY d.id ORDER BY d.created_at DESC",
            (identifier,),
        )
    }


@router.post("/knowledge/{identifier}/documents", status_code=201)
async def document_upload(identifier: UUID, request: Request, file: UploadFile) -> dict:
    user = await identity(request)
    knowledge = await request.app.state.knowledge.owned(identifier, user["id"])
    name, data = await read_upload(file)
    return await request.app.state.knowledge.upload(knowledge, name, data)


@router.delete("/knowledge/{identifier}/documents/{document_id}")
async def document_delete(identifier: UUID, document_id: UUID, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "DELETE FROM documents WHERE id=%s AND knowledge_id=%s RETURNING id", (document_id, identifier)
    )
    if not rows:
        fail(404, "NOT_FOUND", "文档不存在")
    # Retrieval always rechecks SQL source rows, so stale points cannot surface deleted content.
    return {"ok": True}


@router.post("/knowledge/{identifier}/documents/{document_id}/reindex")
async def document_reindex(
    identifier: UUID, document_id: UUID, request: Request, body: ReindexInput | None = None
) -> dict:
    user = await identity(request)
    service = request.app.state.knowledge
    knowledge = await service.owned(identifier, user["id"])
    rows = await service.database.fetch(
        "SELECT c.* FROM document_chunks c JOIN documents d ON d.id=c.document_id "
        "WHERE d.id=%s AND d.knowledge_id=%s AND c.document_revision=d.revision ORDER BY c.position",
        (document_id, identifier),
    )
    if not rows:
        fail(404, "NOT_FOUND", "文档不存在")
    result = await service.index(
        knowledge, document_id, [(row["id"], row["location"], row["content"]) for row in rows],
        revision=rows[0]["document_revision"],
        acknowledge_duplicate=bool(body and body.acknowledge_duplicate),
    )
    return {"ok": True, "index": result}


@router.post("/knowledge/{identifier}/search")
async def knowledge_search(identifier: UUID, body: SearchInput, request: Request) -> dict:
    user = await identity(request)
    return await request.app.state.knowledge.search(identifier, user["id"], body.query, body.limit)


@router.get("/skills")
async def skill_list(request: Request) -> dict:
    user = await identity(request)
    return {
        "items": await request.app.state.db.fetch(
            "SELECT id,name,description,version,enabled,created_at "
            "FROM skills WHERE owner_id=%s ORDER BY created_at DESC",
            (user["id"],),
        )
    }


@router.post("/skills", status_code=201)
async def skill_upload(request: Request, file: UploadFile) -> dict:
    user = await identity(request)
    filename, data = await read_upload(file)
    try:
        name, description, files = await asyncio.to_thread(parse_skill, filename, data)
    except (UnicodeError, zipfile.BadZipFile):
        fail(422, "INVALID_SKILL", "技能包需为有效 ZIP，资源需为 UTF-8 文本")
    rows = await request.app.state.db.fetch(
        "INSERT INTO skills(id,owner_id,name,description,files) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(owner_id,name) DO UPDATE SET description=excluded.description,"
        "files=excluded.files,version=skills.version+1 RETURNING id,version",
        (uuid4(), user["id"], name, description, Jsonb(files)),
    )
    return rows[0]


@router.get("/skills/{identifier}")
async def skill_detail(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT * FROM skills WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "技能不存在或无权访问")
    return rows[0]


@router.patch("/skills/{identifier}")
async def skill_toggle(identifier: UUID, request: Request, enabled: bool) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "UPDATE skills SET enabled=%s WHERE id=%s AND owner_id=%s RETURNING id", (enabled, identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "技能不存在或无权访问")
    return {"ok": True}


@router.delete("/skills/{identifier}")
async def skill_delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM skills WHERE id=%s AND owner_id=%s RETURNING id", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "技能不存在或无权访问")
    return {"ok": True}


@router.get("/mcp")
async def mcp_list(request: Request) -> dict:
    user = await identity(request)
    fields = "id,name,enabled,status,tools,transport,oauth_required"
    if user["is_admin"]:
        fields += ",url,allow_private,command_json,(token_ciphertext<>'') AS token_configured"
    return {"items": await request.app.state.db.fetch(f"SELECT {fields} FROM mcp_servers ORDER BY created_at DESC")}


@router.post("/mcp", status_code=201)
async def mcp_create(body: MCPInput, request: Request) -> dict:
    await administrator(request)
    await validate_mcp_input(body)
    identifier = uuid4()
    token = request.app.state.models.vault.encrypt(body.token.get_secret_value()) if body.token else ""
    try:
        await request.app.state.db.execute(
            "INSERT INTO mcp_servers(id,name,url,token_ciphertext,allow_private,enabled,transport,command_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (identifier, body.name, body.url, token, body.allow_private, body.enabled, body.transport,
             Jsonb(body.command)),
        )
    except UniqueViolation:
        fail(409, "NAME_CONFLICT", "MCP 名称已存在")
    return {"id": identifier}


@router.put("/mcp/{identifier}")
async def mcp_update(identifier: UUID, body: MCPInput, request: Request) -> dict:
    await administrator(request)
    await validate_mcp_input(body)
    rows = await request.app.state.db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (identifier,))
    if not rows:
        fail(404, "NOT_FOUND", "MCP 服务不存在")
    encrypted = rows[0]["token_ciphertext"]
    if body.clear_token:
        encrypted = ""
    elif body.token and body.token.get_secret_value():
        encrypted = request.app.state.models.vault.encrypt(body.token.get_secret_value())
    try:
        await request.app.state.db.execute(
            "UPDATE mcp_servers SET name=%s,url=%s,token_ciphertext=%s,"
            "allow_private=%s,enabled=%s,transport=%s,command_json=%s,status='not_tested',tools='[]' WHERE id=%s",
            (body.name, body.url, encrypted, body.allow_private, body.enabled, body.transport,
             Jsonb(body.command), identifier),
        )
    except UniqueViolation:
        fail(409, "NAME_CONFLICT", "MCP 名称已存在")
    return {"id": identifier}


@router.post("/mcp/{identifier}/discover")
async def mcp_discover(identifier: UUID, request: Request) -> dict:
    user = await administrator(request)
    rows = await request.app.state.db.fetch("SELECT * FROM mcp_servers WHERE id=%s", (identifier,))
    if not rows:
        fail(404, "NOT_FOUND", "MCP 服务不存在")
    if rows[0]["transport"] in {"stdio", "acp"}:
        fail(422, "MCP_ISOLATED_DISCOVERY", "请绑定到自主任务并批准隔离程序发现，Ask不执行stdio或ACP")
    try:
        options = {"owner_id": user["id"]} if rows[0].get("oauth_required") else {}
        tools = await request.app.state.mcp.discover(rows[0], **options)
    except Exception:
        await request.app.state.db.execute("UPDATE mcp_servers SET status='unavailable' WHERE id=%s", (identifier,))
        fail(502, "MCP_CONNECTION_FAILED", "MCP 连接失败，请检查地址、鉴权和网络设置")
    await request.app.state.db.execute(
        "UPDATE mcp_servers SET tools=%s,status='available' WHERE id=%s", (Jsonb(tools), identifier)
    )
    return {"items": tools}


@router.put("/mcp/{identifier}/tools")
async def mcp_tools(identifier: UUID, body: ToolsInput, request: Request) -> dict:
    await administrator(request)
    rows = await request.app.state.db.fetch("SELECT tools FROM mcp_servers WHERE id=%s", (identifier,))
    if not rows:
        fail(404, "NOT_FOUND", "MCP 服务不存在")
    tools = rows[0]["tools"]
    permitted = {item["name"] for item in tools}
    if not set(body.names) <= permitted:
        fail(422, "MCP_TOOL_UNKNOWN", "只能启用已发现的工具")
    if any(item["name"] in body.names and not item["read_only"] for item in tools) and not body.allow_write_tools:
        fail(422, "MCP_WRITE_CONFIRM_REQUIRED", "启用写工具需明确确认；写工具仅能在自主任务逐次审批后执行")
    for item in tools:
        item["enabled"] = item["name"] in body.names
    await request.app.state.db.execute("UPDATE mcp_servers SET tools=%s WHERE id=%s", (Jsonb(tools), identifier))
    return {"ok": True}


@router.delete("/mcp/{identifier}")
async def mcp_delete(identifier: UUID, request: Request) -> dict:
    await administrator(request)
    await request.app.state.db.execute("DELETE FROM mcp_servers WHERE id=%s", (identifier,))
    return {"ok": True}


@router.get("/bindings")
async def bindings_get(request: Request) -> dict:
    user = await identity(request)
    return await request.app.state.capabilities.bindings(user["id"])


@router.put("/bindings")
async def bindings_put(body: BindingInput, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.capabilities.validate(user["id"], body.model_dump(mode="json"))
    await request.app.state.db.execute(
        "INSERT INTO capability_bindings(owner_id,knowledge_ids,skill_ids,mcp_ids,memory_enabled) "
        "VALUES (%s,%s,%s,%s,COALESCE(%s,false))"
        " ON CONFLICT(owner_id) DO UPDATE SET knowledge_ids=excluded.knowledge_ids,"
        "skill_ids=excluded.skill_ids,mcp_ids=excluded.mcp_ids,"
        "memory_enabled=COALESCE(%s,capability_bindings.memory_enabled),"
        "updated_at=now()",
        (
            user["id"],
            Jsonb(body.model_dump(mode="json")["knowledge_ids"]),
            Jsonb(body.model_dump(mode="json")["skill_ids"]),
            Jsonb(body.model_dump(mode="json")["mcp_ids"]),
            body.memory_enabled,
            body.memory_enabled,
        ),
    )
    return {"ok": True}
