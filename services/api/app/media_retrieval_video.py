"""Bounded private video decoding in an approved, networkless container; API owns vectors."""

import asyncio
import base64
import json
import logging
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path, PurePath
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import FileResponse
from qdrant_client import models

from app.embedding_compatibility import fingerprint
from app.errors import fail
from app.extra_cost_budget import reserve as reserve_extra
from app.extra_cost_budget import settle as settle_extra
from app.host_resources import resource_budget
from app.list_pagination import decode_cursor, page
from app.maintenance import slot
from app.multimodal_api import context, vectorize
from app.permissions import require_account
from app.resource_runtime import resource_run
from app.resource_usage import link_model_snapshot
from app.security import identity

router = APIRouter(prefix="/api/v1/capabilities/knowledge", tags=["视频帧检索"])
ROOT = Path(__file__).resolve().parents[3]
MEMORY = 512 * 1024**2
logger = logging.getLogger(__name__)


def private_path(settings, *parts):
    base = settings.data_dir.absolute()
    path = base.joinpath(*parts)
    # Checking the leaf misses a junction anywhere above the private directory.
    if any(parent.is_symlink() or parent.is_junction() for parent in (path, *path.parents)):
        fail(409, "MEDIA_PATH_UNSAFE", "媒体资料路径异常，未读取或修改")
    if not path.resolve().is_relative_to(base.resolve()):
        fail(409, "MEDIA_PATH_UNSAFE", "媒体资料路径异常，未读取或修改")
    return path


def directory(settings, identifier):
    return private_path(settings, "knowledge-videos", str(UUID(str(identifier))))


@router.post("/{identifier}/videos", status_code=202)
async def upload(
    identifier: UUID,
    request: Request,
    file: UploadFile,
    interval_seconds: int = Form(5, ge=1, le=30),
    max_frames: int = Form(12, ge=1, le=12),
):
    user = await identity(request)
    _, config = await context(identifier, request)
    data = await file.read(20 * 1024 * 1024 + 1)
    if not data or len(data) > 20 * 1024 * 1024:
        fail(413, "VIDEO_SIZE_INVALID", "视频不能为空且不能超过20MiB")
    if not (len(data) >= 12 and (data[4:8] == b"ftyp" or data.startswith(b"\x1aE\xdf\xa3"))):
        fail(422, "VIDEO_FORMAT_INVALID", "仅支持MP4或WebM视频")
    key = uuid4()
    folder = directory(request.app.state.settings, key)
    folder.mkdir(parents=True)
    private_path(request.app.state.settings, "knowledge-videos", str(key), "source").write_bytes(data)
    try:
        async with request.app.state.db.pool.connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("media-upload:" + str(user["id"]),)
            )
            capacity = await (
                await connection.execute(
                    "SELECT count(*) AS count,coalesce(sum(size),0) AS size "
                    "FROM knowledge_video_jobs WHERE owner_id=%s AND job_kind='video_index'",
                    (user["id"],),
                )
            ).fetchone()
            if capacity["count"] >= 100 or capacity["size"] + len(data) > 1024**3:
                fail(413, "VIDEO_STORAGE_LIMIT", "本人视频资料已达到100份或1GiB上限，请先删除不需要的资料")
            cursor = await connection.execute(
                "INSERT INTO knowledge_video_jobs(id,knowledge_id,owner_id,model_id,model_revision,name,size,"
                "interval_seconds,max_frames,vector_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING id,status",
                (
                    key,
                    identifier,
                    user["id"],
                    config.id,
                    config.revision,
                    PurePath((file.filename or "video").replace("\\", "/")).name[:200],
                    len(data),
                    interval_seconds,
                    max_frames,
                    fingerprint(config, preprocessing="media-v1"),
                ),
            )
            rows = await cursor.fetchall()
    except Exception:
        (folder / "source").unlink(missing_ok=True)
        folder.rmdir()
        raise
    return {**rows[0], "note": "异步抽取前60秒内最多12帧；采样时间不代表完整时序理解"}


@router.get("/{identifier}/videos")
async def jobs(identifier: UUID, request: Request, before: str | None = None):
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    timestamp, cursor = decode_cursor(before, user["id"], "knowledge-videos:" + str(identifier))
    rows = await request.app.state.db.fetch(
        "SELECT j.id,j.name,j.size,j.status,j.cancel_requested,j.interval_seconds,j.max_frames,j.error_code,"
        "j.created_at,count(f.image_id) AS indexed_frames FROM knowledge_video_jobs j "
        "LEFT JOIN knowledge_video_frames f "
        "ON f.job_id=j.id WHERE j.knowledge_id=%s AND (%s::timestamptz IS NULL OR (j.created_at,j.id)<(%s,%s)) "
        "GROUP BY j.id ORDER BY j.created_at DESC,j.id DESC LIMIT 101",
        (identifier, timestamp, timestamp, cursor),
    )
    return page(rows, 100, user["id"], "knowledge-videos:" + str(identifier))


@router.get("/{identifier}/videos/{job}/file")
async def video_file(identifier: UUID, job: UUID, request: Request):
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT name FROM knowledge_video_jobs WHERE id=%s AND knowledge_id=%s", (job, identifier)
    )
    path = private_path(request.app.state.settings, "knowledge-videos", str(job), "source")
    if not rows or not path.is_file():
        fail(404, "VIDEO_NOT_FOUND", "视频资料不存在或无权访问")
    with path.open("rb") as source:
        header = source.read(12)
    return FileResponse(
        path,
        media_type="video/mp4" if header[4:8] == b"ftyp" else "video/webm",
        filename=rows[0]["name"],
        content_disposition_type="inline",
    )


@router.post("/{identifier}/videos/{job}/cancel")
async def cancel(identifier: UUID, job: UUID, request: Request):
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "UPDATE knowledge_video_jobs SET cancel_requested=true,updated_at=now() WHERE id=%s AND knowledge_id=%s "
        "AND owner_id=%s AND status IN ('PENDING','RUNNING','UNKNOWN') RETURNING id,status",
        (job, identifier, user["id"]),
    )
    if not rows:
        fail(409, "VIDEO_NOT_ACTIVE", "视频处理不存在或已经结束")
    return {**rows[0], "note": "取消请求已保存，确认执行进程停止后才释放内存预留"}


@router.delete("/{identifier}/videos/{job}")
async def delete(identifier: UUID, job: UUID, request: Request):
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT * FROM knowledge_video_jobs WHERE id=%s AND knowledge_id=%s AND owner_id=%s",
        (job, identifier, user["id"]),
    )
    if not rows:
        fail(404, "VIDEO_NOT_FOUND", "视频资料不存在或无权访问")
    if rows[0]["holds_memory"] or rows[0]["status"] in {"PENDING", "RUNNING", "UNKNOWN"}:
        fail(409, "VIDEO_STILL_ACTIVE", "请先取消并等待执行环境已确认停止，再删除资料")
    await remove_frames(request.app.state, rows[0])
    folder = directory(request.app.state.settings, job)
    if folder.is_symlink() or folder.is_junction():
        fail(409, "VIDEO_PATH_UNSAFE", "资料路径异常，未删除")
    if folder.exists():
        for path in folder.rglob("*"):
            if path.is_symlink() or path.is_junction():
                fail(409, "VIDEO_PATH_UNSAFE", "资料路径异常，未删除")
        for path in sorted(folder.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        folder.rmdir()
    await request.app.state.db.execute("DELETE FROM knowledge_video_jobs WHERE id=%s", (job,))
    return {"ok": True}


async def docker(*args, deadline=15):
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=deadline,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("MEDIA_DOCKER_TIMEOUT") from None
    if result.returncode:
        raise RuntimeError("MEDIA_DOCKER_FAILED")
    return result.stdout.decode().strip()


async def stop(job):
    name = "owner-agent-media-" + str(job["id"])
    inventory = await docker(
        "ps", "-a", "--filter", "label=owner_agent_media_job=" + str(job["id"]), "--format", "{{.ID}}"
    )
    if not inventory:
        return
    raw = await docker("inspect", name)
    info = json.loads(raw)[0]
    if info["Config"]["Labels"].get("owner_agent_media_job") != str(job["id"]) or info["Image"] != job["image_id"]:
        raise RuntimeError("MEDIA_CONTAINER_OWNERSHIP_MISMATCH")
    if info["State"]["Running"]:
        await docker("kill", name)
    await docker("rm", name)


async def remove_frames(state, job):
    rows = await state.db.fetch("SELECT image_id FROM knowledge_video_frames WHERE job_id=%s", (job["id"],))
    if rows:
        paths = [private_path(state.settings, "knowledge-images", str(row["image_id"])) for row in rows]
        collection = f"kb_images_{job['knowledge_id']}"
        await state.knowledge.vectors.delete(
            collection, points_selector=models.PointIdsList(points=[str(row["image_id"]) for row in rows])
        )
        for row, path in zip(rows, paths, strict=True):
            await state.db.execute(
                "DELETE FROM knowledge_images WHERE id=%s AND knowledge_id=%s", (row["image_id"], job["knowledge_id"])
            )
            path.unlink(missing_ok=True)


async def claim(db):
    if fence := getattr(db, "service_fence", None):
        await fence.check()
    budget = await resource_budget()
    async with db.pool.connection() as c:
        if not await slot(c):
            return None
        if fence := getattr(db, "service_fence", None):
            await fence.check()
        await c.execute("SELECT pg_advisory_xact_lock(710016)")
        usage = await (
            await c.execute(
                "SELECT coalesce((SELECT sum(CASE WHEN kind='coding' THEN 5368709120 ELSE 3221225472 END) "
                "FROM work_sandbox_sessions WHERE state<>'RELEASED'),0)+"
                "(SELECT count(*)*536870912 FROM knowledge_video_jobs WHERE holds_memory) AS used"
            )
        ).fetchone()
        if usage["used"] + MEMORY > budget:
            return None
        return await (
            await c.execute(
                "UPDATE knowledge_video_jobs SET status='RUNNING',holds_memory=true,updated_at=now() "
                "WHERE id=(SELECT id FROM knowledge_video_jobs WHERE status='PENDING' AND job_kind='video_index' "
                "AND NOT cancel_requested "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *"
            )
        ).fetchone()


async def index_frame(state, job, path, number):
    if fence := getattr(state.db, "service_fence", None):
        await fence.check()
    live = await state.db.fetch(
        "SELECT j.id FROM knowledge_video_jobs j JOIN knowledge_bases k ON k.id=j.knowledge_id "
        "WHERE j.id=%s AND j.status='RUNNING' AND NOT j.cancel_requested AND k.owner_id=%s",
        (job["id"], job["owner_id"]),
    )
    if not live:
        raise RuntimeError("VIDEO_CANCELLED")
    await require_account(state.db, job["owner_id"], "knowledge.manage")
    config = await state.models.get(job["model_id"])
    if job.get("vector_fingerprint") != fingerprint(config, preprocessing="media-v1"):
        raise RuntimeError("VIDEO_MODEL_CHANGED")
    relative = path.absolute().relative_to(state.settings.data_dir.absolute())
    data = private_path(state.settings, *relative.parts).read_bytes()
    call = uuid4()
    async with state.db.pool.connection() as c:
        await c.execute(
            "INSERT INTO model_usage(call_id,run_id,owner_id,mode,model_id,model_revision,status) "
            "VALUES(%s,%s,%s,'resource',%s,%s,'PENDING')",
            (call, job["id"], job["owner_id"], config.id, config.revision),
        )
        await link_model_snapshot(c, call, job["owner_id"], job["id"], config.id)
        await reserve_extra(c, job["owner_id"], call, config.id, "call", Decimal(1))
        await reserve_extra(c, job["owner_id"], call, config.id, "image", Decimal(1), require_price=True)
    try:
        vector = await vectorize(config, {"image": "data:image/png;base64," + base64.b64encode(data).decode()})
    except Exception:
        await state.db.execute("UPDATE model_usage SET status='UNKNOWN',finished_at=now() WHERE call_id=%s", (call,))
        raise
    if fence := getattr(state.db, "service_fence", None):
        await fence.check()
    async with state.db.pool.connection() as c:
        await settle_extra(c, call, "image", Decimal(1))
        await settle_extra(c, call, "call", Decimal(1))
        await c.execute("UPDATE model_usage SET status='UNKNOWN',finished_at=now() WHERE call_id=%s", (call,))
    async with state.db.pool.connection() as c:
        # Lock authorization, model and knowledge ownership through the index commit. Changes
        # that completed while vectorize was pending must invalidate this response.
        account = await (await c.execute(
            "SELECT u.enabled,u.is_admin,u.approval_status,u.must_change_password,r.enabled AS role_enabled,"
            "r.permissions FROM users u JOIN platform_roles r ON r.id=u.role_id "
            "WHERE u.id=%s FOR SHARE OF u,r", (job["owner_id"],))).fetchone()
        if (not account or not account["enabled"] or not account["role_enabled"]
                or account["approval_status"] != "approved" or account["must_change_password"]
                or (not account["is_admin"] and "knowledge.manage" not in account["permissions"])):
            raise RuntimeError("VIDEO_PERMISSION_CHANGED")
        model = await (await c.execute(
            "SELECT enabled,revision,kind,protocol FROM model_configs WHERE id=%s FOR SHARE", (config.id,))).fetchone()
        if (not model or not model["enabled"]
                or (model["kind"], model["protocol"]) != (config.kind, config.protocol)):
            raise RuntimeError("VIDEO_MODEL_CHANGED")
        knowledge = await (await c.execute(
            "SELECT owner_id FROM knowledge_bases WHERE id=%s FOR SHARE", (job["knowledge_id"],))).fetchone()
        if not knowledge or knowledge["owner_id"] != job["owner_id"]:
            raise RuntimeError("VIDEO_OWNER_CHANGED")
        row = await (
            await c.execute(
                "SELECT j.cancel_requested,i.* FROM knowledge_video_jobs j JOIN knowledge_image_indexes i "
                "ON i.knowledge_id=j.knowledge_id WHERE j.id=%s AND j.status='RUNNING' FOR UPDATE OF j,i",
                (job["id"],),
            )
        ).fetchone()
        if not row or row["cancel_requested"]:
            raise RuntimeError("VIDEO_CANCELLED")
        if (row["model_id"] != config.id
                or row.get("vector_fingerprint") != fingerprint(config, preprocessing="media-v1")):
            raise RuntimeError("VIDEO_MODEL_CHANGED")
        if row["dimensions"] and row["dimensions"] != len(vector):
            raise RuntimeError("VECTOR_DIMENSION_MISMATCH")
        key, collection = uuid4(), f"kb_images_{job['knowledge_id']}"
        if not await state.knowledge.vectors.collection_exists(collection):
            await state.knowledge.vectors.create_collection(
                collection, vectors_config=models.VectorParams(size=len(vector), distance=models.Distance.COSINE)
            )
        target = private_path(state.settings, "knowledge-images", str(key))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        try:
            if fence := getattr(state.db, "service_fence", None):
                await fence.check()
            await state.knowledge.vectors.upsert(collection, points=[models.PointStruct(id=str(key), vector=vector)])
            await c.execute(
                "INSERT INTO knowledge_images(id,knowledge_id,name,media_type,size) VALUES(%s,%s,%s,'image/png',%s)",
                (key, job["knowledge_id"], f"{job['name']} · 采样{number * job['interval_seconds']}秒", len(data)),
            )
            await c.execute(
                "INSERT INTO knowledge_video_frames VALUES(%s,%s,%s)",
                (key, job["id"], number * job["interval_seconds"]),
            )
            await c.execute(
                "UPDATE knowledge_image_indexes SET dimensions=%s WHERE knowledge_id=%s",
                (len(vector), job["knowledge_id"]),
            )
            if fence := getattr(state.db, "service_fence", None):
                await fence.check()
        except Exception:
            target.unlink(missing_ok=True)
            await state.knowledge.vectors.delete(collection, points_selector=models.PointIdsList(points=[str(key)]))
            raise


async def process(state, job):
    async with resource_run(state.db, job["owner_id"], "resource", job["id"]):
        return await _process(state, job)


async def _process(state, job):
    rows = await state.db.fetch("SELECT cancel_requested FROM knowledge_video_jobs WHERE id=%s", (job["id"],))
    if not rows or rows[0]["cancel_requested"]:
        await state.db.execute("UPDATE knowledge_video_jobs SET holds_memory=false WHERE id=%s", (job["id"],))
        raise RuntimeError("VIDEO_CANCELLED")
    source = private_path(state.settings, "knowledge-videos", str(job["id"]), "source")
    output = private_path(state.settings, "knowledge-videos", str(job["id"]), "frames")
    output.mkdir(exist_ok=True)
    registered = json.loads((ROOT / ".local/media-decoder-image.json").read_text())
    image = registered["image_id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RuntimeError("MEDIA_IMAGE_NOT_APPROVED")
    await state.db.execute("UPDATE knowledge_video_jobs SET image_id=%s WHERE id=%s", (image, job["id"]))
    job = {**job, "image_id": image}
    name = "owner-agent-media-" + str(job["id"])
    await state.db.execute("UPDATE knowledge_video_jobs SET create_attempted=true WHERE id=%s", (job["id"],))
    cid = await docker(
        "create",
        "--pull=never",
        "--name",
        name,
        "--label",
        "owner_agent_media_job=" + str(job["id"]),
        "--memory",
        "512m",
        "--cpus",
        ".5",
        "--pids-limit",
        "64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:size=32m",
        "--mount",
        f"type=bind,source={source},target=/input/source,readonly",
        "--mount",
        f"type=bind,source={output},target=/output",
        image,
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        "-threads",
        "1",
        "-i",
        "/input/source",
        "-t",
        "60",
        "-vf",
        f"fps=fps=1/{job['interval_seconds']}:start_time=0,scale=512:512:force_original_aspect_ratio=decrease",
        "-frames:v",
        str(job["max_frames"]),
        "/output/frame-%03d.png",
    )
    await state.db.execute("UPDATE knowledge_video_jobs SET container_id=%s WHERE id=%s", (cid, job["id"]))
    decoding = asyncio.create_task(docker("start", "-a", name, deadline=90))
    try:
        while not decoding.done():
            await asyncio.sleep(0.5)
            rows = await state.db.fetch("SELECT cancel_requested FROM knowledge_video_jobs WHERE id=%s", (job["id"],))
            if not rows or rows[0]["cancel_requested"]:
                raise RuntimeError("VIDEO_CANCELLED")
        await decoding
        paths = sorted(output.glob("frame-*.png"))
        if not paths or len(paths) > job["max_frames"]:
            raise RuntimeError("VIDEO_FRAMES_INVALID")
        for index, path in enumerate(paths):
            await index_frame(state, job, path, index)
    finally:
        await stop(job)
        await state.db.execute("UPDATE knowledge_video_jobs SET holds_memory=false WHERE id=%s", (job["id"],))
        if not decoding.done():
            decoding.cancel()
            await asyncio.gather(decoding, return_exceptions=True)


async def recover(state, *, startup=False):
    # Keep maintenance admission for the entire cleanup, including external stop and files.
    async with state.db.pool.connection() as admission:
        if not await slot(admission):
            return False
        if fence := getattr(state.db, "service_fence", None):
            await fence.check()
        if startup:
            await state.db.execute("UPDATE knowledge_video_jobs SET status='UNKNOWN' WHERE status='RUNNING'")
        interrupted = await state.db.fetch("SELECT * FROM knowledge_video_jobs WHERE status='UNKNOWN' LIMIT 10")
        for previous in interrupted:
            try:
                if fence := getattr(state.db, "service_fence", None):
                    await fence.check()
                if previous["create_attempted"]:
                    await stop(previous)
                if fence := getattr(state.db, "service_fence", None):
                    await fence.check()
                await remove_frames(state, previous)
                if previous["job_kind"] == "probe":
                    folder = directory(state.settings, previous["id"])
                    if folder.is_symlink() or folder.is_junction():
                        raise RuntimeError("MEDIA_PATH_UNSAFE")
                    source = private_path(state.settings, "knowledge-videos", str(previous["id"]), "source")
                    source.unlink(missing_ok=True)
                    if folder.exists():
                        folder.rmdir()
                if fence := getattr(state.db, "service_fence", None):
                    await fence.check()
                await state.db.execute(
                    "UPDATE knowledge_video_jobs SET status=CASE WHEN cancel_requested THEN 'CANCELLED' "
                    "ELSE 'FAILED' END,holds_memory=false,updated_at=now() WHERE id=%s",
                    (previous["id"],),
                )
            except Exception as error:
                logger.error("Media cleanup remains unconfirmed (%s)", type(error).__name__)
        await state.db.execute(
            "UPDATE knowledge_video_jobs SET status='CANCELLED' WHERE status='PENDING' AND cancel_requested"
        )
        return True


async def serve(state):
    startup = True
    while True:
        if not await recover(state, startup=startup):
            await asyncio.sleep(2)
            continue
        startup = False
        job = await claim(state.db)
        if job:
            try:
                await process(state, job)
                await state.db.execute(
                    "UPDATE knowledge_video_jobs SET status='COMPLETED',holds_memory=false,"
                    "updated_at=now() WHERE id=%s",
                    (job["id"],),
                )
            except Exception as error:
                code = str(error) if re.fullmatch(r"[A-Z_]{3,80}", str(error)) else type(error).__name__
                # A container whose stop was not confirmed stays reserved for explicit inspection.
                await state.db.execute(
                    "UPDATE knowledge_video_jobs SET status='UNKNOWN',error_code=%s,updated_at=now() WHERE id=%s",
                    (code, job["id"]),
                )
                logger.error("Media job requires review (%s)", code)
        await asyncio.sleep(2)
