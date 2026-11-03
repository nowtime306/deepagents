"""Authenticated, bounded text-to-speech generation and owned audio files."""

import base64
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import ModelConfig
from app.errors import fail
from app.security import identity

router = APIRouter(prefix="/api/v1/speech", tags=["语音合成"])
MAX_AUDIO = 20 * 1024 * 1024
MAX_INPUT_AUDIO = 10 * 1024 * 1024
FORMATS = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


class SpeechInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=4000)
    voice: str = Field(min_length=1, max_length=200)
    billing_reserve_seconds: int = Field(default=600, ge=1, le=3600)


async def generate_audio(config: ModelConfig, text: str, voice: str) -> bytes:
    """Receive bounded MP3 data without following provider redirects."""
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    result = bytearray()
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        async with client.stream(
            "POST",
            f"{config.base_url}/audio/speech",
            headers=headers,
            json={"model": config.model, "input": text, "voice": voice, "response_format": "mp3"},
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
                raise ValueError("Unexpected audio content type")
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > MAX_AUDIO:
                    raise ValueError("Audio too large")
    if not result:
        raise ValueError("Empty audio")
    return bytes(result)


@router.get("/models")
async def models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "text_to_speech" and row["protocol"] == "openai"
        ]
    }


@router.get("/transcription-models")
async def transcription_models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "speech_to_text" and row["protocol"] == "openai"
        ]
    }


@router.get("/understanding-models")
async def understanding_models(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row["kind"] == "audio" and row["protocol"] == "openai"
        ]
    }


async def understand_audio(config: ModelConfig, data: bytes, audio_format: str, question: str) -> str:
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {
        "model": config.model,
        "modalities": ["text"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": base64.b64encode(data).decode("ascii"), "format": audio_format},
                    },
                ],
            }
        ],
    }
    result = bytearray()
    async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
        async with client.stream("POST", f"{config.base_url}/chat/completions", headers=headers, json=body) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > 1024 * 1024:
                    raise ValueError("Response too large")
    text = json.loads(result)["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Invalid response")
    return text


@router.post("/understanding")
async def understand(
    request: Request,
    model_id: Annotated[str, Form(min_length=1, max_length=120)],
    question: Annotated[str, Form(min_length=1, max_length=4000)],
    file: UploadFile,
) -> dict:
    user = await identity(request)
    config = await request.app.state.models.get(model_id)
    if config.kind != "audio" or config.protocol != "openai":
        fail(422, "MODEL_KIND_INVALID", "请选择支持音频输入的音频理解模型")
    data = await file.read(MAX_INPUT_AUDIO + 1)
    if not data or len(data) > MAX_INPUT_AUDIO:
        fail(413, "AUDIO_SIZE_INVALID", "音频不能为空且不能超过 10 MiB")
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        audio_format = "wav"
    elif data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        audio_format = "mp3"
    else:
        fail(422, "AUDIO_FORMAT_INVALID", "请选择 WAV 或 MP3 音频")
    from app.extra_cost_media import begin, finish

    call = await begin(request.app.state, user["id"], config, "audio_second", data=data)
    try:
        text = await understand_audio(config, data, audio_format, question)
    except Exception:
        await finish(request.app.state, call, "audio_second", success=False)
        fail(502, "AUDIO_UNDERSTANDING_FAILED", "音频理解失败，请检查模型、音频、密钥和服务地址")
    await finish(request.app.state, call, "audio_second")
    return {"text": text}


async def transcribe_audio(config: ModelConfig, data: bytes, suffix: str) -> str:
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    result = bytearray()
    async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
        async with client.stream(
            "POST",
            f"{config.base_url}/audio/transcriptions",
            headers=headers,
            data={"model": config.model},
            files={"file": (f"audio{suffix}", data, FORMATS[suffix])},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > 1024 * 1024:
                    raise ValueError("Transcription too large")
    from app.extra_cost_media import ReportedText, billed_duration

    payload = json.loads(result)
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("Invalid transcription")
    return ReportedText(text, billed_duration(payload))


@router.post("/transcriptions")
async def transcribe(
    request: Request, model_id: Annotated[str, Form(min_length=1, max_length=120)], file: UploadFile
) -> dict:
    user = await identity(request)
    config = await request.app.state.models.get(model_id)
    if config.kind != "speech_to_text" or config.protocol != "openai":
        fail(422, "MODEL_KIND_INVALID", "请选择支持 /audio/transcriptions 的语音识别模型")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in FORMATS:
        fail(422, "AUDIO_FORMAT_INVALID", "请选择 MP3、WAV、M4A、MP4、WEBM、OGG 或 FLAC 音频")
    data = await file.read(MAX_INPUT_AUDIO + 1)
    if not data or len(data) > MAX_INPUT_AUDIO:
        fail(413, "AUDIO_SIZE_INVALID", "音频不能为空且不能超过 10 MiB")
    from app.extra_cost_media import begin, finish

    call = await begin(request.app.state, user["id"], config, "audio_second", data=data)
    try:
        text = await transcribe_audio(config, data, suffix)
    except Exception:
        await finish(request.app.state, call, "audio_second", success=False)
        fail(502, "TRANSCRIPTION_FAILED", "语音识别失败，请检查模型、音频格式、密钥和服务地址")
    await finish(request.app.state, call, "audio_second", getattr(text, "billed_seconds", None))
    return {"text": text}


@router.get("")
async def listing(request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT id,model_name,voice,input,size,created_at FROM speech_outputs "
        "WHERE owner_id=%s ORDER BY created_at DESC LIMIT 50",
        (user["id"],),
    )
    return {"items": rows}


@router.post("", status_code=201)
async def create(body: SpeechInput, request: Request) -> dict:
    user = await identity(request)
    config = await request.app.state.models.get(body.model_id)
    if config.kind != "text_to_speech" or config.protocol != "openai":
        fail(422, "MODEL_KIND_INVALID", "请选择支持 /audio/speech 的语音合成模型")
    from decimal import Decimal

    from app.extra_cost_media import begin, finish

    call = await begin(
        request.app.state, user["id"], config, "audio_second", output_reserve=Decimal(body.billing_reserve_seconds)
    )
    try:
        data = await generate_audio(config, body.text, body.voice)
    except Exception:
        await finish(request.app.state, call, "audio_second", success=False)
        fail(502, "SPEECH_FAILED", "语音生成失败，请检查模型、音色、密钥和服务地址")
    await finish(request.app.state, call, "audio_second")
    identifier = uuid4()
    directory = request.app.state.settings.data_dir / "speech"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{identifier}.mp3"
    path.write_bytes(data)
    try:
        await request.app.state.db.execute(
            "INSERT INTO speech_outputs(id,owner_id,model_name,voice,input,size) VALUES (%s,%s,%s,%s,%s,%s)",
            (identifier, user["id"], config.name, body.voice, body.text, len(data)),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": identifier}


async def owned(identifier: UUID, request: Request) -> None:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "SELECT id FROM speech_outputs WHERE id=%s AND owner_id=%s", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "音频不存在或无权访问")


@router.get("/{identifier}/audio")
async def audio(identifier: UUID, request: Request) -> FileResponse:
    await owned(identifier, request)
    path = request.app.state.settings.data_dir / "speech" / f"{identifier}.mp3"
    if not path.is_file():
        fail(404, "NOT_FOUND", "音频文件不存在")
    return FileResponse(
        path, media_type="audio/mpeg", filename=f"speech-{identifier}.mp3", content_disposition_type="inline"
    )


@router.delete("/{identifier}")
async def delete(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    rows = await request.app.state.db.fetch(
        "DELETE FROM speech_outputs WHERE id=%s AND owner_id=%s RETURNING id", (identifier, user["id"])
    )
    if not rows:
        fail(404, "NOT_FOUND", "音频不存在或无权访问")
    (request.app.state.settings.data_dir / "speech" / f"{identifier}.mp3").unlink(missing_ok=True)
    return {"ok": True}
