"""Cat manh tu anh mau DA CHINH TAY -> sheet gia lap (AccountSheet) cho cac phan sau cua gacard.

Anh dau vao (data/acc-card/reference/) la dau ra do chu du an chinh tay, khong phai anh game goc. Moi khoi
(ho so, luoi nhan vat, luoi vu khi, panel cung menh, o nho) nam trong mot khung mau do nguoi chinh ve.
Script nay: (1) 'snap' vung rough trong regions.json vao duong khung that theo mau khung, (2) cat BEN TRONG khung,
(3) tach the bang gacard.grid.split_cards, (4) ghi sheet.json / options.json / _contact.png.

Chay:  .venv\\Scripts\\python.exe scripts\\acc_card\\extract_reference.py [--only 1043,CN1076] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from gacard.grid import detect_cards, split_cards  # noqa: E402

REF_DIR = ROOT / "data" / "acc-card" / "reference"
OUT_DIR = ROOT / "data" / "acc-card" / "fixtures"
REGIONS = Path(__file__).with_name("regions.json")

SNAP_RADIUS = 12      # px quanh canh rough de tim duong khung
INSET_MARGIN = 2      # px bo them sau khi het khung (do vien toi/blur JPEG)
MAX_FRAME_THICK = 9   # do day khung toi da khi di vao ben trong


# --------------------------------------------------------------------------- khung


def frame_mask(img: np.ndarray, hue: tuple[int, int]) -> np.ndarray:
    """Mask (0/1 float) cac pixel mang mau khung. hue theo OpenCV 0..179; lo > hi nghia la vong qua 0 (do)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    h = hsv[..., 0]
    lo, hi = hue
    in_hue = (h >= lo) & (h <= hi) if lo <= hi else (h >= lo) | (h <= hi)
    return (in_hue & (hsv[..., 1] >= 70) & (hsv[..., 2] >= 110)).astype(np.float32)


def snap_rect(like: np.ndarray, rect: list[int]) -> tuple[tuple[int, int, int, int], list[str]]:
    """Snap rect rough vao khung that, tra ve rect BEN TRONG khung (x0,y0,x1,y1 - x1,y1 loai tru)."""
    h, w = like.shape
    x0, y0, x1, y1 = rect
    warns: list[str] = []
    mx0, mx1 = x0 + (x1 - x0) // 5, x1 - (x1 - x0) // 5
    my0, my1 = y0 + (y1 - y0) // 5, y1 - (y1 - y0) // 5

    def col_score(x: int) -> float:
        x = min(max(x, 0), w - 1)
        return float(like[my0:my1, x].mean())

    def row_score(y: int) -> float:
        y = min(max(y, 0), h - 1)
        return float(like[y, mx0:mx1].mean())

    def best(rough: int, score) -> int | None:
        cand = [p for p in range(rough - SNAP_RADIUS, rough + SNAP_RADIUS + 1) if score(p) >= 0.5]
        return min(cand, key=lambda p: abs(p - rough)) if cand else None

    def inward(pos: int, step: int, score) -> int:
        """Tu duong khung di vao trong den khi het khung; tra ve toa do pixel dau tien ben trong."""
        p = pos
        for _ in range(MAX_FRAME_THICK):
            if score(p + step) >= 0.35:
                p += step
            else:
                break
        return p + step

    res: dict[str, int] = {}
    for key, rough, score in (("trai", x0, col_score), ("phai", x1, col_score), ("tren", y0, row_score), ("duoi", y1, row_score)):
        pos = best(rough, score)
        if pos is None:
            warns.append(f"{rect}: khong thay khung canh {key}, dung toa do rough")
            # dat sao cho 'inward' khong di vao: coi rough la khung
            res[key] = rough
        else:
            res[key] = pos
    il = inward(res["trai"], +1, col_score) + INSET_MARGIN - 1
    ir = inward(res["phai"], -1, col_score) - INSET_MARGIN + 1
    it = inward(res["tren"], +1, row_score) + INSET_MARGIN - 1
    ib = inward(res["duoi"], -1, row_score) - INSET_MARGIN + 1
    return (il, it, ir + 1, ib + 1), warns


def snap_item(like: np.ndarray, rect: list[int], margin: int = 16, inset: int = 5) -> tuple[tuple[int, int, int, int], list[str]]:
    """O nho khung khong chu nhat (o dem banner hinh chu L, chan dung bo goc): lay bbox cua cum pixel mau khung lon nhat
    trong vung rough mo rong `margin` px, bo vao `inset` px (do day khung + vien)."""
    h, w = like.shape
    x0, y0, x1, y1 = rect
    xa, ya, xb, yb = max(0, x0 - margin), max(0, y0 - margin), min(w, x1 + margin), min(h, y1 + margin)
    m = (like[ya:yb, xa:xb] > 0).astype(np.uint8)
    m = cv2.dilate(m, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    best, best_area = None, 0
    for i in range(1, n):
        x, y, cw, ch, _ = (int(v) for v in st[i])
        if cw * ch > best_area:
            best, best_area = (x, y, cw, ch), cw * ch
    if best is None or best_area < 0.3 * (x1 - x0) * (y1 - y0):
        return (x0, y0, x1, y1), [f"{rect}: khong thay khung o nho, dung toa do rough"]
    x, y, cw, ch = best
    bx0, by0, bx1, by1 = xa + x, ya + y, xa + x + cw, ya + y + ch
    # cum mau khung co the dinh vao nen cung tong mau: khong cho vuot rect rough qua 10 px
    bx0, by0, bx1, by1 = max(bx0, x0 - 10), max(by0, y0 - 10), min(bx1, x1 + 10), min(by1, y1 + 10)
    return (bx0 + inset, by0 + inset, bx1 - inset, by1 - inset), []


# --------------------------------------------------------------------------- do hiem


def infer_rarity(card: Image.Image, kind: str) -> int | None:
    """Suy do hiem tu mau nen the (le trai/phai, phan giua-tren). None neu khong chac (vd nen do cua nhan vat dac biet)."""
    a = np.asarray(card.convert("RGB"))
    h, w = a.shape[:2]
    ys = slice(int(h * 0.30), int(h * 0.62))
    left = a[ys, max(1, int(w * 0.03)):max(2, int(w * 0.09))]
    right = a[ys, w - max(2, int(w * 0.09)):w - max(1, int(w * 0.03))]
    px = np.concatenate([left, right], axis=1).reshape(-1, 3).astype(np.uint8)[None]
    med = np.median(cv2.cvtColor(px, cv2.COLOR_RGB2HSV)[0], axis=0)
    hue, sat = float(med[0]), float(med[1])
    if sat < 40:
        return 2 if kind == "weapon" else None
    if 6 <= hue <= 26:
        return 5
    if 120 <= hue <= 152:
        return 4
    if 92 <= hue <= 118:
        return 3
    if 40 <= hue <= 85:
        return 2
    return None


# --------------------------------------------------------------------------- contact sheet


def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet(groups: list[tuple[str, list[list[Image.Image]]]], width: int = 1800, cell_h: int = 120) -> Image.Image:
    """Moi nhom (ten, danh sach hang anh): tieu de + cac anh nho kem so thu tu; xuong dong theo hang."""
    font, small = _font(16), _font(12)
    pad = 8
    placements: list[tuple[str, int, list[tuple[Image.Image, int, int]]]] = []
    y = pad
    for name, rows in groups:
        title_y = y
        y += 24
        target = cell_h * (2 if name in ("profile", "panels") else 1)
        placed: list[tuple[Image.Image, int, int]] = []
        for row in rows:
            x, line_h = pad, 0
            for im in row:
                th = min(target, im.height)
                tw = max(1, round(im.width * th / im.height))
                if tw > width - 2 * pad:
                    tw = width - 2 * pad
                    th = max(1, round(im.height * tw / im.width))
                if x + tw > width - pad:
                    x, y, line_h = pad, y + line_h + 18, 0
                placed.append((im.resize((tw, th), Image.LANCZOS), x, y))
                x += tw + 8
                line_h = max(line_h, th)
            y += line_h + 18
        placements.append((name, title_y, placed))
        y += 6
    sheet = Image.new("RGB", (width, y + pad), (34, 34, 38))
    d = ImageDraw.Draw(sheet)
    for name, title_y, placed in placements:
        d.text((pad, title_y), f"{name}  (n={len(placed)})", fill=(255, 220, 120), font=font)
        for k, (im, x, yy) in enumerate(placed, 1):
            sheet.paste(im, (x, yy))
            d.text((x + 2, yy + im.height + 1), f"{k:02d}", fill=(170, 210, 255), font=small)
    return sheet


# --------------------------------------------------------------------------- chinh


def process(file: str, spec: dict, out_root: Path) -> dict:
    pil = Image.open(REF_DIR / file).convert("RGB")
    like = frame_mask(np.asarray(pil), tuple(spec["frame_hue"]))
    code = spec["code"]
    folder = code.replace("#", "")
    out = out_root / folder
    for sub in ("chars", "weapons", "panels", "items", "_debug"):
        (out / sub).mkdir(parents=True, exist_ok=True)
        for old in (out / sub).glob("*.png"):
            old.unlink()
    warns: list[str] = []

    def crop(rect: list[int], name: str, item: bool = False) -> Image.Image:
        inner, w = (snap_item if item else snap_rect)(like, rect)
        warns.extend(f"{name}: {m}" for m in w)
        return pil.crop(inner)

    def rel(p: Path) -> str:
        return p.relative_to(out).as_posix()

    sheet: dict = {"version": 1, "profile": None, "characters": [], "weapons": [], "panels": [], "items": []}

    prof = crop(spec["profile"], "profile")
    prof.save(out / "profile.png")
    sheet["profile"] = {"image": "profile.png", "badge": None}
    groups: list[tuple[str, list[list[Image.Image]]]] = [("profile", [[prof]])]
    counts: dict[str, int] = {}

    for key, sub, kind, skey in (("chars", "chars", "char", "characters"), ("weapons", "weapons", "weapon", "weapons")):
        block = crop(spec[key], key)
        block.save(out / "_debug" / f"{key}_block.png")
        boxes = detect_cards(block, expected_cols=5 if key == "chars" else None)
        cards = split_cards(block, expected_cols=5 if key == "chars" else None)
        assert len(cards) == len(boxes)
        for i, c in enumerate(cards, 1):
            p = out / sub / f"{i:02d}.png"
            c.save(p)
            sheet[skey].append({"image": rel(p), "badge": None, "rarity": infer_rarity(c, kind), "character": None})
        rows: list[list[Image.Image]] = []
        for b, c in zip(boxes, cards):
            if not rows or b.row != last_row:
                rows.append([])
            rows[-1].append(c)
            last_row = b.row
        groups.append((key, rows))
        counts[key] = len(cards)

    panels = []
    for i, rect in enumerate(spec.get("panels", []), 1):
        im = crop(rect, f"panel{i}")
        p = out / "panels" / f"{i:02d}.png"
        im.save(p)
        sheet["panels"].append({"image": rel(p), "badge": None})
        panels.append(im)
    groups.append(("panels", [panels]))
    counts["panels"] = len(panels)

    items = []
    for i, (kind, rect) in enumerate(spec.get("items", []), 1):
        im = crop(rect, f"item{i}-{kind}", item=True)
        p = out / "items" / f"{i:02d}.png"
        im.save(p)
        sheet["items"].append({"image": rel(p), "badge": None, "kind": kind})
        items.append(im)
    groups.append(("items", [items]))
    counts["items"] = len(items)

    with open(out / "sheet.json", "w", encoding="utf-8") as f:
        json.dump(sheet, f, ensure_ascii=False, indent=2)
    options = {"code": code, "uid": spec.get("uid"), "extra_lines": spec.get("extra_lines", []), "family": "auto"}
    with open(out / "options.json", "w", encoding="utf-8") as f:
        json.dump(options, f, ensure_ascii=False, indent=2)
    contact_sheet(groups).save(out / "_contact.png")
    return {"file": file, "dir": folder, "counts": counts, "warns": warns}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="danh sach ma, vd 1043,CN1076")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    with open(REGIONS, encoding="utf-8") as f:
        regions = json.load(f)["samples"]
    only = {s.strip().replace("#", "") for s in args.only.split(",")} if args.only else None
    for file, spec in regions.items():
        if only and spec["code"].replace("#", "") not in only:
            continue
        r = process(file, spec, args.out)
        c = r["counts"]
        print(f"{r['dir']:>8} <- {file:<13} chars={c['chars']:>2} weapons={c['weapons']:>2} panels={c['panels']:>2} items={c['items']}")
        for w in r["warns"]:
            print("   CANH BAO:", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
