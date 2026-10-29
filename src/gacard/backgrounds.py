"""Kho ảnh nền theo nhân vật (hợp đồng mục 4d).

Cấu trúc: <root>/<slug>/<id>.jpg + <root>/index.json (UTF-8, ghi qua file tạm + os.replace).
"""

from __future__ import annotations

import io
import json
import os
import random
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

MIN_W, MIN_H = 1920, 1080
Side = Literal["left", "center", "right"]
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class BackgroundError(Exception):
    """Lỗi kho nền; `message_vi` là câu báo cho người dùng."""

    def __init__(self, message_vi: str):
        super().__init__(message_vi)
        self.message_vi = message_vi


class Background(BaseModel):
    id: str
    character: str
    file: Path                       # tương đối với root
    width: int
    height: int
    subject_side: Side
    added_at: datetime
    used_count: int = 0
    last_used_at: datetime | None = None
    source: str | None = None        # ghi chú nguồn ảnh (tuỳ chọn)


class _Index(BaseModel):
    version: int = 1
    backgrounds: list[Background] = []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def estimate_subject_side(img: Image.Image) -> Side:
    """Đoán nhân vật đứng bên nào: cột ảnh nào nhiều chi tiết + màu nhất (đơn giản, có thể sai)."""
    small = img.convert("RGB").resize((192, 108), Image.Resampling.BILINEAR)
    rgb = np.asarray(small, dtype=np.float32) / 255
    gray = rgb.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1))[:-1, :]
    gy = np.abs(np.diff(gray, axis=0))[:, :-1]
    detail = gx + gy
    sat = (rgb.max(axis=2) - rgb.min(axis=2))[:-1, :-1]
    energy = (detail * (0.5 + sat)).sum(axis=0)
    thirds = [float(part.sum()) for part in np.array_split(energy, 3)]
    best = int(np.argmax(thirds))
    others = [v for i, v in enumerate(thirds) if i != best]
    if best == 1 or thirds[best] < 1.15 * max(others):
        return "center"
    return "left" if best == 0 else "right"


class BackgroundLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.index_path = self.root / "index.json"

    # ------------------------------------------------------------ chỉ mục
    def _load(self) -> list[Background]:
        if not self.index_path.is_file():
            return []
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return _Index.model_validate(data).backgrounds

    def _save(self, items: list[Background]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = _Index(backgrounds=items).model_dump_json(indent=2)
        fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.index_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------ thao tác
    def add(self, image_bytes: bytes, character: str, source: str | None = None) -> Background:
        """Lưu nền mới (JPEG q95) cho nhân vật `character` (slug). Nhỏ hơn 1920×1080 → BackgroundError."""
        slug = (character or "").strip().lower()
        if not _SLUG.match(slug):
            raise BackgroundError(f"Tên nhân vật không hợp lệ: {character!r} (dùng chữ thường không dấu, vd columbina)")
        try:
            img = Image.open(io.BytesIO(image_bytes))
            img.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise BackgroundError("File gửi lên không phải ảnh hoặc ảnh bị hỏng.") from exc
        if img.width < MIN_W or img.height < MIN_H:
            raise BackgroundError(
                f"Ảnh nền quá nhỏ ({img.width}×{img.height}). Cần tối thiểu {MIN_W}×{MIN_H}; "
                "hãy gửi dạng File để Telegram không nén ảnh.")
        rgb = img.convert("RGB")
        bg_id = f"{slug}-{uuid.uuid4().hex[:8]}"
        rel = Path(slug) / f"{bg_id}.jpg"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(dest, "JPEG", quality=95, subsampling="4:2:0", optimize=True)
        bg = Background(id=bg_id, character=slug, file=rel, width=rgb.width, height=rgb.height,
                        subject_side=estimate_subject_side(rgb), added_at=_now(), source=source)
        items = self._load()
        items.append(bg)
        self._save(items)
        return bg

    def list(self, character: str | None = None) -> list[Background]:
        items = self._load()
        return [b for b in items if character is None or b.character == character]

    def get(self, bg_id: str) -> Background:
        for b in self._load():
            if b.id == bg_id:
                return b
        raise BackgroundError(f"Không có nền mã {bg_id!r} trong kho.")

    def remove(self, bg_id: str) -> None:
        items = self._load()
        keep = [b for b in items if b.id != bg_id]
        if len(keep) == len(items):
            raise BackgroundError(f"Không có nền mã {bg_id!r} trong kho.")
        gone = next(b for b in items if b.id == bg_id)
        self._save(keep)
        (self.root / gone.file).unlink(missing_ok=True)

    def _update(self, bg_id: str, **changes) -> Background:
        items = self._load()
        for i, b in enumerate(items):
            if b.id == bg_id:
                items[i] = b.model_copy(update=changes)
                self._save(items)
                return items[i]
        raise BackgroundError(f"Không có nền mã {bg_id!r} trong kho.")

    def set_subject_side(self, bg_id: str, side: Side) -> Background:
        if side not in ("left", "center", "right"):
            raise BackgroundError("Vị trí nhân vật chỉ nhận: left, center, right.")
        return self._update(bg_id, subject_side=side)

    def mark_used(self, bg_id: str) -> Background:
        b = self.get(bg_id)
        return self._update(bg_id, used_count=b.used_count + 1, last_used_at=_now())

    def matching(self, characters_in_account: Iterable[str]) -> list[Background]:
        """Mọi nền trong kho có nhân vật nằm trong acc (thứ tự như chỉ mục)."""
        wanted = set(characters_in_account)
        return [b for b in self._load() if b.character in wanted]

    def pick(self, characters_in_account: Iterable[str], rng: random.Random | None = None,
             exclude: Iterable[str] = ()) -> Background | None:
        """Luật chủ dự án: chọn NGẪU NHIÊN (random.choice, `rng` để test cố định) trong các nền có nhân vật
        nằm trong acc, bỏ các id trong `exclude` (nút "Đổi nền" tránh lặp nền đang dùng). Không còn nền nào
        khớp → None (nền mặc định). Không dùng điểm hype."""
        skip = set(exclude)
        candidates = [b for b in self.matching(characters_in_account) if b.id not in skip]
        if not candidates:
            return None
        return (rng or random).choice(candidates)

    def open_image(self, bg: Background) -> Image.Image:
        path = self.root / bg.file
        if not path.is_file():
            raise BackgroundError(f"Mất file nền {bg.file} (mã {bg.id}).")
        with Image.open(path) as img:
            return img.convert("RGB")
