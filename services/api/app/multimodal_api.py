"""Private image indexing and text-to-image retrieval with DashScope embeddings."""

import base64
import json
import math
from pathlib import PurePath
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from qdrant_client import models

from app.config import ModelConfig
from app.errors import fail
from app.security import identity
from app.vision_api import image_type

router = APIRouter(prefix="/api/v1/capabilities", tags=["多模态知识库"])


class IndexInput(BaseModel):
    model_id: str = Field(min_length=1, max_length=120)


class ImageSearch(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


def compatible(config: ModelConfig) -> bool:
    return config.kind == "multimodal_embedding" and config.protocol == "dashscope_multimodal"


async def vectorize(config: ModelConfig, content: dict) -> list[float]:
    key = config.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    result = bytearray()
    async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
        async with client.stream(
            "POST",
            f"{config.base_url}/services/embeddings/multimodal-embedding/multimodal-embedding",
            headers=headers,
            json={"model": config.model, "input": {"contents": [content]}},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > 2 * 1024 * 1024:
                    raise ValueError("Response too large")
    values = json.loads(result)["output"]["embeddings"][0]["embedding"]
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 8192
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
    ):
        raise ValueError("Invalid vector")
    return values


@router.get("/multimodal-models")
async def models_list(request: Request) -> dict:
    await identity(request)
    rows = await request.app.state.models.list(enabled_only=True)
    return {
        "items": [
            {"id": r["id"], "name": r["name"]}
            for r in rows
            if r["kind"] == "multimodal_embedding" and r["protocol"] == "dashscope_multimodal"
        ]
    }


async def context(identifier: UUID, request: Request) -> tuple[dict, ModelConfig]:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT * FROM knowledge_image_indexes WHERE knowledge_id=%s", (identifier,)
    )
    if not rows:
        fail(422, "INDEX_NOT_CONFIGURED", "请先为知识库绑定多模态向量模型")
    config = await request.app.state.models.get(rows[0]["model_id"])
    if not compatible(config) or config.model != rows[0]["model_name"] or config.base_url != rows[0]["base_url"]:
        fail(409, "INDEX_MODEL_CHANGED", "模型配置已改变，不能混用旧向量；请新建知识库重建索引")
    return rows[0], config


@router.get("/knowledge/{identifier}/image-index")
async def image_index(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT model_id,model_name,dimensions FROM knowledge_image_indexes WHERE knowledge_id=%s", (identifier,)
    )
    return {"index": rows[0] if rows else None}


@router.post("/knowledge/{identifier}/image-index", status_code=201)
async def create_index(identifier: UUID, body: IndexInput, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    config = await request.app.state.models.get(body.model_id)
    if not compatible(config):
        fail(422, "MODEL_KIND_INVALID", "请选择百炼原生多模态向量模型")
    rows = await request.app.state.db.fetch(
        "INSERT INTO knowledge_image_indexes(knowledge_id,model_id,model_name,base_url) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING knowledge_id",
        (identifier, config.id, config.model, config.base_url),
    )
    if not rows:
        fail(409, "INDEX_EXISTS", "知识库已绑定多模态模型，请新建知识库更换模型")
    return {"ok": True}


@router.get("/knowledge/{identifier}/images")
async def list_images(identifier: UUID, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT id,name,media_type,size,created_at FROM knowledge_images "
        "WHERE knowledge_id=%s ORDER BY created_at DESC",
        (identifier,),
    )
    return {"items": rows}


@router.post("/knowledge/{identifier}/images", status_code=201)
async def upload_image(identifier: UUID, request: Request, file: UploadFile) -> dict:
    _, config = await context(identifier, request)
    data = await file.read(5 * 1024 * 1024 + 1)
    if not data or len(data) > 5 * 1024 * 1024:
        fail(413, "IMAGE_SIZE_INVALID", "图片不能为空且不能超过 5 MiB")
    media = image_type(data)
    if not media:
        fail(422, "IMAGE_FORMAT_INVALID", "请选择 PNG、JPEG 或 WebP 图片")
    try:
        vector = await vectorize(config, {"image": f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"})
    except Exception:
        fail(502, "MULTIMODAL_INDEX_FAILED", "图片向量化失败，请检查模型和服务配置")
    image_id = uuid4()
    name = PurePath((file.filename or "图片").replace("\\", "/")).name[:200]
    path = request.app.state.settings.data_dir / "knowledge-images" / str(image_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    vectors = request.app.state.knowledge.vectors
    collection = f"kb_images_{identifier}"
    async with request.app.state.db.pool.connection() as connection:
        parent = await connection.execute("SELECT id FROM knowledge_bases WHERE id=%s FOR UPDATE", (identifier,))
        if not await parent.fetchone():
            fail(404, "NOT_FOUND", "知识库不存在或无权访问")
        cursor = await connection.execute(
            "SELECT * FROM knowledge_image_indexes WHERE knowledge_id=%s FOR UPDATE", (identifier,)
        )
        index = await cursor.fetchone()
        if (
            not index
            or index["model_id"] != config.id
            or index["model_name"] != config.model
            or index["base_url"] != config.base_url
        ):
            fail(409, "INDEX_CHANGED", "索引配置已改变，请重新操作")
        if index["dimensions"] and index["dimensions"] != len(vector):
            fail(409, "VECTOR_DIMENSION_MISMATCH", "向量维度与索引不一致，未保存图片")
        if not await vectors.collection_exists(collection):
            await vectors.create_collection(
                collection, vectors_config=models.VectorParams(size=len(vector), distance=models.Distance.COSINE)
            )
        try:
            path.write_bytes(data)
            await vectors.upsert(collection, points=[models.PointStruct(id=str(image_id), vector=vector)])
            await connection.execute(
                "UPDATE knowledge_image_indexes SET dimensions=%s WHERE knowledge_id=%s", (len(vector), identifier)
            )
            await connection.execute(
                "INSERT INTO knowledge_images(id,knowledge_id,name,media_type,size) VALUES (%s,%s,%s,%s,%s)",
                (image_id, identifier, name, media, len(data)),
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return {"id": image_id}


@router.post("/knowledge/{identifier}/image-search")
async def search_images(identifier: UUID, body: ImageSearch, request: Request) -> dict:
    index, config = await context(identifier, request)
    if not index["dimensions"]:
        return {"items": []}
    try:
        vector = await vectorize(config, {"text": body.query})
        if len(vector) != index["dimensions"]:
            fail(409, "VECTOR_DIMENSION_MISMATCH", "查询向量维度与图片索引不一致")
        response = await request.app.state.knowledge.vectors.query_points(
            f"kb_images_{identifier}", query=vector, limit=20
        )
    except Exception as error:
        from app.errors import APIError

        if isinstance(error, APIError):
            raise
        fail(502, "MULTIMODAL_SEARCH_FAILED", "图片检索失败，请检查模型和索引")
    rows = await request.app.state.db.fetch(
        "SELECT i.id,i.name,i.media_type,f.sample_seconds,f.job_id AS video_job_id "
        "FROM knowledge_images i LEFT JOIN knowledge_video_frames f ON f.image_id=i.id "
        "LEFT JOIN knowledge_video_jobs j ON j.id=f.job_id "
        "WHERE i.knowledge_id=%s AND (f.job_id IS NULL OR j.status='COMPLETED')",
        (identifier,),
    )
    owned = {str(row["id"]): row for row in rows}
    return {
        "items": [{**owned[str(point.id)], "score": point.score} for point in response.points if str(point.id) in owned]
    }


@router.get("/knowledge/{identifier}/images/{image_id}")
async def get_image(identifier: UUID, image_id: UUID, request: Request) -> FileResponse:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "SELECT media_type FROM knowledge_images WHERE id=%s AND knowledge_id=%s", (image_id, identifier)
    )
    if not rows:
        fail(404, "NOT_FOUND", "图片不存在或无权访问")
    path = request.app.state.settings.data_dir / "knowledge-images" / str(image_id)
    if not path.is_file():
        fail(404, "NOT_FOUND", "图片文件不存在")
    return FileResponse(path, media_type=rows[0]["media_type"])


@router.delete("/knowledge/{identifier}/images/{image_id}")
async def delete_image(identifier: UUID, image_id: UUID, request: Request) -> dict:
    user = await identity(request)
    await request.app.state.knowledge.owned(identifier, user["id"])
    rows = await request.app.state.db.fetch(
        "DELETE FROM knowledge_images WHERE id=%s AND knowledge_id=%s RETURNING id", (image_id, identifier)
    )
    if not rows:
        fail(404, "NOT_FOUND", "图片不存在或无权访问")
    (request.app.state.settings.data_dir / "knowledge-images" / str(image_id)).unlink(missing_ok=True)
    vectors = request.app.state.knowledge.vectors
    collection = f"kb_images_{identifier}"
    if await vectors.collection_exists(collection):
        await vectors.delete(collection, points_selector=models.PointIdsList(points=[str(image_id)]))
    return {"ok": True}
