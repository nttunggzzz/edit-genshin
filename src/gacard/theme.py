"""Màu khung (mặc định cyan, tuỳ chọn đoán theo nền) và font chữ (đủ dấu tiếng Việt)."""

from __future__ import annotations

import colorsys
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFont

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "acc-card" / "fonts"
_FONT_FILES = {
    "extrabold": ["BeVietnamPro-ExtraBold.ttf", "BeVietnamPro-Bold.ttf"],
    "bold": ["BeVietnamPro-Bold.ttf", "BeVietnamPro-ExtraBold.ttf"],
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISPLAY_FONT_DEFAULT = Path("data/acc-card/fonts/HYWenHei-85W.ttf")  # font game, git-ignored, KHÔNG commit
_SYSTEM_FALLBACKS = [Path(r"C:\Windows\Fonts\segoeuib.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf"),
                     Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]

RGB = tuple[int, int, int]

# Màu nhấn quen dùng trong ảnh mẫu: (tên, màu, hue 0-360)
PALETTE: dict[str, RGB] = {
    "cyan": (0x46, 0xC3, 0xE1),
    "red": (0xE8, 0x3A, 0x4E),
    "pink": (0xF0, 0x6E, 0xB9),
    "purple": (0x9D, 0x6B, 0xF2),
    "green": (0x4C, 0xCB, 0x7C),
    "blue": (0x4A, 0x8E, 0xF0),
}
DEFAULT_FRAME = PALETTE["cyan"]

# Gradient khung (đỉnh, đáy). cyan = «Blue_16» (tam.psd), pink = «Red_05» (nho.psd); còn lại tự suy.
GRADIENTS: dict[str, tuple[RGB, RGB]] = {
    "cyan": ((0x68, 0xE0, 0xCF), (0x20, 0x9C, 0xFF)),
    "pink": ((0xEF, 0x0A, 0x6A), (0xB6, 0x35, 0x9C)),
    "red": ((0xFF, 0x6B, 0x5A), (0xC2, 0x1E, 0x45)),
    "purple": ((0xC5, 0x9B, 0xFF), (0x70, 0x42, 0xE0)),
    "green": ((0xA6, 0xE8, 0x6A), (0x22, 0xA8, 0x6A)),
    "blue": ((0x6E, 0xC1, 0xFF), (0x3D, 0x5B, 0xF0)),
}
COOL_HUE = 260     # hướng lệch hue của màu đáy gradient tự suy
FROST_ALPHA = 235   # độ phủ lớp nền «frosted» sau lưới thẻ


def _display_font_path() -> Path | None:
    """GACARD_DISPLAY_FONT > data/acc-card/fonts/HYWenHei-85W.ttf; None nếu không có file."""
    env = os.environ.get("GACARD_DISPLAY_FONT", "").strip()
    for raw in ([env] if env else []) + [str(DISPLAY_FONT_DEFAULT)]:
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.is_file():
            return path
        if raw == env:
            log.warning("GACARD_DISPLAY_FONT không tồn tại: %s", path)
    return None


@lru_cache(maxsize=1)
def _display_font_resolved() -> Path | None:
    path = _display_font_path()
    if path:
        log.info("Font hiển thị (mã/UID/số đè): %s", path)
    else:
        log.info("Font hiển thị: không có font game, dùng Be Vietnam Pro ExtraBold")
    return path


@lru_cache(maxsize=64)
def font(size: int, weight: str = "extrabold") -> ImageFont.FreeTypeFont:
    """Font OFL trong assets; thiếu thì dùng font hệ thống (Segoe UI Bold / Arial Bold).
    weight="display": font game nếu có (xem _display_font_path), không thì như extrabold."""
    if weight == "display":
        custom = _display_font_resolved()
        if custom:
            try:
                return ImageFont.truetype(str(custom), size)
            except OSError:
                log.warning("Không mở được font hiển thị %s, dùng font mặc định", custom)
        weight = "extrabold"
    candidates = [FONT_DIR / name for name in _FONT_FILES.get(weight, _FONT_FILES["bold"])]
    for path in [*candidates, *_SYSTEM_FALLBACKS]:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    log.warning("Không tìm thấy font có dấu tiếng Việt, dùng font mặc định của Pillow")
    return ImageFont.load_default(size)


def reset_font_cache() -> None:
    """Xoá cache font (dùng khi đổi GACARD_DISPLAY_FONT lúc chạy / trong test)."""
    _display_font_resolved.cache_clear()
    font.cache_clear()
    text_size.cache_clear()


def text_weight(kind: str) -> str:
    """Watermark giữ Be Vietnam Pro; mã, UID, dòng thêm... dùng font hiển thị."""
    return "extrabold" if kind == "watermark" else "display"


@lru_cache(maxsize=1024)
def text_size(text: str, size: int, stroke: int = 0, weight: str = "extrabold") -> tuple[int, int]:
    """Kích thước (rộng, cao) của dòng chữ, tính cả viền."""
    left, top, right, bottom = font(size, weight).getbbox(text, stroke_width=stroke)
    return right - left, bottom - top


@dataclass(frozen=True)
class Theme:
    frame: RGB                 # màu nhấn (giữa gradient khung)
    outline: RGB               # viền chữ khi không tô gradient được: màu giữa đỉnh/đáy, không làm tối
    fill: tuple[int, int, int, int]   # màu pha vào nền «frosted» sau lưới thẻ (RGB) + alpha của lớp đó
    badge: RGB                 # như outline, cho số đè
    text: RGB = (255, 255, 255)
    frame_top: RGB | None = None      # gradient dọc của viền khung/chữ: màu ở đỉnh khối
    frame_bottom: RGB | None = None   # ... và ở đáy khối (PSD: màu đầu của gradient nằm ở đáy)

    def __post_init__(self) -> None:
        if self.frame_top is None or self.frame_bottom is None:
            top, bottom = gradient_pair(self.frame)
            object.__setattr__(self, "frame_top", self.frame_top or top)
            object.__setattr__(self, "frame_bottom", self.frame_bottom or bottom)


def parse_hex(color: str) -> RGB:
    """'#RRGGBB' → (r, g, b); lỗi thì ValueError."""
    value = color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Màu không hợp lệ: {color!r} (cần dạng #RRGGBB)")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a: RGB, b: RGB, t: float = 0.5) -> RGB:
    """Trộn tuyến tính a → b (t = 0 → a, t = 1 → b)."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def _shift(color: RGB, dh: float, sat: float, val: float) -> RGB:
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in color))
    r, g, b = colorsys.hsv_to_rgb((h + dh / 360) % 1.0, min(1.0, s * sat), min(1.0, v * val))
    return round(r * 255), round(g * 255), round(b * 255)


def gradient_pair(frame: RGB) -> tuple[RGB, RGB]:
    """(đỉnh, đáy) của gradient khung. Màu trong PALETTE dùng cặp cố định (cyan, pink đo từ PSD
    của chủ dự án; các màu khác suy theo cùng kiểu). Màu tự chọn: như hai cặp PSD, đáy lệch hue 15°
    về phía lạnh (≈ 260°, xanh tím) và đậm hơn, đỉnh lệch 15° ngược lại và sáng hơn."""
    for name, accent in PALETTE.items():
        if accent == tuple(frame):
            return GRADIENTS[name]
    hue = colorsys.rgb_to_hsv(*(c / 255 for c in frame))[0] * 360
    toward_cool = 1 if (COOL_HUE - hue) % 360 < 180 else -1
    return (_shift(frame, -15 * toward_cool, 0.85, 1.12), _shift(frame, 15 * toward_cool, 1.1, 0.85))


def theme_from_color(frame: RGB, pair: tuple[RGB, RGB] | None = None) -> Theme:
    top, bottom = pair or gradient_pair(frame)
    mid = mix(top, bottom)
    return Theme(frame=frame, outline=mid, fill=(*bottom, FROST_ALPHA), badge=mid,
                 frame_top=top, frame_bottom=bottom)


def dominant_accent(bg: Image.Image) -> RGB:
    """Chọn màu nhấn trong PALETTE gần với tông màu bão hoà nổi bật nhất của nền."""
    small = np.asarray(bg.convert("RGB").resize((96, 54), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    mx, mn = small.max(axis=2), small.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    mask = (sat > 0.35) & (mx > 0.35)
    if mask.mean() < 0.04:
        return DEFAULT_FRAME
    hsv = np.asarray(Image.fromarray((small * 255).astype(np.uint8)).convert("HSV"), dtype=np.float32)
    hue = hsv[..., 0][mask] * 360 / 255
    weights = sat[mask]
    hist, _ = np.histogram(hue, bins=24, range=(0, 360), weights=weights)
    peak = (int(np.argmax(hist)) + 0.5) * 15
    return _accent_for_hue(peak)


def _accent_for_hue(hue: float) -> RGB:
    # vàng/cam/nâu không có trong bảng → cyan (tương phản tốt, giống ảnh mẫu)
    if hue < 15 or hue >= 340:
        return PALETTE["red"]
    if hue < 75:
        return PALETTE["cyan"]
    if hue < 165:
        return PALETTE["green"]
    if hue < 205:
        return PALETTE["cyan"]
    if hue < 250:
        return PALETTE["blue"]
    if hue < 290:
        return PALETTE["purple"]
    return PALETTE["pink"]


FRAME_COLOR_ENV = "GACARD_FRAME_COLOR"
AUTO = "auto"


def pick_theme(bg: Image.Image | None, frame_color: str | None = None) -> Theme:
    """Màu khung: "#RRGGBB" → màu đó; "auto" → đoán từ nền (cyan nếu không có nền / nền nhạt màu);
    None → GACARD_FRAME_COLOR nếu đặt (hex hoặc "auto"), không thì cyan — màu chuẩn của chủ dự án
    (gradient «Blue_16»: đáy #209CFF → đỉnh #68E0CF)."""
    choice = (frame_color or os.environ.get(FRAME_COLOR_ENV, "")).strip()
    if not choice:
        return theme_from_color(DEFAULT_FRAME)
    if choice.lower() != AUTO:
        return theme_from_color(parse_hex(choice))
    if bg is None:
        return theme_from_color(DEFAULT_FRAME)
    accent = dominant_accent(bg)
    frame = _readable(accent, bg)
    if frame == accent:
        return theme_from_color(accent)
    top, bottom = gradient_pair(accent)
    return theme_from_color(frame, (_lighten(top), _lighten(bottom)))


def _luminance(color) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(c)) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _readable(frame: RGB, bg: Image.Image) -> RGB:
    """Khung quá giống độ sáng nền (tương phản < 1.8) → pha trắng cho nổi."""
    mean = np.asarray(bg.convert("RGB").resize((32, 18)), dtype=np.float32).reshape(-1, 3).mean(axis=0)
    lf, lb = _luminance(frame), _luminance(mean)
    if (max(lf, lb) + 0.05) / (min(lf, lb) + 0.05) >= 1.8:
        return frame
    return _lighten(frame)


def _lighten(color: RGB, t: float = 0.4) -> RGB:
    return mix(color, (255, 255, 255), t)
