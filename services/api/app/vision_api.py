"""Bounded image understanding through compatible chat-completions providers."""

import base64
import json
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import ModelConfig
from app.errors import fail
from app.security import identity

router = APIRouter(prefix="/api/v1/vision", tags=["图片理解"])
MAX_IMAGE = 5 * 1024 * 1024


def image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def understand_image(config: ModelConfig, question: str, data: bytes, media_type: str) -> str:
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    image = base64.b64encode(data).decode("ascii")
    body = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image}"}},
                ],
            }
        ],
    }
    content = bytearray()
    async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
        async with client.stream("POST", f"{config.base_url}/chat/completions", headers=headers, json=body) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 1024 * 1024:
                    raise ValueError("Response too large")
    text = json.loads(content)["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Invalid response")
    return text


@router.get("/models")
async def models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "vision" and row["protocol"] == "openai"
        ]
    }


@router.post("")
async def analyze(
    request: Request,
    model_id: Annotated[str, Form(min_length=1, max_length=120)],
    question: Annotated[str, Form(min_length=1, max_length=4000)],
    file: UploadFile,
) -> dict:
    await identity(request)
    config = await request.app.state.models.get(model_id)
    if config.kind != "vision" or config.protocol != "openai":
        fail(422, "MODEL_KIND_INVALID", "请选择支持图文输入的图片理解模型")
    data = await file.read(MAX_IMAGE + 1)
    if not data or len(data) > MAX_IMAGE:
        fail(413, "IMAGE_SIZE_INVALID", "图片不能为空且不能超过 5 MiB")
    media_type = image_type(data)
    if not media_type:
        fail(422, "IMAGE_FORMAT_INVALID", "请选择 PNG、JPEG 或 WebP 图片")
    try:
        text = await understand_image(config, question, data, media_type)
    except Exception:
        fail(502, "VISION_FAILED", "图片理解失败，请检查模型、密钥和服务地址")
    return {"text": text}


class ImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model_id: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=4000)


async def generate_image(config: ModelConfig, prompt: str) -> bytes:
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": config.model, "prompt": prompt}
    if config.provider != "siliconflow":
        body["n"] = 1
    content = bytearray()
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        async with client.stream(
            "POST", f"{config.base_url}/images/generations", headers=headers, json=body
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 16 * 1024 * 1024:
                    raise ValueError("Response too large")
        payload = json.loads(content)
        if config.provider == "siliconflow":
            url = payload["images"][0]["url"]
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "sf-maas-uat-prod.oss-cn-shanghai.aliyuncs.com"
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
            ):
                raise ValueError("Unsupported image download host")
            data_buffer = bytearray()
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    data_buffer.extend(chunk)
                    if len(data_buffer) > 10 * 1024 * 1024:
                        raise ValueError("Image too large")
            data = bytes(data_buffer)
        else:
            result = payload["data"][0].get("b64_json")
            if not isinstance(result, str):
                raise ValueError("Only base64 image responses supported")
            data = base64.b64decode(result, validate=True)
    if len(data) > 10 * 1024 * 1024 or image_type(data) != "image/png":
        raise ValueError("Invalid generated PNG")
    return data


@router.get("/generation-models")
async def generation_models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "image_generation" and row["protocol"] == "openai"
        ]
    }


@router.get("/generated")
async def generated_list(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT id,model_name,prompt,size,created_at FROM generated_images "
        "WHERE owner_id=%s ORDER BY created_at DESC LIMIT 50",
        (user["id"],),
    )
    return {"items": rows}


@router.post("/generated", status_code=201)
async def create_image(body: ImageInput, request: Request) -> dict:
    user = await identity(request)
    config = await request.app.state.models.get(body.model_id)
    if config.kind != "image_generation" or config.protocol != "openai":
        fail(422, "MODEL_KIND_INVALID", "请选择兼容的图片生成模型")
    from decimal import Decimal

    from app.extra_cost_media import begin, finish

    call = await begin(request.app.state, user["id"], config, "image", output_reserve=Decimal(1))
    try:
        data = await generate_image(config, body.prompt)
    except Exception:
        await finish(request.app.state, call, "image", success=False)
        fail(502, "IMAGE_GENERATION_FAILED", "图片生成失败；请检查模型、密钥及图片返回格式或存储地址")
    await finish(request.app.state, call, "image", Decimal(1))
    identifier = uuid4()
    directory = request.app.state.settings.data_dir / "images"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier}.png"
    path.write_bytes(data)
    try:
        await request.app.state.db.execute(
            "INSERT INTO generated_images(id,owner_id,model_name,prompt,size) VALUES (%s,%s,%s,%s,%s)",
            (identifier, user["id"], config.name, body.prompt, len(data)),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": identifier}


@router.get("/generated/{identifier}/image")
async def image_file(identifier: UUID, request: Request) -> FileResponse:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT id FROM generated_images WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "图片不存在或无权访问")
    path = request.app.state.settings.data_dir / "images" / f"{identifier}.png"
    if not path.is_file():
        fail(404, "NOT_FOUND", "图片文件不存在")
    return FileResponse(
        path, media_type="image/png", filename=f"image-{identifier}.png", content_disposition_type="inline"
    )


@router.delete("/generated/{identifier}")
async def delete_image(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM generated_images WHERE id=%s AND owner_id=%s RETURNING id", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "图片不存在或无权访问")
    (request.app.state.settings.data_dir / "images" / f"{identifier}.png").unlink(missing_ok=True)
    return {"ok": True}


@router.get("/video-models")
async def video_models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "video" and row["protocol"] == "openai" and row["provider"] == "dashscope"
        ]
    }


async def understand_video(config: ModelConfig, question: str, data: bytes) -> str:
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    encoded = base64.b64encode(data).decode("ascii")
    body = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{encoded}"}},
                    {"type": "text", "text": question},
                ],
            }
        ],
    }
    content = bytearray()
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        async with client.stream("POST", f"{config.base_url}/chat/completions", headers=headers, json=body) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 1024 * 1024:
                    raise ValueError("Response too large")
    text = json.loads(content)["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Invalid response")
    return text


@router.post("/video")
async def analyze_video(
    request: Request,
    model_id: Annotated[str, Form(min_length=1, max_length=120)],
    question: Annotated[str, Form(min_length=1, max_length=4000)],
    file: UploadFile,
) -> dict:
    user = await identity(request)
    config = await request.app.state.models.get(model_id)
    if config.kind != "video" or config.protocol != "openai" or config.provider != "dashscope":
        fail(422, "MODEL_KIND_INVALID", "请选择支持视频输入的百炼模型")
    data = await file.read(10 * 1024 * 1024 + 1)
    if not data or len(data) > 10 * 1024 * 1024:
        fail(413, "VIDEO_SIZE_INVALID", "视频不能为空且不能超过 10 MiB")
    if len(data) < 12 or data[4:8] != b"ftyp":
        fail(422, "VIDEO_FORMAT_INVALID", "请选择 MP4 视频")
    from app.extra_cost_media import begin, finish

    call = await begin(request.app.state, user["id"], config, "video_second", data=data)
    try:
        text = await understand_video(config, question, data)
    except Exception:
        await finish(request.app.state, call, "video_second", success=False)
        fail(502, "VIDEO_FAILED", "视频理解失败，请检查模型、视频、密钥和服务地址")
    await finish(request.app.state, call, "video_second")
    return {"text": text}
