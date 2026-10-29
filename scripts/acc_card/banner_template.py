r"""Chuyển khung ô đếm banner PSD của chủ dự án thành PNG + JSON để bot render (không mở PSD mỗi lần).

Quy ước layer trong PSD (xem .claude/skills/acc-card-banner-box/SKILL.md):
    N    layer chữ chứa số (bot thay nội dung, giữ font/cỡ/màu/viền/căn lề)
    TEN  layer chữ tên nhân vật (chỉ khung `lech`)
    ANH  layer / khung ảnh nhân vật (tuỳ chọn, khung `limited`)
Các layer khác giữ nguyên, được ghép thành ảnh nền; ba layer trên bị ẩn khi ghép.

Hai chế độ: (1) layer đặt tên N / TEN / ANH như trên; (2) PSD của chủ dự án chỉ có layer ảnh: mỗi kiểu một layer
"hoàn chỉnh" (mặc định limited = "Layer 4", lech = "Layer 9"; đổi bằng --limited-layer / --lech-layer): dựng
PNG kèm viền Stroke, xoá chữ mẫu, đo bbox / màu / cỡ chữ (xem convert_finished).

Chạy:
    .venv\Scripts\python.exe scripts\acc_card\banner_template.py data\acc-card\templates\banner\banner.psd
    .venv\Scripts\python.exe scripts\acc_card\banner_template.py data\acc-card\templates\banner\lech.psd
    .venv\Scripts\python.exe scripts\acc_card\banner_template.py a.psd --kind limited --out data\acc-card\templates\banner
    thêm --theme-frame nếu PSD KHÔNG có khung (bot sẽ vẽ khung theme quanh ô như các ô khác).
Đầu ra: <out>/<kind>.png + <out>/<kind>.json  (kind mặc định = tên file PSD không đuôi: limited | lech).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SLOT_NAMES = ("N", "TEN", "ANH")
JUSTIFY = {"0": "left", "1": "right", "2": "center", "Left": "left", "Right": "right", "Cntr": "center",
           "Center": "center"}


def slot_from_layer(name: str, bbox: tuple[int, int, int, int], text: dict | None = None,
                    effects: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Layer (bbox + thông tin chữ do inspect_psd._text / _effects trả về) → (slot JSON, cảnh báo)."""
    from gacard.bannerbox import MIN_FONT

    warnings: list[str] = []
    slot: dict[str, Any] = {"bbox": [int(v) for v in bbox]}
    if name == "ANH":
        return slot, warnings
    h = bbox[3] - bbox[1]
    runs = (text or {}).get("runs") or []
    run = runs[0] if runs else {}
    font_name = str(run.get("font") or "")
    slot["font"] = "display" if "hywenhei" in font_name.lower().replace(" ", "") else "bold"
    size = (text or {}).get("effective_size_px") or run.get("size")
    if not isinstance(size, (int, float)):
        size = max(MIN_FONT, round(h * 0.7))
        warnings.append(f"{name}: không đọc được cỡ chữ từ PSD, tạm lấy {size}px theo chiều cao layer")
    slot["size"] = max(MIN_FONT, round(float(size)))
    slot["color"] = run.get("fill") or "#4E4952"
    slot["align"] = JUSTIFY.get(str((text or {}).get("justification")), "center")
    stroke = next((e for e in effects or [] if e.get("type", "").lower().startswith("stroke") and e.get("enabled", True)), None)
    if stroke is not None:
        slot["stroke"] = max(0, round(stroke.get("size") or 0))
        slot["stroke_color"] = stroke.get("color") or "#FFFFFF"
    slot["max_lines"] = 2 if name == "TEN" else 1
    if font_name and slot["font"] == "bold" and "hywenhei" not in font_name.lower():
        warnings.append(f"{name}: font PSD '{font_name}' không có trong dự án, dùng Be Vietnam Pro")
    return slot, warnings


def _walk(layers) -> list:
    out = []
    for layer in layers:
        out.append(layer)
        if layer.is_group():
            out.extend(_walk(layer))
    return out


def convert(psd_path: Path, out_dir: Path, kind: str, own_frame: bool = True) -> tuple[Path, Path, list[str]]:
    from psd_tools import PSDImage

    import inspect_psd as ip
    from gacard.bannerbox import Slot, Template, save_template, _parse_color

    psd = PSDImage.open(psd_path)
    found: dict[str, Any] = {}
    for layer in _walk(psd):
        key = (layer.name or "").strip().upper()
        if key in SLOT_NAMES and key not in found:
            found[key] = layer
    warnings: list[str] = []
    if "N" not in found:
        raise SystemExit("PSD thiếu layer tên 'N' (layer chữ chứa số).")
    if kind == "lech" and "TEN" not in found:
        raise SystemExit("PSD khung lech thiếu layer tên 'TEN'.")
    was_visible = {k: layer.visible for k, layer in found.items()}
    for layer in found.values():
        layer.visible = False
    image = psd.composite(force=True)
    if image is None:
        raise SystemExit("Không ghép được ảnh nền từ PSD.")
    image = image.convert("RGBA")
    for k, layer in found.items():
        layer.visible = was_visible[k]
    slots = {}
    for key, layer in found.items():
        bbox = tuple(max(0, v) for v in layer.bbox)
        text = ip._text(layer) if layer.kind == "type" else None
        if layer.kind != "type" and key != "ANH":
            warnings.append(f"{key}: không phải layer chữ, chỉ lấy vị trí (font/cỡ/màu mặc định)")
        spec, w = slot_from_layer(key, bbox, text, ip._effects(layer) if layer.kind == "type" else None)
        warnings += w
        slots[key] = Slot(tuple(spec["bbox"]), spec.get("font", "display"), spec.get("size", 48),
                          _parse_color(spec.get("color"), (60, 60, 60)), spec.get("align", "center"),
                          spec.get("stroke", 0), _parse_color(spec.get("stroke_color"), (255, 255, 255)),
                          spec.get("max_lines", 1))
    png, js = save_template(Template(kind, image, slots, own_frame, "file"), out_dir)
    return png, js, warnings


# --------------------------------------------------------------------------- chế độ "ô hoàn chỉnh"
# PSD của chủ dự án (banner.psd): chỉ layer ảnh, mỗi kiểu một layer hoàn chỉnh (hình bậc thang: phần trên hẹp,
# thanh "‹ N ›" rộng; viền Stroke gradient cyan "Blue_16" 8px phía ngoài). Bot dựng khung từ layer đó: ghép
# kèm viền, xoá chữ mẫu (số "1", tên "Skirk (5 sao)") bằng màu nền cục bộ, ghi bbox/màu/cỡ chữ để điền lại.
FINISHED_LAYERS = {"limited": "Layer 4", "lech": "Layer 9"}
DARK = 150           # luma dưới mức này = nét chữ số trên thanh sáng
TEXT_DIFF = 40       # khác màu nền nhãn quá mức này = nét chữ tên


def _luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _bbox_of(mask):
    import numpy as np
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _fit_size(text: str, target: float, axis: str) -> int:
    """Cỡ chữ lớn nhất (font hiển thị) để `text` cao / rộng ≤ target px."""
    from gacard.theme import font
    best = 8
    for size in range(8, 160):
        l, t, r, b = font(size, "display").getbbox(text)
        if (r - l if axis == "w" else b - t) <= target:
            best = size
    return best


def _inpaint_rows(img, box, ref_pad: int = 4) -> None:
    """Xoá vùng `box` (x0,y0,x1,y1) trên `img` (numpy RGBA, sửa tại chỗ): mỗi hàng nội suy tuyến tính giữa
    màu ngay bên trái và ngay bên phải vùng (giữ độ chuyển sắc / hạt giấy của thanh)."""
    import numpy as np
    x0, y0, x1, y1 = box
    for y in range(y0, y1):
        left = img[y, max(0, x0 - ref_pad):x0].astype(np.float32).mean(axis=0)
        right = img[y, x1:x1 + ref_pad].astype(np.float32).mean(axis=0)
        t = np.linspace(0, 1, x1 - x0, dtype=np.float32)[:, None]
        img[y, x0:x1] = np.round(left * (1 - t) + right * t).astype(np.uint8)


def convert_finished(psd, layer_name: str, kind: str, sample_n: str = "1", sample_name: str = "Skirk"):
    """Layer hoàn chỉnh → Template (kèm biến thể two_line cho kiểu lech). Trả về (template, cảnh báo)."""
    import numpy as np
    from PIL import Image

    import inspect_psd as ip
    from gacard.bannerbox import NAME_SUFFIX, Slot, Template

    layer = next((l for l in _walk(psd) if (l.name or "").strip() == layer_name), None)
    if layer is None:
        raise SystemExit(f"PSD không có layer '{layer_name}' (khung {kind}).")
    warnings: list[str] = []
    raw = np.asarray(layer.topil().convert("RGBA")).copy()
    h, w = raw.shape[:2]
    alpha = raw[..., 3]
    bar_top = next(y for y in range(h) if alpha[y, 0] > 200)            # hàng đầu của thanh rộng toàn bề ngang
    xs = np.nonzero(alpha[0] > 200)[0]
    lx0, lx1 = int(xs.min()), int(xs.max()) + 1                          # bề ngang phần trên (nhãn / ảnh)
    stroke = next((e for e in ip._effects(layer) if str(e.get("type", "")).startswith("Stroke")
                   and e.get("enabled", True)), None)
    m = int(round(stroke.get("size") or 0)) if stroke else 0
    own_frame = m > 0
    # ảnh có viền: ghép riêng layer này (ẩn các layer khác), cắt theo bbox + viền
    states = {id(l): l.visible for l in _walk(psd)}
    for l in _walk(psd):
        l.visible = l is layer
    try:
        full = psd.composite(force=True).convert("RGBA")
    finally:
        for l in _walk(psd):
            l.visible = states[id(l)]
    x0, y0 = layer.bbox[0], layer.bbox[1]
    img = np.asarray(full.crop((x0 - m, y0 - m, x0 + w + m, y0 + h + m))).copy()

    # --- số N trên thanh: nét tối ở giữa thanh
    c0, c1 = int(w * 0.3), int(w * 0.7)
    bar = raw[bar_top:, c0:c1]
    dark = _luma(bar.astype(np.float32)) < DARK
    nb = _bbox_of(dark)
    if nb is None:
        raise SystemExit("Không tìm thấy chữ số mẫu trên thanh dưới.")
    nx0, ny0, nx1, ny1 = nb[0] + c0, nb[1] + bar_top, nb[2] + c0, nb[3] + bar_top
    lum = _luma(bar.astype(np.float32))
    core = lum <= np.percentile(lum[dark], 15)                            # lõi nét chữ (bỏ viền khử răng cưa)
    n_color = tuple(int(v) for v in bar[core].astype(np.float32).mean(axis=0)[:3])
    _inpaint_rows(img, (nx0 - 2 + m, ny0 - 2 + m, nx1 + 2 + m, ny1 + 2 + m))
    n_size = round(_fit_size(sample_n, ny1 - ny0, "h") * 1.2)      # font mẫu đậm / to hơn HYWenHei một chút
    slots = {"N": Slot((c0 + m, bar_top + m, c1 + m, h + m), "display", n_size, n_color)}
    anh_default = None
    two_line = None
    if kind == "lech":
        label = raw[:bar_top, lx0:lx1, :3].astype(np.float32)
        bg = np.median(label.reshape(-1, 3), axis=0)
        diff = np.abs(label - bg).max(axis=2)
        tb = _bbox_of(diff > TEXT_DIFF)
        if tb is None:
            raise SystemExit("Không tìm thấy tên mẫu trên nhãn.")
        tx0, ty0, tx1, ty1 = tb[0] + lx0, tb[1], tb[2] + lx0, tb[3]
        t_color = tuple(int(v) for v in label[diff > TEXT_DIFF * 2.5].mean(axis=0))
        img[ty0 - 2 + m:ty1 + 2 + m, tx0 - 2 + m:tx1 + 2 + m] = (*[int(round(v)) for v in bg], 255)
        t_size = _fit_size(f"{sample_name} {NAME_SUFFIX}", tx1 - tx0, "w")
        pad = 4
        slots["TEN"] = Slot((lx0 + pad + m, 2 + m, lx1 - pad + m, bar_top - 2 + m), "display", t_size, t_color,
                            max_lines=2)
        # biến thể 2 dòng: kéo giãn thân nhãn thêm `extra` hàng (không có chữ, gradient viền giãn mượt)
        extra = int(round(t_size * 1.15))
        a, b = m + 8, m + bar_top - 9
        if b - a >= 6:
            mid = img[a:b]
            mid2 = np.asarray(Image.fromarray(mid).resize((mid.shape[1], mid.shape[0] + extra),
                                                          Image.Resampling.BICUBIC))
            img2 = np.concatenate([img[:a], mid2, img[b:]], axis=0)
            n1 = slots["N"]
            s2 = {"N": Slot((n1.bbox[0], n1.bbox[1] + extra, n1.bbox[2], n1.bbox[3] + extra), n1.font, n1.size,
                            n1.color),
                  "TEN": Slot((lx0 + pad + m, 2 + m, lx1 - pad + m, bar_top - 2 + m + extra), "display", t_size,
                              t_color, max_lines=2)}
            two_line = Template(kind, Image.fromarray(img2), s2, own_frame, "file")
    else:
        art = raw[:bar_top, lx0:lx1, :3].astype(np.float32)
        light = art[_luma(art) > 215]
        fill = tuple(int(v) for v in np.median(light, axis=0)) if len(light) else (245, 238, 210)
        slots["ANH"] = Slot((lx0 + m, m, lx1 + m, bar_top + m), inset=6, fill=fill)
        anh_default = sample_name
    return Template(kind, Image.fromarray(img), slots, own_frame, "file", anh_default, two_line), warnings


def build_finished(psd_path: Path, out_dir: Path, kinds: list[str], layers: dict[str, str]) -> list[str]:
    from psd_tools import PSDImage

    from gacard.bannerbox import save_template

    psd = PSDImage.open(psd_path)
    done = []
    for kind in kinds:
        t, warnings = convert_finished(psd, layers[kind], kind)
        for w in warnings:
            print("Cảnh báo:", w)
        png, js = save_template(t, out_dir)
        done += [str(png), str(js)]
    return done


def has_named_layers(psd_path: Path) -> bool:
    from psd_tools import PSDImage
    return any((l.name or "").strip().upper() == "N" for l in _walk(PSDImage.open(psd_path)))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="PSD khung banner → PNG + JSON cho gacard")
    p.add_argument("psd", type=Path)
    p.add_argument("--kind", choices=("limited", "lech"), help="chỉ dựng kiểu này (mặc định: cả hai / theo tên file)")
    p.add_argument("--out", type=Path, default=ROOT / "data" / "acc-card" / "templates" / "banner")
    p.add_argument("--limited-layer", default=FINISHED_LAYERS["limited"], help="tên layer ô limited hoàn chỉnh")
    p.add_argument("--lech-layer", default=FINISHED_LAYERS["lech"], help="tên layer ô lệch hoàn chỉnh")
    p.add_argument("--theme-frame", action="store_true", help="(chế độ layer N/TEN/ANH) PSD không có khung")
    args = p.parse_args(argv)
    if has_named_layers(args.psd):
        kind = args.kind or args.psd.stem.lower()
        if kind not in ("limited", "lech"):
            print("Không đoán được kiểu khung từ tên file; dùng --kind limited|lech", file=sys.stderr)
            return 2
        png, js, warnings = convert(args.psd, args.out, kind, own_frame=not args.theme_frame)
        for w in warnings:
            print("Cảnh báo:", w)
        print(f"Đã ghi {png} và {js}")
        return 0
    kinds = [args.kind] if args.kind else ["limited", "lech"]
    done = build_finished(args.psd, args.out, kinds, {"limited": args.limited_layer, "lech": args.lech_layer})
    print("Đã ghi:", *done, sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
