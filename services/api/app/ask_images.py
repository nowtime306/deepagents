"""Validated private image attachments become real model image blocks."""

import base64
import hashlib
import io
import warnings
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from app.errors import fail

FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def validate_image(data: bytes) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=list(FORMATS)) as image:
                if (
                    max(image.size) > 8192
                    or image.width * image.height > 25000000
                    or getattr(image, "is_animated", False)
                ):
                    raise ValueError("Oversized or animated image")
                media = FORMATS[image.format]
                image.verify()
            return media
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        fail(422, "IMAGE_INVALID", "图片无效：支持静态 PNG、JPEG、WebP，边长最多 8192、像素最多 2500 万")


def image_blocks(directory, attachments: list[dict]) -> list[dict]:
    blocks = []
    for item in attachments:
        if not item.get("media_type", "").startswith("image/"):
            continue
        identifier = UUID(str(item["id"]))
        data = (directory / "attachments" / str(identifier)).read_bytes()
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["digest"]:
            fail(503, "IMAGE_CORRUPTED", "图片附件校验失败，请重新上传")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:" + item["media_type"] + ";base64," + base64.b64encode(data).decode("ascii")
                },
            }
        )
    return blocks
