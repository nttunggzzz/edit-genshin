"""Vẽ bố cục đã tính lên canvas 1920×1080: nền, nền frosted sau lưới, mảnh, khung, chữ, số đè.

Theo style PSD của chủ dự án (docs/acc-card/03-phan-tich-psd.md): không bóng đổ, ảnh mảnh giữ góc
vuông, mọi khối và mọi chữ cùng một viền 4 px vẽ ra ngoài, tô gradient dọc 2 màu (đáy → đỉnh)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from gacard.layout import BLOCK_KINDS, FRAME_WIDTH, Box, Layout, Rect
from gacard.theme import RGB, Theme, font, text_weight

RADIUS = FRAME_WIDTH          # bo góc NGOÀI của viền (góc trong vuông, ảnh không bị bo)
CARD_BADGE = 0.33             # cao chữ số / cao thẻ
PANEL_BADGE_W, PANEL_BADGE_H = 0.145, 0.5   # cao chữ số ≤ 0,145 × rộng panel và ≤ 0,5 × cao panel
PANEL_BADGE_LIFT = 0.08       # số panel cao hơn tâm 8 % chiều cao chữ
FROST_BLUR, FROST_DIM, FROST_TINT = 24, 0.45, 0.25   # nền frosted sau lưới thẻ
FOCUS_X = {"left": 0.3, "center": 0.5, "right": 0.7}
LANCZOS = Image.Resampling.LANCZOS


@dataclass
class PieceImages:
    """Ảnh đã mở theo loại khối; chỉ số trùng `Box.index`. Badge None = không đè số."""
    profile: Image.Image | None = None
    characters: list[Image.Image] = field(default_factory=list)
    weapons: list[Image.Image] = field(default_factory=list)
    panels: list[Image.Image] = field(default_factory=list)
    items: list[Image.Image] = field(default_factory=list)
    explorations: list[Image.Image] = field(default_factory=list)
    badges: dict[tuple[str, int], int] = field(default_factory=dict)   # (kind, index) → số
    own_frame: set[tuple[str, int]] = field(default_factory=set)       # mảnh đã có khung riêng (không vẽ khung theme)

    def get(self, kind: str, index: int | None) -> Image.Image | None:
        if kind == "profile":
            return self.profile
        seq = {"character": self.characters, "weapon": self.weapons,
               "panel": self.panels, "item": self.items, "exploration": self.explorations}.get(kind)
        if seq is None or index is None or index >= len(seq):
            return None
        return seq[index]


# ---------------------------------------------------------------- nền

def cover_fit(img: Image.Image, size: tuple[int, int], focus_x: float = 0.5) -> Image.Image:
    """Phủ kín khung (cắt phần thừa), giữ điểm `focus_x` (0–1) trong khung."""
    w, h = size
    scale = max(w / img.width, h / img.height)
    nw, nh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
    resized = img.convert("RGB").resize((nw, nh), LANCZOS)
    left = min(max(0, round(nw * focus_x - w / 2)), nw - w)
    top = (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def default_background(size: tuple[int, int], theme: Theme) -> Image.Image:
    """Nền mặc định: chuyển sắc tối + quầng sáng nhẹ theo màu khung."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = yy / h
    top, bottom = np.array([22, 27, 44], np.float32), np.array([8, 10, 18], np.float32)
    base = top[None, None, :] * (1 - t[..., None]) + bottom[None, None, :] * t[..., None]
    for cx, cy, r, k in ((0.42, 0.38, 0.55, 0.30), (0.85, 0.9, 0.5, 0.12)):
        d = np.sqrt(((xx / w - cx) * 1.6) ** 2 + (yy / h - cy) ** 2) / r
        glow = np.clip(1 - d, 0, 1) ** 2 * k
        base += glow[..., None] * np.array(theme.frame, np.float32)[None, None, :]
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- vẽ

def _vgradient(w: int, h: int, top: RGB, bottom: RGB) -> Image.Image:
    t = np.linspace(0.0, 1.0, max(h, 1), dtype=np.float32)[:, None, None]
    col = np.asarray(top, np.float32) * (1 - t) + np.asarray(bottom, np.float32) * t
    return Image.fromarray(np.broadcast_to(np.round(col), (h, w, 3)).astype(np.uint8), "RGB")


def _fill_mask(canvas: Image.Image, mask: Image.Image, x: int, y: int, theme: Theme) -> None:
    """Tô gradient khung (đỉnh → đáy theo chiều cao mask) vào canvas qua mask đặt tại (x, y)."""
    canvas.paste(_vgradient(mask.width, mask.height, theme.frame_top, theme.frame_bottom), (x, y), mask)


def _frame(canvas: Image.Image, r: Rect, theme: Theme, width: int = FRAME_WIDTH) -> None:
    """Viền `width` px nằm ngoài rect (không che ảnh), gradient dọc theo cả khối."""
    w, h = r.w + 2 * width, r.h + 2 * width
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=RADIUS, fill=255)
    d.rectangle((width, width, width + r.w - 1, width + r.h - 1), fill=0)
    _fill_mask(canvas, mask, r.x - width, r.y - width, theme)


def _outlined_text(canvas: Image.Image, origin: tuple[int, int], text: str, f, stroke: int,
                   theme: Theme) -> None:
    """Chữ trắng, viền `stroke` px tô cùng gradient khung theo bbox của chữ."""
    left, top, right, bottom = f.getbbox(text, stroke_width=stroke)
    mask = Image.new("L", (max(1, right - left), max(1, bottom - top)), 0)
    ImageDraw.Draw(mask).text((-left, -top), text, font=f, fill=255, stroke_width=stroke, stroke_fill=255)
    _fill_mask(canvas, mask, origin[0] + left, origin[1] + top, theme)
    ImageDraw.Draw(canvas).text(origin, text, font=f, fill=theme.text)


def _contain(img: Image.Image, w: int, h: int) -> Image.Image:
    scale = min(w / img.width, h / img.height)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, LANCZOS) if size != img.size else img


def _paste(canvas: Image.Image, img: Image.Image, r: Rect) -> None:
    fitted = _contain(img.convert("RGBA"), r.w, r.h)
    x = r.x + (r.w - fitted.width) // 2
    y = r.y + (r.h - fitted.height) // 2
    canvas.paste(fitted.convert("RGB"), (x, y), fitted.getchannel("A"))


def _frosted(canvas: Image.Image, r: Rect, theme: Theme) -> None:
    """Nền mờ đục sau lưới thẻ (giống nền game giữa các thẻ): làm mờ vùng nền, tối đi,
    pha màu khung đáy, phủ với alpha của theme.fill."""
    pad = 2 * FROST_BLUR
    box = (max(0, r.x - pad), max(0, r.y - pad), min(canvas.width, r.right + pad),
           min(canvas.height, r.bottom + pad))
    blurred = canvas.crop(box).filter(ImageFilter.GaussianBlur(FROST_BLUR))
    inner = (r.x - box[0], r.y - box[1], r.right - box[0], r.bottom - box[1])
    soft = np.asarray(blurred.crop(inner), np.float32)
    tint = np.asarray(theme.fill[:3], np.float32)
    glass = soft * FROST_DIM * (1 - FROST_TINT) + tint * FROST_TINT
    a = theme.fill[3] / 255
    orig = np.asarray(canvas.crop((r.x, r.y, r.right, r.bottom)), np.float32)
    out = glass * a + orig * (1 - a)
    canvas.paste(Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8), "RGB"), (r.x, r.y))


def _draw_text(canvas: Image.Image, box: Box, theme: Theme) -> None:
    f = font(box.size, text_weight(box.kind))
    left, top, _, _ = f.getbbox(box.text or "", stroke_width=box.stroke)
    _outlined_text(canvas, (box.rect.x - left, box.rect.y - top), box.text or "", f, box.stroke, theme)


def _digit_font(digit_h: float):
    """Font hiển thị có chữ số cao ≈ digit_h px."""
    probe = font(100, "display").getbbox("8")
    return font(max(8, round(digit_h * 100 / (probe[3] - probe[1]))), "display")


def _badge(canvas: Image.Image, value: int, r: Rect, theme: Theme, centered: bool) -> None:
    """Số đè: chữ số trắng viền gradient khung FRAME_WIDTH px. Thẻ: cao 0,33 × thẻ, glyph sát góc
    dưới phải (chỉ viền tràn ra); panel: cao min(0,145 × rộng, 0,5 × cao), giữa, hơi cao hơn tâm."""
    text = str(value)
    target = min(r.w * PANEL_BADGE_W, r.h * PANEL_BADGE_H) if centered else r.h * CARD_BADGE
    f = _digit_font(target)
    left, top, right, bottom = f.getbbox(text)            # glyph, không tính viền
    tw, th = right - left, bottom - top
    if centered:
        gx = r.x + (r.w - tw) // 2
        gy = r.y + (r.h - th) // 2 - round(th * PANEL_BADGE_LIFT)
    else:
        gx, gy = r.right - tw, r.bottom - th
    _outlined_text(canvas, (gx - left, gy - top), text, f, FRAME_WIDTH, theme)


def compose(layout: Layout, pieces: PieceImages, theme: Theme,
            background: Image.Image | None = None, subject_side: str | None = None) -> Image.Image:
    """Ảnh RGB kích thước layout (1920×1080)."""
    size = (layout.width, layout.height)
    if background is not None:
        canvas = cover_fit(background, size, FOCUS_X.get(subject_side or "", 0.5))
    else:
        canvas = default_background(size, theme)
    canvas = canvas.convert("RGB")
    blocks = [b for b in layout.boxes if b.kind in BLOCK_KINDS]

    for b in blocks:                   # không bóng đổ; chỉ lưới thẻ có nền frosted
        if b.kind in ("characters", "weapons"):
            _frosted(canvas, b.rect, theme)
    for b in layout.boxes:
        img = pieces.get(b.kind, b.index)
        if img is not None and b.kind == "exploration":   # kéo đúng khung (cao có thể lệch 1 px)
            img = img.convert("RGBA").resize((b.rect.w, b.rect.h), LANCZOS)
        if img is not None:
            _paste(canvas, img, b.rect)
    for b in blocks:
        if (b.kind, b.index) not in pieces.own_frame:
            _frame(canvas, b.rect, theme)
    for b in layout.boxes:
        value = pieces.badges.get((b.kind, b.index)) if b.index is not None else None
        if value:                      # None hoặc 0 (C0) → không vẽ
            _badge(canvas, value, b.rect, theme, centered=b.kind == "panel")
    for b in layout.boxes:
        if b.kind in ("code", "uid", "extra"):
            _draw_text(canvas, b, theme)

    marks = [b for b in layout.boxes if b.kind == "watermark"]
    if not marks:
        return canvas
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for b in marks:
        f = font(b.size, text_weight(b.kind))
        left, top, _, _ = f.getbbox(b.text or "", stroke_width=b.stroke)
        od.text((b.rect.x - left, b.rect.y - top), b.text or "", font=f, fill=(255, 255, 255, 185),
                stroke_width=b.stroke, stroke_fill=(0, 0, 0, 90))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
