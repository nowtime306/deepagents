"""Duration estimation inside the same approved decoder and memory reservation domain."""

import json
import re
from decimal import Decimal
from uuid import uuid4

from app.errors import fail
from app.host_resources import resource_budget
from app.maintenance import slot
from app.media_retrieval_video import MEMORY, ROOT, directory, docker, private_path, stop


async def duration(state, owner, config, data):
    if fence := getattr(state.db, "service_fence", None):
        await fence.check()
    key = uuid4()
    registered = json.loads((ROOT / ".local/media-decoder-image.json").read_text())
    image = registered["image_id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        fail(503, "MEDIA_DECODER_UNAVAILABLE", "媒体解码镜像尚未批准")
    folder = directory(state.settings, key)
    folder.mkdir(parents=True)
    source = private_path(state.settings, "knowledge-videos", str(key), "source")
    source.write_bytes(data)
    job = {"id": key, "image_id": image}
    admitted = False
    attempted = False
    confirmed = False
    try:
        budget = await resource_budget()
        async with state.db.pool.connection() as c:
            if not await slot(c):
                fail(503, "PLATFORM_MAINTENANCE", "平台正在维护，未启动媒体检测")
            if fence := getattr(state.db, "service_fence", None):
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
                fail(429, "MEDIA_CAPACITY_WAIT", "媒体时长检测需要512MiB执行预算，请稍后重试")
            await c.execute(
                "INSERT INTO knowledge_video_jobs(id,job_kind,owner_id,model_id,model_revision,name,size,"
                "interval_seconds,max_frames,status,holds_memory,image_id) "
                "VALUES(%s,'probe',%s,%s,%s,'duration probe',%s,1,1,'RUNNING',true,%s)",
                (key, owner, config.id, config.revision, len(data), image),
            )
            admitted = True
        name = "owner-agent-media-" + str(key)
        await state.db.execute("UPDATE knowledge_video_jobs SET create_attempted=true WHERE id=%s", (key,))
        attempted = True
        cid = await docker(
            "create",
            "--pull=never",
            "--name",
            name,
            "--label",
            "owner_agent_media_job=" + str(key),
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
            image,
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            "/input/source",
        )
        await state.db.execute("UPDATE knowledge_video_jobs SET container_id=%s WHERE id=%s", (cid, key))
        result = await docker("start", "-a", name, deadline=45)
        value = Decimal(str(json.loads(result)["format"]["duration"]))
        if not value.is_finite() or not 0 < value <= 86400:
            raise ValueError("invalid duration")
        return value
    finally:
        if admitted:
            try:
                if attempted:
                    await stop(job)
                confirmed = True
                await state.db.execute("DELETE FROM knowledge_video_jobs WHERE id=%s", (key,))
            except Exception:
                await state.db.execute(
                    "UPDATE knowledge_video_jobs SET status='UNKNOWN',error_code='PROBE_CLEANUP_UNKNOWN' WHERE id=%s",
                    (key,),
                )
        if not admitted or confirmed:
            private_path(state.settings, "knowledge-videos", str(key), "source").unlink(missing_ok=True)
            folder.rmdir()
