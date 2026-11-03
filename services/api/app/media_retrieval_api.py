"""Image-query retrieval against the existing private multimodal index."""

import base64
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, UploadFile

from app.embedding_compatibility import fingerprint
from app.errors import APIError, fail
from app.multimodal_api import context, vectorize
from app.permissions import require_account
from app.resource_runtime import resource_run
from app.security import identity
from app.vision_api import image_type

router = APIRouter(prefix="/api/v1/capabilities/knowledge", tags=["图片相似检索"])


@router.post("/{identifier}/image-query/search")
async def query_image(identifier: UUID, request: Request, file: UploadFile) -> dict:
    owner = (await identity(request))["id"]
    async with resource_run(request.app.state.db, owner, "resource", uuid4()):
        return await _query_image(identifier, request, file)


async def _query_image(identifier, request, file):
    user = await identity(request)
    await require_account(request.app.state.db, user["id"], "knowledge.search")
    index, config = await context(identifier, request)
    data = await file.read(5 * 1024 * 1024 + 1)
    if not data or len(data) > 5 * 1024 * 1024:
        fail(413, "IMAGE_SIZE_INVALID", "查询图片不能为空且不能超过5MiB")
    media = image_type(data)
    if not media:
        fail(422, "IMAGE_FORMAT_INVALID", "请选择PNG、JPEG或WebP图片")
    if not index["dimensions"]:
        return {"items": [], "query_kind": "image", "note": "当前图片索引为空"}
    from app.extra_cost_media import begin, finish

    call = await begin(
        request.app.state, user["id"], config, "image", output_reserve=Decimal(1), permission="knowledge.search"
    )
    try:
        vector = await vectorize(config, {"image": f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"})
    except Exception:
        await finish(request.app.state, call, "image", success=False)
        fail(502, "MULTIMODAL_SEARCH_FAILED", "图片向量请求失败，费用保留未知")
    await finish(request.app.state, call, "image", Decimal(1))
    try:
        if len(vector) != index["dimensions"]:
            fail(409, "VECTOR_DIMENSION_MISMATCH", "查询向量维度与已有图片索引不一致")
        response = await request.app.state.knowledge.vectors.query_points(
            f"kb_images_{identifier}", query=vector, limit=20
        )
    except APIError:
        raise
    except Exception:
        fail(502, "MULTIMODAL_SEARCH_FAILED", "图片相似检索失败，请检查多模态模型和索引")
    # Re-authorize after provider response: deletion or revocation cannot leak old search results.
    after, after_config = await context(identifier, request)
    if (after["model_id"], after["dimensions"], fingerprint(after_config, preprocessing="media-v1")) != (
        index["model_id"],
        index["dimensions"],
        fingerprint(config, preprocessing="media-v1"),
    ):
        fail(409, "INDEX_CHANGED", "图片检索期间模型或索引已改变，请重新检索")
    await require_account(request.app.state.db, user["id"], "knowledge.search")
    rows = await request.app.state.db.fetch(
        "SELECT i.id,i.name,i.media_type,f.sample_seconds,f.job_id AS video_job_id "
        "FROM knowledge_images i LEFT JOIN knowledge_video_frames f ON f.image_id=i.id "
        "LEFT JOIN knowledge_video_jobs j ON j.id=f.job_id "
        "WHERE i.knowledge_id=%s AND (f.job_id IS NULL OR j.status='COMPLETED')",
        (identifier,),
    )
    owned = {str(row["id"]): row for row in rows}
    return {
        "query_kind": "image",
        "items": [
            {**owned[str(point.id)], "score": point.score} for point in response.points if str(point.id) in owned
        ],
        "note": "分数表示向量相似度，不是回答置信度；查询图片不保存",
    }
