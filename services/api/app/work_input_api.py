"""Bounded task input files, isolated storage and immutable run references."""

import hashlib
import os
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.errors import fail
from app.security import identity
from app.workspace_rpc import parts

router = APIRouter(prefix="/api/v1", tags=["任务文件"])
MAX_FILE = 1024 * 1024


def storage(request: Request, identifier: UUID) -> Path:
    directory = request.app.state.settings.data_dir / "work-inputs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / str(identifier)


async def task_owner(request: Request, identifier: UUID) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT * FROM work_tasks WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "TASK_NOT_FOUND", "任务不存在或无权访问")
    return rows[0]


@router.get("/work-tasks/{identifier}/files")
async def listing(identifier: UUID, request: Request) -> dict:
    task = await task_owner(request, identifier)
    return {
        "revision": task["revision"],
        "items": await request.app.state.db.fetch(
            "SELECT id,path,size,digest FROM work_input_files WHERE task_id=%s AND owner_id=%s AND "
            "active ORDER BY path",
            (identifier, task["owner_id"]),
        ),
    }


@router.post("/work-tasks/{identifier}/files", status_code=201)
async def upload(identifier: UUID, request: Request, path: str, revision: int) -> dict:
    task = await task_owner(request, identifier)
    try:
        segments = parts(path)
        if not segments or len(segments) > 10 or len(path) > 240:
            raise ValueError("Path too long")
        normalized = "/" + "/".join(segments)
    except ValueError:
        fail(422, "WORK_FILE_PATH_INVALID", "填写工作区内路径，如 /src/main.py，不能使用上级目录或盘符")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_FILE:
            fail(413, "WORK_FILE_TOO_LARGE", "任务输入单文件最多 1 MB")
    file_id = uuid4()
    target = storage(request, file_id)
    prepared = False
    try:
        async with request.app.state.db.pool.connection() as connection:
            locked = await (
                await connection.execute(
                    "SELECT id FROM work_tasks WHERE id=%s AND owner_id=%s AND revision=%s FOR UPDATE",
                    (identifier, task["owner_id"], revision),
                )
            ).fetchone()
            if not locked:
                fail(409, "TASK_CONFLICT", "任务已变化，请刷新后上传")
            rows = await (
                await connection.execute(
                    "SELECT path,size FROM work_input_files WHERE task_id=%s AND active AND path<>%s",
                    (identifier, normalized),
                )
            ).fetchall()
            paths = {
                "/".join(row["path"].split("/")[:i])
                for row in [*rows, {"path": normalized}]
                for i in range(2, len(row["path"].split("/")) + 1)
            }
            if len(rows) >= 100 or len(paths) > 250 or sum(row["size"] for row in rows) + len(data) > 20 * MAX_FILE:
                fail(413, "WORK_INPUT_LIMIT", "任务最多 100 个输入文件、250 个目录与文件条目、合计 20 MB")
            for row in rows:
                if row["path"].startswith(normalized + "/") or normalized.startswith(row["path"] + "/"):
                    fail(409, "WORK_INPUT_CONFLICT", "文件路径与已有目录或文件冲突")
            with target.open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            await connection.execute(
                "UPDATE work_input_files SET active=false WHERE task_id=%s AND path=%s AND active",
                (identifier, normalized),
            )
            await connection.execute(
                "INSERT INTO work_input_files(id,task_id,owner_id,path,size,digest) VALUES(%s,%s,%s,%s,%s,%s)",
                (file_id, identifier, task["owner_id"], normalized, len(data), hashlib.sha256(data).hexdigest()),
            )
            revision = (
                await (
                    await connection.execute(
                        "UPDATE work_tasks SET revision=revision+1,updated_at=now() WHERE id=%s RETURNING revision",
                        (identifier,),
                    )
                ).fetchone()
            )["revision"]
            prepared = True
    except BaseException:
        if not prepared:
            target.unlink(missing_ok=True)
        raise
    return {"id": file_id, "revision": revision}


@router.delete("/work-tasks/{identifier}/files/{file_id}")
async def remove(identifier: UUID, file_id: UUID, request: Request, revision: int) -> dict:
    task = await task_owner(request, identifier)
    async with request.app.state.db.pool.connection() as connection:
        locked = await (
            await connection.execute(
                "SELECT id FROM work_tasks WHERE id=%s AND owner_id=%s AND revision=%s FOR UPDATE",
                (identifier, task["owner_id"], revision),
            )
        ).fetchone()
        if not locked:
            fail(409, "TASK_CONFLICT", "任务已变化，请刷新后移除")
        rows = await (
            await connection.execute(
                "UPDATE work_input_files SET active=false WHERE id=%s AND task_id=%s AND owner_id=%s "
                "AND active RETURNING id",
                (file_id, identifier, task["owner_id"]),
            )
        ).fetchall()
        if not rows:
            fail(404, "WORK_FILE_NOT_FOUND", "输入文件不存在")
        revision = (
            await (
                await connection.execute(
                    "UPDATE work_tasks SET revision=revision+1,updated_at=now() WHERE id=%s RETURNING revision",
                    (identifier,),
                )
            ).fetchone()
        )["revision"]
    return {"revision": revision}


@router.get("/work-input-files/{identifier}/download")
async def download(identifier: UUID, request: Request) -> Response:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT path,size,digest FROM work_input_files WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "WORK_FILE_NOT_FOUND", "输入文件不存在或无权访问")
    try:
        content = storage(request, identifier).read_bytes()
    except OSError:
        fail(503, "WORK_FILE_UNAVAILABLE", "输入文件暂不可用")
    if len(content) != rows[0]["size"] or hashlib.sha256(content).hexdigest() != rows[0]["digest"]:
        fail(503, "WORK_FILE_CORRUPTED", "输入文件校验失败")
    filename = quote(rows[0]["path"].split("/")[-1], safe="")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
