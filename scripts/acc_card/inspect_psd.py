"""Dump cấu trúc file PSD (cây layer, chữ, hiệu ứng, mask) ra JSON + xuất ảnh composite/thumbnail.

Dùng để đọc template Photoshop của chủ dự án và so với bố cục tự động của gacard.
File PSD là tài sản riêng, nằm trong data/ (git bỏ qua); đầu ra cũng ghi vào data/.

Chạy:
    .venv\\Scripts\\python.exe scripts\\acc_card\\inspect_psd.py                      # mọi *.psd trong data/acc-card/psd
    .venv\\Scripts\\python.exe scripts\\acc_card\\inspect_psd.py path\\a.psd --no-png
Đầu ra cho mỗi <name>.psd:
    <dir>/<name>.layers.json        cây layer + text + effects
    <dir>/<name>/composite.png      ảnh ghép toàn bộ
    <dir>/<name>/NN_<layer>.png     ảnh từng layer cấp cao nhất (đã cắt theo bbox)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from psd_tools import PSDImage

ROOT = Path(__file__).resolve().parents[2]
PSD_DIR = ROOT / "data" / "acc-card" / "psd"
THUMB_MAX = 1600      # cạnh dài tối đa ảnh thumbnail layer


# --------------------------------------------------------------------------- tiện ích


def _plain(value: Any, depth: int = 0) -> Any:
    """Chuyển giá trị descriptor của psd-tools về kiểu JSON (bỏ qua cái không đổi được)."""
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace").strip("\x00")
        except Exception:  # noqa: BLE001
            return repr(value)[:80]
    if hasattr(value, "items"):
        out = {}
        for k, v in value.items():
            key = k.decode("latin-1") if isinstance(k, bytes) else str(k)
            key = key.strip("'\"").replace("b'", "")
            out[key] = _plain(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(v, depth + 1) for v in value]
    if hasattr(value, "value"):
        return _plain(value.value, depth + 1)
    return str(value)


def _color(desc: Any) -> str | None:
    """Descriptor màu (RGBC / Grsc) → '#RRGGBB'."""
    if desc is None:
        return None
    try:
        d = {str(k).strip("'\"b"): v for k, v in desc.items()}
    except AttributeError:
        return str(desc)
    def num(*keys):
        for k in keys:
            for dk, dv in d.items():
                if dk.endswith(k):
                    return float(getattr(dv, "value", dv))
        return None
    r, g, b = num("Rd  ", "Rd"), num("Grn ", "Grn"), num("Bl  ", "Bl")
    if r is None:
        r, g, b = num("redFloat"), num("greenFloat"), num("blueFloat")
        if r is not None:
            r, g, b = r * 255, g * 255, b * 255
    if r is None:
        gray = num("Gry ", "Gry")
        if gray is not None:
            v = 255 * (1 - gray / 100)
            r = g = b = v
    if r is None:
        return None
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c))) for c in (r, g, b)))


def _unit(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(getattr(v, "value", v)), 2)
    except (TypeError, ValueError):
        return None


def _enum(v: Any) -> str | None:
    if v is None:
        return None
    s = str(getattr(v, "enum", v))
    return s.strip("'\"b").replace("b'", "")


def _get(d: Any, *keys: str) -> Any:
    """Lấy khoá descriptor bất kể dạng bytes/str/Key enum."""
    if d is None:
        return None
    try:
        items = list(d.items())
    except AttributeError:
        return None
    for want in keys:
        for k, v in items:
            ks = str(k)
            if ks == want or ks.strip("'\"b ") == want.strip() or ks.endswith(want) or ks.endswith(f"{want}'"):
                return v
    return None


# --------------------------------------------------------------------------- hiệu ứng


def _effects(layer) -> list[dict]:
    """Layer effects (stroke, shadow, glow, overlay) từ psd-tools; mỗi cái một dict tóm tắt."""
    out = []
    try:
        effects = layer.effects
    except Exception:  # noqa: BLE001
        return out
    if not effects:
        return out
    for fx in effects:
        kind = type(fx).__name__
        item: dict[str, Any] = {"type": kind, "enabled": bool(getattr(fx, "enabled", True))}
        for attr in ("size", "distance", "angle", "opacity", "choke", "range", "noise", "scale"):
            if hasattr(fx, attr):
                item[attr] = _unit(getattr(fx, attr))
        for attr in ("position", "blend_mode", "fill_type", "style", "type", "technique"):
            if hasattr(fx, attr) and attr != "type":
                item[attr] = _enum(getattr(fx, attr))
        try:
            if hasattr(fx, "color"):
                item["color"] = _color(fx.color)
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(fx, "gradient") and fx.gradient is not None:
                item["gradient"] = _gradient(fx.gradient)
                desc = getattr(fx, "descriptor", None) or getattr(fx, "value", None)
                # Photoshop: Angl=90 → vị trí 0 ở ĐÁY, 100% ở ĐỈNH; Algn=True → trải theo bbox layer
                item["gradient"]["angle"] = _unit(_get(desc, "Angl"))
                item["gradient"]["style"] = _enum(_get(desc, "Type"))
                item["gradient"]["align_with_layer"] = _plain(_get(desc, "Algn"))
                item["gradient"]["reverse"] = _plain(_get(desc, "Rvrs"))
                if "GRADIENT" in str(item.get("fill_type", "")):
                    item["color"] = None          # Clr  bị bỏ qua khi tô gradient
        except Exception:  # noqa: BLE001
            pass
        out.append(item)
    return out


def _gradient(desc: Any) -> dict:
    stops = []
    clrs = _get(desc, "Clrs")
    for s in clrs or []:
        stops.append({"color": _color(_get(s, "Clr ")), "loc": _unit(_get(s, "Lctn"))})
    trns = _get(desc, "Trns")
    alpha = [{"opacity": _unit(_get(t, "Opct")), "loc": _unit(_get(t, "Lctn"))} for t in (trns or [])]
    return {"name": _plain(_get(desc, "Nm  ")), "stops": stops, "alpha": alpha}


# --------------------------------------------------------------------------- chữ


def _text(layer) -> dict | None:
    if layer.kind != "type":
        return None
    info: dict[str, Any] = {"text": layer.text}
    try:
        es = layer.engine_dict
        rd = layer.resource_dict
        fonts = [_plain(f.get("Name")) for f in rd.get("FontSet", [])]
        runs = []
        style_runs = es["StyleRun"]
        lengths = list(style_runs["RunLengthArray"])
        for length, run in zip(lengths, style_runs["RunArray"]):
            sd = run["StyleSheet"]["StyleSheetData"]
            r: dict[str, Any] = {"length": int(length)}
            if "Font" in sd:
                idx = int(sd["Font"])
                r["font"] = fonts[idx] if idx < len(fonts) else idx
            for key, name in (("FontSize", "size"), ("Tracking", "tracking"), ("Leading", "leading"),
                              ("HorizontalScale", "hscale"), ("VerticalScale", "vscale"),
                              ("FauxBold", "faux_bold"), ("FauxItalic", "faux_italic"),
                              ("OutlineWidth", "outline_width"), ("StrokeFlag", "stroke_flag"),
                              ("FillFlag", "fill_flag")):
                if key in sd:
                    r[name] = _plain(sd[key])
            for key, name in (("FillColor", "fill"), ("StrokeColor", "stroke")):
                if key in sd:
                    vals = [float(v) for v in sd[key]["Values"]]   # ARGB 0..1
                    if len(vals) == 4:
                        r[name] = "#{:02X}{:02X}{:02X}".format(*(round(v * 255) for v in vals[1:]))
                        r[name + "_alpha"] = round(vals[0], 3)
            runs.append(r)
        info["runs"] = runs
        para = es.get("ParagraphRun", {}).get("RunArray", [])
        if para:
            ps = para[0]["ParagraphSheet"]["Properties"]
            info["justification"] = _plain(ps.get("Justification"))
    except Exception as exc:  # noqa: BLE001
        info["engine_error"] = repr(exc)[:200]
    # ma trận biến đổi → cỡ chữ hiệu dụng (Photoshop lưu FontSize trước khi scale)
    try:
        t = layer.transform   # (xx, xy, yx, yy, tx, ty)
        info["transform"] = [round(float(v), 4) for v in t]
        sy = (float(t[2]) ** 2 + float(t[3]) ** 2) ** 0.5
        if info.get("runs") and isinstance(info["runs"][0].get("size"), (int, float)):
            info["effective_size_px"] = round(info["runs"][0]["size"] * sy, 1)
    except Exception:  # noqa: BLE001
        pass
    return info


# --------------------------------------------------------------------------- cây layer


def _bbox(layer, cw: int, ch: int) -> dict:
    x0, y0, x1, y1 = layer.bbox
    k = 1920 / cw    # tỉ lệ đều theo chiều rộng (canvas không phải 16:9 thì trục y không bị méo)
    return {"px": [x0, y0, x1, y1], "w": x1 - x0, "h": y1 - y0,
            "frac": [round(x0 / cw, 4), round(y0 / ch, 4), round(x1 / cw, 4), round(y1 / ch, 4)],
            "at1920": [round(v * k, 1) for v in (x0, y0, x1, y1)]}


def _masks(layer) -> dict:
    out: dict[str, Any] = {}
    try:
        if layer.has_mask() and layer.mask is not None:
            m = layer.mask
            out["mask"] = {"bbox": list(m.bbox), "disabled": bool(getattr(m, "disabled", False))}
    except Exception:  # noqa: BLE001
        pass
    try:
        if layer.has_vector_mask():
            vm = layer.vector_mask
            out["vector_mask"] = {"bbox": [round(v, 4) for v in vm.bbox] if vm.bbox else None,
                                  "paths": len(vm.paths)}
    except Exception:  # noqa: BLE001
        pass
    try:
        if getattr(layer, "has_origination", lambda: False)():
            out["origination"] = [_plain(o.__class__.__name__) for o in layer.origination]
    except Exception:  # noqa: BLE001
        pass
    return out


def _smart(layer) -> dict | None:
    if layer.kind != "smartobject":
        return None
    try:
        so = layer.smart_object
        return {"filename": _plain(so.filename), "filetype": _plain(so.filetype),
                "kind": _plain(so.kind), "size": list(getattr(so, "size", []) or [])}
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)[:120]}


def _fill(layer) -> dict | None:
    """Màu của shape/fill layer (solid color / gradient)."""
    try:
        for key in (b"SoCo", b"GdFl", b"PtFl"):
            block = layer.tagged_blocks.get_data(key) if layer.tagged_blocks else None
            if block is not None:
                if key == b"SoCo":
                    return {"solid": _color(_get(block, "Clr "))}
                if key == b"GdFl":
                    return {"gradient": _gradient(_get(block, "Grad"))}
                return {"pattern": True}
    except Exception:  # noqa: BLE001
        pass
    try:
        so = layer.stroke   # vector stroke của shape
        if so is not None:
            return {"vector_stroke": {"width": _unit(getattr(so, "line_width", None)),
                                      "enabled": getattr(so, "enabled", None),
                                      "alignment": _enum(getattr(so, "line_alignment", None))}}
    except Exception:  # noqa: BLE001
        pass
    return None


def _node(layer, cw: int, ch: int) -> dict:
    d: dict[str, Any] = {
        "name": layer.name,
        "kind": layer.kind,
        "visible": layer.visible,
        "opacity": round(layer.opacity / 255, 3),
        "blend": _enum(layer.blend_mode),
        "clipping": bool(getattr(layer, "clipping", False)),
        "bbox": _bbox(layer, cw, ch),
    }
    try:
        fo = layer.tagged_blocks.get_data(b"iOpa") if layer.tagged_blocks else None
        if fo is not None:
            d["fill_opacity"] = round(int(fo) / 255, 3)
    except Exception:  # noqa: BLE001
        pass
    for key, fn in (("text", _text), ("smart", _smart), ("fill", _fill)):
        v = fn(layer)
        if v:
            d[key] = v
    fx = _effects(layer)
    if fx:
        d["effects"] = fx
    m = _masks(layer)
    if m:
        d.update(m)
    if layer.is_group():
        d["children"] = [_node(c, cw, ch) for c in layer]
    return d


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")[:40] or "layer"


def inspect(path: Path, out_dir: Path, png: bool = True) -> dict:
    psd = PSDImage.open(path)
    cw, ch = psd.width, psd.height
    tree = {"file": path.name, "size": [cw, ch], "scale_to_1920": round(1920 / cw, 4),
            "color_mode": _enum(psd.color_mode),
            "depth": psd.depth, "layers": [_node(l, cw, ch) for l in psd]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{path.stem}.layers.json").write_text(
        json.dumps(tree, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if png:
        img_dir = out_dir / path.stem
        img_dir.mkdir(exist_ok=True)
        try:
            comp = psd.composite(force=False)
        except Exception:  # noqa: BLE001
            comp = psd.composite(force=True)
        comp.convert("RGB").save(img_dir / "composite.png")
        for i, layer in enumerate(psd):
            try:
                im = layer.composite(force=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {layer.name}: {exc!r}"[:160])
                continue
            if im is None:
                continue
            if max(im.size) > THUMB_MAX:
                k = THUMB_MAX / max(im.size)
                im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))))
            im.save(img_dir / f"{i:02d}_{_safe(layer.name)}.png")
    return tree


def _fx_color(e: dict) -> str | None:
    g = e.get("gradient")
    if e.get("color") is None and g and g.get("stops"):
        return "->".join(str(st["color"]) for st in g["stops"]) + f"@{g.get('angle')}"
    return e.get("color")


def _print_tree(nodes: list[dict], indent: int = 0) -> None:
    for n in nodes:
        b = n["bbox"]["at1920"]
        extra = ""
        if "text" in n:
            t = n["text"]
            run = (t.get("runs") or [{}])[0]
            extra = f" «{t['text'][:30]!s}» {run.get('font')} {t.get('effective_size_px')}px fill={run.get('fill')}"
        if "effects" in n:
            extra += " fx=" + ",".join(f"{e['type']}({e.get('size')},{_fx_color(e)})" for e in n["effects"] if e["enabled"])
        vis = "" if n["visible"] else " [ẩn]"
        print(f"{'  ' * indent}- {n['name']!s:30} {n['kind']:11} op={n['opacity']} {n['blend']} "
              f"@1920={b}{vis}{extra}")
        if "children" in n:
            _print_tree(n["children"], indent + 1)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*", type=Path, help="file .psd (mặc định: mọi file trong data/acc-card/psd)")
    ap.add_argument("--out", type=Path, default=None, help="thư mục ghi (mặc định: cạnh file PSD)")
    ap.add_argument("--no-png", action="store_true", help="chỉ ghi JSON")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    files = args.files or sorted(PSD_DIR.glob("*.psd"))
    if not files:
        print(f"Không có file PSD trong {PSD_DIR}")
        return 1
    for f in files:
        print(f"== {f.name}")
        tree = inspect(f, args.out or f.parent, png=not args.no_png)
        print(f"   {tree['size'][0]}x{tree['size'][1]}, {len(tree['layers'])} layer cấp cao nhất")
        if not args.quiet:
            _print_tree(tree["layers"], 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
