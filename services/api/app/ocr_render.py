"""A bounded, credential-free subprocess renders OCR inputs; stdout contains only image data."""

import base64
import io
import json
import sys
from contextlib import closing

import pypdfium2 as pdfium
from PIL import Image

from app.ask_images import validate_image


def encoded(image, number):
    with image.convert("RGB") as rgb:
        rgb.thumbnail((1800, 1800))
        output = io.BytesIO()
        rgb.save(output, format="PNG")
    data = output.getvalue()
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Page image too large")
    return {"page": number, "data": base64.b64encode(data).decode("ascii")}


def render(data: bytes, start: int, end: int):
    if not data or len(data) > 4 * 1024 * 1024 or not 1 <= start <= end <= 100 or end - start >= 4:
        raise ValueError("Invalid bounded input")
    if not data.startswith(b"%PDF-"):
        if start != 1 or end != 1:
            raise ValueError("Images have one page")
        validate_image(data)
        with Image.open(io.BytesIO(data)) as image:
            return [encoded(image, 1)]
    pages = []
    with closing(pdfium.PdfDocument(data)) as document:
        if len(document) > 100 or end > len(document):
            raise ValueError("Page selection unavailable")
        for number in range(start, end + 1):
            with closing(document[number - 1]) as page:
                width, height = page.get_size()
                if not 1 <= width <= 14400 or not 1 <= height <= 14400:
                    raise ValueError("Invalid page dimensions")
                scale = min(2, 1800 / max(width, height))
                with closing(page.render(scale=scale, may_draw_forms=False, draw_annots=False)) as bitmap:
                    with bitmap.to_pil() as image:
                        pages.append(encoded(image, number))
    return pages


if __name__ == "__main__":
    try:
        result = render(sys.stdin.buffer.read(4 * 1024 * 1024 + 1), int(sys.argv[1]), int(sys.argv[2]))
        sys.stdout.write(json.dumps(result))
    except Exception:
        sys.stderr.write("OCR_RENDER_FAILED")
        raise SystemExit(2) from None
