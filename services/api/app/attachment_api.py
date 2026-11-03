"""Bounded document attachments scoped to the authenticated conversation owner."""

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.ask_images import validate_image
from app.errors import APIError, fail
from app.knowledge import MAX_UPLOAD, parse_document
from app.security import identity

router = APIRouter(prefix="/api/v1", tags=["对话附件"])


def storage(request: Request, identifier: UUID) -> Path:
    directory = request.app.state.settings.data_dir / "attachments"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / str(identifier)


async def conversation(request: Request, identifier: UUID) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT * FROM conversations WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "对话不存在或无权访问")
    return rows[0]


@router.post("/conversations/{identifier}/attachments", status_code=201)
async def upload(identifier: UUID, request: Request, name: str) -> dict:
    owned = await conversation(request, identifier)
    if owned["archived"]:
        fail(409, "CONVERSATION_ARCHIVED", "请先恢复归档对话")
    if not name or len(name) > 180 or any(c in name for c in ("/", "\\", "\x00")):
        fail(422, "INVALID_FILENAME", "附件名称无效")
    suffix = Path(name).suffix.lower()
    is_image = suffix in {".png", ".jpg", ".jpeg", ".webp"}
    if suffix not in {".txt", ".md", ".pdf", ".docx"} and not is_image:
        fail(
            422,
            "ATTACHMENT_UNSUPPORTED",
            "支持文档 TXT、Markdown、PDF、DOCX 和图片 PNG、JPEG、WebP",
        )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_UPLOAD:
            fail(413, "ATTACHMENT_TOO_LARGE", "单个附件最多 4 MB")
    if not content:
        fail(422, "EMPTY_ATTACHMENT", "附件不能为空")
    media_type = "application/octet-stream"
    extracted = ""
    if is_image:
        media_type = await asyncio.to_thread(validate_image, bytes(content))
    else:
        try:
            sections = await asyncio.to_thread(parse_document, name, bytes(content))
        except APIError:
            raise
        except Exception:
            fail(422, "ATTACHMENT_INVALID", "附件解析失败，请检查格式与编码")
        extracted = "\n\n".join(location + "\n" + text for location, text in sections)
        if len(extracted) > 40000:
            fail(413, "ATTACHMENT_TEXT_LIMIT", "附件提取文字超过 4 万字，请拆分后上传")
    attachment = uuid4()
    target = storage(request, attachment)
    prepared = False
    try:
        async with request.app.state.db.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT archived FROM conversations WHERE id=%s AND owner_id=%s FOR UPDATE",
                (identifier, owned["owner_id"]),
            )
            current = await cursor.fetchone()
            if not current or current["archived"]:
                fail(409, "CONVERSATION_CHANGED", "对话已归档或删除")
            cursor = await connection.execute(
                "SELECT count(*) AS count FROM conversation_attachments WHERE conversation_id=%s", (identifier,)
            )
            if (await cursor.fetchone())["count"] >= 20:
                fail(413, "ATTACHMENT_QUOTA", "每个对话最多保存 20 个附件")
            with target.open("xb") as output:
                output.write(content)
            await connection.execute(
                "INSERT INTO conversation_attachments(id,conversation_id,owner_id,name,size,digest,"
                "extracted,media_type) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    attachment,
                    identifier,
                    owned["owner_id"],
                    name,
                    len(content),
                    hashlib.sha256(content).hexdigest(),
                    extracted,
                    media_type,
                ),
            )
            prepared = True
    except BaseException:
        if not prepared:
            target.unlink(missing_ok=True)
        raise
    return {"id": attachment, "name": name, "size": len(content), "media_type": media_type}


@router.get("/conversations/{identifier}/attachments")
async def listing(identifier: UUID, request: Request, pending: bool = False) -> dict:
    owned = await conversation(request, identifier)
    rows = await request.app.state.db.fetch(
        "SELECT id,name,size,media_type,used_at,created_at FROM conversation_attachments "
        "WHERE conversation_id=%s AND owner_id=%s "
        "AND (NOT %s OR used_at IS NULL) ORDER BY created_at,id",
        (identifier, owned["owner_id"], pending),
    )
    return {"items": rows}


@router.get("/attachments/{identifier}/download")
async def download(identifier: UUID, request: Request, inline: bool = False) -> Response:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT name,size,digest,media_type FROM conversation_attachments WHERE id=%s AND owner_id=%s",
        (identifier, user["id"]),
    )
    if not rows:
        fail(404, "NOT_FOUND", "附件不存在或无权访问")
    try:
        content = storage(request, identifier).read_bytes()
    except OSError:
        fail(503, "ATTACHMENT_UNAVAILABLE", "附件暂不可用，请检查存储")
    if len(content) != rows[0]["size"] or hashlib.sha256(content).hexdigest() != rows[0]["digest"]:
        fail(503, "ATTACHMENT_CORRUPTED", "附件校验失败")
    image = rows[0]["media_type"].startswith("image/")
    return Response(
        content,
        media_type=rows[0]["media_type"] if image else "application/octet-stream",
        headers={
            "Content-Disposition": ("inline" if inline and image else "attachment")
            + "; filename*=UTF-8''"
            + quote(rows[0]["name"], safe=""),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/attachments/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM conversation_attachments WHERE id=%s AND owner_id=%s AND used_at IS NULL RETURNING id",
        (identifier, user["id"]),
    )
    if not rows:
        fail(409, "ATTACHMENT_IN_USE", "附件不存在、无权访问或已被对话引用")
    storage(request, identifier).unlink(missing_ok=True)
    return {"ok": True}
