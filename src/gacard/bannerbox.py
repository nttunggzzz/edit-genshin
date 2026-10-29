"""Ô đếm banner "‹ N ›" của ảnh acc. Chủ dự án tự làm KHUNG bằng Photoshop; bot không tự vẽ khung mà
đặt khung mẫu rồi chỉ điền chữ (số N; tên nhân vật nếu lệch 50/50; ảnh nhân vật nếu mẫu có ô ảnh).

Khung mẫu = data/acc-card/templates/banner/<kiểu>.json + <kiểu>.png (kiểu: limited | lech), sinh từ PSD bằng
scripts/acc_card/banner_template.py. JSON:
    {"version": 1, "size": [w, h], "background": "limited.png", "own_frame": true,
     "slots": {"N":   {"bbox": [x0, y0, x1, y1], "font": "display", "size": 80, "color": "#4E4952",
                       "align": "center", "stroke": 0, "stroke_color": "#FFFFFF", "max_lines": 1},
               "TEN": {... "max_lines": 2},          # chỉ kiểu lech
               "ANH": {"bbox": [...], "inset": 6, "fill": "#F5EEC8"}},   # tuỳ chọn, chỉ kiểu limited
     "anh_default": "Skirk",       # nhân vật có sẵn trong ảnh mẫu (nhân vật khác mà không có ảnh → ô ảnh để trống)
     "two_line": {"background": "lech2.png", "size": [w, h2], "slots": {...}}}   # tuỳ chọn: nhãn cao hơn
                                   # cho tên dài (2 dòng "<Tên>" / "(5 sao)")
Thiếu / hỏng file → dùng khung tạm vẽ trong code (mô phỏng mẫu lech-mizuki-sample.png) kèm cảnh báo.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from gacard.theme import font as theme_font
from gacard.uidmode import data_dir

log = logging.getLogger(__name__)
LANCZOS = Image.Resampling.LANCZOS

KINDS = ("limited", "lech")
MAX_TEMPLATE_PX = 4096
FONT_KINDS = ("display", "bold", "extrabold")
MIN_FONT = 10
NAME_SUFFIX = "(5 sao)"


@dataclass
class Slot:
    bbox: tuple[int, int, int, int]
    font: str = "display"                 # display | bold | extrabold | tên file trong data/acc-card/fonts
    size: int = 48
    color: tuple[int, int, int] = (60, 60, 60)
    align: str = "center"                 # center | left | right
    stroke: int = 0
    stroke_color: tuple[int, int, int] = (255, 255, 255)
    max_lines: int = 1
    inset: int = 0                        # ANH: ảnh nhân vật thu vào cách mép bbox (giữ viền hoa văn của mẫu)
    fill: tuple[int, int, int] | None = None   # ANH: màu nền kem phía sau ảnh


@dataclass
class Template:
    kind: str
    image: Image.Image                    # RGBA, đã bỏ các layer N / TEN / ANH
    slots: dict[str, Slot] = field(default_factory=dict)
    own_frame: bool = False               # khung đã nằm trong ảnh mẫu → compose không vẽ thêm khung theme
    source: str = "file"                  # file | fallback
    anh_default: str | None = None        # nhân vật vẽ sẵn trong ô ANH của ảnh mẫu
    two_line: Template | None = None      # biến thể nhãn cao hơn cho tên dài (2 dòng)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


# ------------------------------------------------------------------ đọc / ghi mẫu
def templates_dir() -> Path:
    return data_dir() / "templates" / "banner"


def _hex(c: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


def _parse_color(v, default):
    if isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v.strip()):
        s = v.strip().lstrip("#")
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return default


def _slot_from_json(name: str, d: dict, size: tuple[int, int]) -> Slot:
    bbox = [int(round(float(v))) for v in d["bbox"]]
    if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"slot {name}: bbox không hợp lệ")
    x0, y0, x1, y1 = bbox
    bbox = [max(0, x0), max(0, y0), min(size[0], x1), min(size[1], y1)]
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"slot {name}: bbox nằm ngoài ảnh mẫu")
    return Slot(tuple(bbox), str(d.get("font") or "display"), max(MIN_FONT, int(d.get("size") or 48)),
                _parse_color(d.get("color"), (60, 60, 60)), str(d.get("align") or "center"),
                max(0, int(d.get("stroke") or 0)), _parse_color(d.get("stroke_color"), (255, 255, 255)),
                max(1, int(d.get("max_lines") or 1)), max(0, int(d.get("inset") or 0)),
                _parse_color(d.get("fill"), None) if d.get("fill") else None)


def _read_image(d: Path, spec: dict, default_name: str) -> Image.Image:
    bg_path = (d / str(spec.get("background") or default_name)).resolve()
    if not bg_path.is_relative_to(d.resolve()):
        raise ValueError("ảnh nền nằm ngoài thư mục mẫu")
    with Image.open(bg_path) as im:
        if max(im.size) > MAX_TEMPLATE_PX:
            raise ValueError("ảnh mẫu quá lớn")
        return im.convert("RGBA")


def _read_slots(spec: dict, size: tuple[int, int]) -> dict[str, Slot]:
    return {str(k).upper(): _slot_from_json(str(k), v, size) for k, v in (spec.get("slots") or {}).items()}


def load_template(kind: str, directory: Path | None = None) -> Template:
    """Mẫu từ file; ném ValueError/OSError nếu thiếu hoặc sai."""
    d = Path(directory) if directory else templates_dir()
    spec = json.loads((d / f"{kind}.json").read_text(encoding="utf-8"))
    image = _read_image(d, spec, f"{kind}.png")
    slots = _read_slots(spec, image.size)
    if "N" not in slots:
        raise ValueError("mẫu thiếu slot N")
    if kind == "lech" and "TEN" not in slots:
        raise ValueError("mẫu lech thiếu slot TEN")
    t = Template(kind, image, slots, bool(spec.get("own_frame", True)), "file",
                 str(spec["anh_default"]) if spec.get("anh_default") else None)
    two = spec.get("two_line")
    if isinstance(two, dict):
        img2 = _read_image(d, two, f"{kind}2.png")
        slots2 = _read_slots(two, img2.size)
        if "N" not in slots2 or "TEN" not in slots2:
            raise ValueError("biến thể two_line thiếu slot N / TEN")
        t.two_line = Template(kind, img2, slots2, t.own_frame, "file")
    return t


def _slots_json(slots: dict[str, Slot]) -> dict:
    out = {}
    for name, sl in slots.items():
        d = {"bbox": list(sl.bbox), "font": sl.font, "size": sl.size, "color": _hex(sl.color), "align": sl.align,
             "stroke": sl.stroke, "stroke_color": _hex(sl.stroke_color), "max_lines": sl.max_lines}
        if sl.inset:
            d["inset"] = sl.inset
        if sl.fill:
            d["fill"] = _hex(sl.fill)
        out[name] = d
    return out


def save_template(t: Template, directory: Path) -> tuple[Path, Path]:
    """Ghi <kind>.png + <kind>.json (+ <kind>2.png nếu có biến thể two_line); dùng cho script chuyển PSD và test."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    png, js = d / f"{t.kind}.png", d / f"{t.kind}.json"
    t.image.save(png)
    spec = {"version": 1, "size": list(t.size), "background": png.name, "own_frame": t.own_frame,
            "slots": _slots_json(t.slots)}
    if t.anh_default:
        spec["anh_default"] = t.anh_default
    if t.two_line is not None:
        png2 = d / f"{t.kind}2.png"
        t.two_line.image.save(png2)
        spec["two_line"] = {"size": list(t.two_line.size), "background": png2.name,
                            "slots": _slots_json(t.two_line.slots)}
    js.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return png, js


# ------------------------------------------------------------------ khung tạm (mô phỏng mẫu lech của chủ dự án)
FB_SIZE = (528, 222)                      # mẫu 176×74 nhân 3
FB_LABEL = (57, 0, 471, 114)              # phần trên (ảnh / tên), hẹp hơn thanh dưới
FB_BAR_TOP = 111
FB_FRAME = 9
FB_CREAM = (246, 241, 231)
FB_BAR = (233, 229, 222)
FB_FRAME_TOP, FB_FRAME_BOTTOM = (229, 19, 110), (226, 118, 62)


def _fallback_image() -> Image.Image:
    ss = 2
    w, h = FB_SIZE[0] * ss, FB_SIZE[1] * ss
    f = FB_FRAME * ss
    lx0, ly0, lx1, _ = (v * ss for v in FB_LABEL)
    bt = FB_BAR_TOP * ss
    outer = Image.new("L", (w, h), 0)
    od = ImageDraw.Draw(outer)
    od.rectangle((lx0, 0, lx1 - 1, bt + 2 * ss), fill=255)
    od.rectangle((0, bt, w - 1, h - 1), fill=255)
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / max(1, h - 1)
        gd.line((0, y, w, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(FB_FRAME_TOP, FB_FRAME_BOTTOM)))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.paste(grad, (0, 0), outer)
    d = ImageDraw.Draw(img)
    d.rectangle((lx0 + f, f, lx1 - 1 - f, bt + f), fill=(*FB_CREAM, 255))
    d.rectangle((f, bt + f, w - 1 - f, h - 1 - f), fill=(*FB_BAR, 255))
    # nút tròn ‹ ›: viền mờ, mũi tên trái xám nhạt, phải xám đậm (như mẫu)
    cy = (bt + f + h - f) // 2
    r = round(36 * ss)
    for cx, direction, color in ((90 * ss, -1, (172, 170, 168)), (438 * ss, 1, (79, 72, 72))):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 236, 228, 255), outline=(222, 212, 196, 255),
                  width=2 * ss)
        k = r * 0.36
        pts = [(cx - direction * k * 0.5, cy - k), (cx + direction * k * 0.5, cy), (cx - direction * k * 0.5, cy + k)]
        d.line(pts, fill=(*color, 255), width=round(r * 0.22), joint="curve")
    return img.resize(FB_SIZE, LANCZOS)


def fallback_template(kind: str) -> Template:
    img = _fallback_image()
    f = FB_FRAME
    inner = (FB_LABEL[0] + f + 6, f + 6, FB_LABEL[2] - f - 6, FB_BAR_TOP + f - 6)
    n_slot = Slot((190, FB_BAR_TOP + f + 6, 338, FB_SIZE[1] - f - 6), "display", 84, (78, 73, 82))
    slots = {"N": n_slot}
    if kind == "lech":
        slots["TEN"] = Slot(inner, "bold", 52, (190, 110, 50), max_lines=2)
    else:
        slots["ANH"] = Slot(inner)
    return Template(kind, img, slots, own_frame=True, source="fallback")


def get_template(kind: str, directory: Path | None = None) -> tuple[Template, str | None]:
    """(mẫu, cảnh báo). Có file hợp lệ → dùng; không → khung tạm + cảnh báo."""
    try:
        return load_template(kind, directory), None
    except FileNotFoundError:
        why = "chưa có file"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        why = f"file hỏng ({type(exc).__name__})"
    return fallback_template(kind), (f"Khung banner '{kind}': {why} trong data/acc-card/templates/banner/, "
                                     f"dùng khung tạm (mô phỏng mẫu).")


# ------------------------------------------------------------------ điền chữ / ảnh
def _slot_font(slot: Slot, size: int) -> ImageFont.FreeTypeFont:
    if slot.font in FONT_KINDS:
        return theme_font(size, slot.font)
    name = Path(slot.font).name                       # chỉ tên file trong thư mục font của dự án
    path = data_dir() / "fonts" / name
    if name == slot.font and path.is_file():
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return theme_font(size, "display")


def _layout_lines(slot: Slot, lines: list[str]) -> tuple[ImageFont.FreeTypeFont, list[float]]:
    """Cỡ chữ lớn nhất ≤ slot.size sao cho mọi dòng vừa bbox (rộng và cao, tính cả nét lên/xuống thật của
    glyph); trả về (font, toạ độ y đường cơ sở từng dòng) với khối chữ canh giữa bbox."""
    x0, y0, x1, y1 = slot.bbox
    bw, bh = x1 - x0, y1 - y0
    cy = (y0 + y1) / 2
    d = ImageDraw.Draw(Image.new("L", (8, 8)))
    size = slot.size
    while True:
        f = _slot_font(slot, size)
        widths = [d.textlength(t, font=f) + 2 * slot.stroke for t in lines]
        asc = desc = 0
        for t in lines:
            _, top, _, bottom = f.getbbox(t, anchor="ls", stroke_width=slot.stroke)
            asc, desc = max(asc, -top), max(desc, bottom)
        step = round(size * 1.12)
        block = asc + desc + step * (len(lines) - 1)
        if (max(widths) <= bw and block <= bh) or size <= MIN_FONT:
            first = cy - block / 2 + asc
            return f, [first + i * step for i in range(len(lines))]
        size -= 1


def _draw_lines(img: Image.Image, slot: Slot, lines: list[str]) -> None:
    x0, _, x1, _ = slot.bbox
    f, baselines = _layout_lines(slot, lines)
    d = ImageDraw.Draw(img)
    for text, y in zip(lines, baselines):
        if slot.align == "left":
            xy, anchor = (x0 + slot.stroke, y), "ls"
        elif slot.align == "right":
            xy, anchor = (x1 - slot.stroke, y), "rs"
        else:
            xy, anchor = ((x0 + x1) / 2, y), "ms"
        d.text(xy, text, font=f, fill=(*slot.color, 255), anchor=anchor,
               stroke_width=slot.stroke, stroke_fill=(*slot.stroke_color, 255))


def _one_line_fits(ten: Slot, name: str) -> bool:
    d = ImageDraw.Draw(Image.new("L", (8, 8)))
    w = d.textlength(f"{name.strip()} {NAME_SUFFIX}", font=_slot_font(ten, ten.size)) + 2 * ten.stroke
    return w <= ten.bbox[2] - ten.bbox[0]


def render_banner_box(template: Template, n: int, name: str | None = None) -> Image.Image:
    """Mẫu + số N; kiểu lech: "<Tên> (5 sao)" vào slot TEN (một dòng; tên dài quá nhãn thì dùng biến thể
    `two_line`: "<Tên>" / "(5 sao)"); kiểu limited: giữ nguyên ảnh của chủ trong mẫu, chỉ điền N."""
    ten = template.slots.get("TEN")
    two = False
    if ten is not None and name and not _one_line_fits(ten, name) and template.two_line is not None:
        template, ten, two = template.two_line, template.two_line.slots["TEN"], True
    img = template.image.copy()
    if ten is not None and name:
        lines = [name.strip(), NAME_SUFFIX] if two else [f"{name.strip()} {NAME_SUFFIX}"]
        _draw_lines(img, ten, lines)
    _draw_lines(img, template.slots["N"], [str(int(n))])
    return img


def choose_kind(last5: str | None, limited: bool | None) -> str:
    """Kiểu khung: 5★ gần nhất là nhân vật thường trú (lệch 50/50) → lech (điền tên + N); còn lại
    (giới hạn, hoặc chưa có 5★ nào) → limited (điền N, ảnh nhân vật nếu mẫu có ô ANH)."""
    return "lech" if last5 and not limited else "limited"
