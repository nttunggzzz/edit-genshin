"""Xuất JPG: RGB, 4:2:0, optimize, không EXIF, ≤ 2 MB."""

from __future__ import annotations

import io

from PIL import Image

MAX_BYTES = 2_000_000
QUALITY_START, QUALITY_MIN, QUALITY_STEP = 90, 80, 5


def resize_to_height(img: Image.Image, height: int) -> Image.Image:
    """Thu về chiều cao `height`, rộng 16:9 (LANCZOS). 1080 → giữ nguyên."""
    width = round(height * 16 / 9)
    if img.size == (width, height):
        return img
    return img.resize((width, height), Image.Resampling.LANCZOS)


def to_jpeg(img: Image.Image, height: int = 1080, max_bytes: int = MAX_BYTES) -> bytes:
    """Mã hoá JPEG, giảm quality từng bước 5 (90 → 80) đến khi ≤ max_bytes."""
    if height not in (1080, 720):
        raise ValueError("Chiều cao ảnh chỉ nhận 1080 hoặc 720")
    rgb = resize_to_height(img.convert("RGB"), height)
    data = b""
    for quality in range(QUALITY_START, QUALITY_MIN - 1, -QUALITY_STEP):
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=quality, subsampling="4:2:0", optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            break
    return data
