"""A tiny generated local video is decoded only in the approved restricted container."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from test_api import ORIGIN
from test_api import client as client

from app.media_retrieval_probe import duration
from app.media_retrieval_video import claim, docker, process, serve
from app.media_retrieval_video import router as video_router


def test_native_bounded_video_frames_and_owned_delete(client, monkeypatch):
    if os.getenv("RUN_LOCAL_SANDBOX") != "1":
        pytest.skip("Requires the approved local media image")
    c, app = client
    app.include_router(video_router)
    owner = c.get("/api/v1/auth/me").json()["id"]
    c.portal.call(app.state.db.execute, "UPDATE users SET is_admin=true WHERE id=%s", (owner,))
    model = str(uuid4())
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES(%s,%s,'mm','http://unused','', 'multimodal_embedding','dashscope_multimodal')",
        (model, model),
    )
    kb = c.post("/api/v1/capabilities/knowledge", json={"name": "native-video-" + uuid4().hex}, headers=ORIGIN).json()[
        "id"
    ]
    path = f"/api/v1/capabilities/knowledge/{kb}"
    assert c.post(path + "/image-index", json={"model_id": model}, headers=ORIGIN).status_code == 201
    folder = app.state.settings.data_dir / "native-video-source"
    folder.mkdir(exist_ok=True)
    image = json.loads((Path(__file__).parents[4] / ".local/media-decoder-image.json").read_text())["image_id"]
    c.portal.call(
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--memory",
        "128m",
        "--cpus",
        ".25",
        "--mount",
        f"type=bind,source={folder.resolve()},target=/output",
        image,
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=16x16:r=1",
        "-t",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-y",
        "/output/input.mp4",
    )
    config = c.portal.call(app.state.models.get, model)
    measured = c.portal.call(duration, app.state, owner, config, (folder / "input.mp4").read_bytes())
    assert abs(float(measured) - 2) < 0.01

    async def embedding(config, content):
        assert "image" in content and "text" not in content
        return [1.0, 0.0]

    monkeypatch.setattr("app.media_retrieval_video.vectorize", embedding)
    result = c.post(
        path + "/videos",
        headers=ORIGIN,
        files={"file": ("tiny.mp4", (folder / "input.mp4").read_bytes())},
        data={"interval_seconds": "1", "max_frames": "2"},
    )
    assert result.status_code == 202, result.text
    job = c.portal.call(claim, app.state.db)
    assert str(job["id"]) == result.json()["id"]
    c.portal.call(process, app.state, job)
    frames = c.portal.call(app.state.db.fetch, "SELECT * FROM knowledge_video_frames WHERE job_id=%s", (job["id"],))
    assert len(frames) == 2 and sorted(float(row["sample_seconds"]) for row in frames) == [0.0, 1.0]
    assert (
        c.portal.call(app.state.db.fetch, "SELECT holds_memory FROM knowledge_video_jobs WHERE id=%s", (job["id"],))[0][
            "holds_memory"
        ]
        is False
    )
    c.portal.call(app.state.db.execute, "UPDATE knowledge_video_jobs SET status='COMPLETED' WHERE id=%s", (job["id"],))
    assert c.delete(path + f"/videos/{job['id']}", headers=ORIGIN).status_code == 200
    assert not c.portal.call(app.state.db.fetch, "SELECT * FROM knowledge_images WHERE knowledge_id=%s", (kb,))


def test_video_low_budget_waits_and_cancel_before_decode_never_sends(client, monkeypatch):
    c, app = client
    app.include_router(video_router)
    model = str(uuid4())
    c.portal.call(
        app.state.db.execute,
        "INSERT INTO model_configs(id,name,model,base_url,api_key_ciphertext,kind,protocol) "
        "VALUES(%s,%s,'mm','http://unused','', 'multimodal_embedding','dashscope_multimodal')",
        (model, model),
    )
    kb = c.post("/api/v1/capabilities/knowledge", json={"name": "cancel-video-" + uuid4().hex}, headers=ORIGIN).json()[
        "id"
    ]
    path = f"/api/v1/capabilities/knowledge/{kb}"
    assert c.post(path + "/image-index", json={"model_id": model}, headers=ORIGIN).status_code == 201
    result = c.post(path + "/videos", headers=ORIGIN, files={"file": ("tiny.mp4", b"0000ftypmp42fixture")})
    assert result.status_code == 202

    async def low():
        return 0

    monkeypatch.setattr("app.media_retrieval_video.resource_budget", low)
    assert c.portal.call(claim, app.state.db) is None

    async def enough():
        return 1024**3

    monkeypatch.setattr("app.media_retrieval_video.resource_budget", enough)
    job = c.portal.call(claim, app.state.db)
    assert job["holds_memory"]
    assert c.post(path + f"/videos/{job['id']}/cancel", headers=ORIGIN).status_code == 200

    async def forbidden(*args, **kwargs):
        raise AssertionError("Cancel must prevent decoder and model requests")

    monkeypatch.setattr("app.media_retrieval_video.docker", forbidden)
    monkeypatch.setattr("app.media_retrieval_video.vectorize", forbidden)
    with pytest.raises(RuntimeError, match="VIDEO_CANCELLED"):
        c.portal.call(process, app.state, job)
    assert not c.portal.call(
        app.state.db.fetch, "SELECT holds_memory FROM knowledge_video_jobs WHERE id=%s", (job["id"],)
    )[0]["holds_memory"]

    async def confirmed_absent(*args, **kwargs):
        assert args[:2] == ("ps", "-a")
        return ""

    c.portal.call(
        app.state.db.execute,
        "UPDATE knowledge_video_jobs SET status='UNKNOWN',holds_memory=true,create_attempted=true,"
        "image_id=%s WHERE id=%s",
        ("sha256:" + "a" * 64, job["id"]),
    )
    monkeypatch.setattr("app.media_retrieval_video.docker", confirmed_absent)

    async def recover_once():
        task = asyncio.create_task(serve(app.state))
        try:
            for _ in range(40):
                rows = await app.state.db.fetch(
                    "SELECT status,holds_memory FROM knowledge_video_jobs WHERE id=%s", (job["id"],)
                )
                if rows[0]["status"] == "CANCELLED":
                    return rows[0]
                await asyncio.sleep(0.05)
            raise AssertionError("Cancelled unknown decoder was not reconciled")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    recovered = c.portal.call(recover_once)
    assert recovered["holds_memory"] is False
