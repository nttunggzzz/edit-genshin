"""Tach tung the (nhan vat / vu khi) trong mot khoi luoi.

Gia dinh ve anh game (se chay tren anh game GOC, khong chi anh mau da chinh):

* Moi the co **dai nhan sang mau be/xam nhat o day the** (chua "Lv.90"): mau rat sang, do bao hoa thap
  (vd RGB ~ 234,231,226). Day la tin hieu chinh, KHONG phu thuoc mau nen do hiem (cam/tim/xanh/do) hay so cot.
* Moi the cung kich thuoc (W x H); cac the cach nhau mot khe hep (>= 2 px) co mau nen khoi.
* Day nhan cua cac the o cung hang nam thang hang; the bo co nhieu cot/hang tuy y, hang cuoi co the thieu the.
* Khoi co the con vien/padding 1-3 px cua khung do nguoi chinh ve, nhieu JPEG, thanh loc "Cap" voi vung trong rong
  (nhung vat the nay la mot thanh sang rong gap nhieu lan the -> bi loai theo kich thuoc/ti le).
* The bi cat o mep khoi (mat dinh, mat day) bi bo: chi tra ve the day du (cao >= 92% chieu cao chuan).

Thuat toan:

1. Mask "be" = pixel sang (min kenh >= 185) va it mau (max-min <= 55); dong 3x3 de noi chu "Lv." trong day.
2. Thanh phan lien thong -> ung vien day nhan; loc theo ti le cao/rong, do day dac, kich thuoc.
3. Chon cum rong chiem da so (dung de loai thanh loc/chu trang to); loai ung vien lech chieu cao day.
4. Xep hang (gom theo tam y), cot theo x -> thu tu doc.
5. Tim dinh the: tu day nhan di len den khe (hang pixel le trai/phai giong mau nen ngay ben ngoai the);
   chieu cao chuan H = trung vi cac the quet duoc (the cung kich thuoc).
6. Neu `expected_cols` va so cot phat hien khac nhau: thu lai voi nguong mem hon, giu ket qua gan expected_cols hon
   va ghi canh bao (logging). `expected_cols` chi la goi y/kiem tra, khong ep so cot.

Gioi han da biet: hai day nhan dinh lien nhau (khe < 3 px) se bi gop thanh mot ung vien rong va bi loai; nen phang
mau nen trang/be (vd nen sang) co the sinh ung vien gia; the co nen rat gan mau nen khoi co the quet dinh sai.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger("gacard.grid")

__all__ = ["CardBox", "detect_cards", "split_cards"]

_MAX_SIDE = 1600           # canh dai nhat khi xu ly (anh lon hon duoc thu nho de nhanh)
_MIN_STRIP_W = 24          # px (sau thu nho) - day nhan hep hon la nhieu (chu, bieu tuong)
_FULL_CARD_RATIO = 0.92    # the thap hon ti le nay so voi H chuan = bi cat -> bo


@dataclass(frozen=True)
class CardBox:
    """Hop the trong toa do anh vao (x1, y1 loai tru). `row`, `col` tinh tu 0."""

    x0: int
    y0: int
    x1: int
    y1: int
    row: int
    col: int
    pad_top: int = 0  # so hang pixel the bi cat o mep tren khoi (<= 8% chieu cao), split_cards lap bang cach nhan hang tren cung

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class _Strip:
    """Day nhan cua mot the: cot x..x+w va hang cuoi `yb` (hang beige cuoi cung, bao gom)."""

    x: int
    w: int
    yb: int


# --------------------------------------------------------------------------- phat hien day nhan


def _light_mask(rgb: np.ndarray, min_channel: int, max_chroma: int) -> np.ndarray:
    """Pixel 'be/xam nhat': rat sang va it mau."""
    mn = rgb.min(axis=2)
    mx = rgb.max(axis=2)
    return (mn >= min_channel) & ((mx.astype(np.int16) - mn) <= max_chroma)


def _strip_candidates(rgb: np.ndarray, min_channel: int, max_chroma: int) -> list[_Strip]:
    """Tim DAY DUOI cua cac day nhan: duong ngang dai ma phia tren sang, phia duoi khong sang (khe/nen).

    Dung canh duoi (khong dung ca khoi sang) vi nen the co the co vung trang (toc trang, ao trang) dinh liu vao day nhan.
    """
    h, w = rgb.shape[:2]
    light = _light_mask(rgb, min_channel, max_chroma)
    pad = np.zeros((2, w), dtype=bool)
    below1 = np.vstack([light[1:], pad[:1]])
    below2 = np.vstack([light[2:], pad])
    edge = (light & ~below1 & ~below2).astype(np.uint8)
    edge = cv2.morphologyEx(edge, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1)))
    # chi giu duong ngang >= _MIN_STRIP_W (loai canh tren cua chu "Lv.xx"), roi noi cac hang lech 1-2 px
    edge = cv2.morphologyEx(edge, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (_MIN_STRIP_W, 1)))
    edge = cv2.morphologyEx(edge, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(edge, connectivity=8)

    # cot 'co sang o dau do' trong dai 10 hang phia tren day: dung de mo rong ra mep the that (goc bo tron, chu Lv.)
    out: list[_Strip] = []
    for i in range(1, n):
        x, y, cw, ch, _area = (int(v) for v in stats[i])
        if cw < _MIN_STRIP_W or ch > 7:
            continue
        yb = y + ch - 1
        if x <= 0 and x + cw >= w:
            continue  # dai sang cham ca hai mep khoi: nen sang / thanh loc, khong phai the
        # ket thuc cua duong phai la mep that (nen/khe), khong phai noi chu "Lv." chen vao day sang
        if (x - 3 >= 0 and light[yb, x - 3]) or (x + cw + 2 < w and light[yb, x + cw + 2]):
            continue
        band = light[max(0, yb - 9): max(1, yb - 2)].any(axis=0)
        x0, x1 = x, x + cw
        for _ in range(12):
            if x0 > 0 and band[x0 - 1]:
                x0 -= 1
            else:
                break
        for _ in range(12):
            if x1 < w and band[x1]:
                x1 += 1
            else:
                break
        out.append(_Strip(x0, x1 - x0, yb))
    return out


def _dominant_cluster(strips: list[_Strip]) -> list[_Strip]:
    """Giu cum day nhan cung rong (the that); loai thanh loc / vat the sang to. Hoa thi chon cum hep hon."""
    if not strips:
        return []
    best: list[_Strip] = []
    best_key: tuple[int, float] = (-1, 0.0)
    for ref in sorted({s.w for s in strips}):
        grp = [s for s in strips if abs(s.w - ref) <= 0.10 * ref + 1]
        key = (len(grp), -float(ref))
        if key > best_key:
            best_key, best = key, grp
    return best


# --------------------------------------------------------------------------- quet dinh the


def _median_cols(rgb: np.ndarray, ya: int, yb: int, xa: int, xb: int) -> np.ndarray | None:
    h, w = rgb.shape[:2]
    xa, xb = max(0, xa), min(w, xb)
    if xb <= xa or yb <= ya:
        return None
    return np.median(rgb[ya:yb, xa:xb].astype(np.float32), axis=1)  # (rows, 3)


def _scan_top(rgb: np.ndarray, s: _Strip, max_up: int, thr: float = 22.0) -> tuple[int, bool]:
    """Di len tu dinh day nhan den khe nen. Tra ve (y dinh the, co gap khe that khong)."""
    h, w = rgb.shape[:2]
    yb = s.yb  # loai tru: quet tu hang day nhan len tren
    ya = max(0, yb - max_up)
    if yb <= ya:
        return yb, False
    m = max(1, round(s.w * 0.04))
    mw = max(2, round(s.w * 0.07))
    ml = _median_cols(rgb, ya, yb, s.x + m, s.x + m + mw)
    mr = _median_cols(rgb, ya, yb, s.x + s.w - m - mw, s.x + s.w - m)
    ol = _median_cols(rgb, ya, yb, s.x - 3, s.x - 1)
    orr = _median_cols(rgb, ya, yb, s.x + s.w + 1, s.x + s.w + 3)
    if ml is None or mr is None:
        return yb, False

    def dist(a: np.ndarray | None, b: np.ndarray | None) -> np.ndarray:
        if a is None or b is None:
            return np.full(len(ml), 1e9, dtype=np.float32)
        return np.linalg.norm(a - b, axis=1)

    # hang la "khe" khi le trai VA le phai deu giong mau nen ben ngoai (ben trai hoac ben phai)
    gap_l = np.minimum(dist(ml, ol), dist(ml, orr)) < thr
    gap_r = np.minimum(dist(mr, ol), dist(mr, orr)) < thr
    gap = gap_l & gap_r
    n = len(gap)
    # quet tu sat day nhan len tren: khe that = it nhat 2 hang khe lien tiep
    for i in range(n - 1, 0, -1):
        if gap[i] and gap[i - 1]:
            return ya + i + 1, True
    return ya, False  # khong thay khe: the cat o mep tren (hoac khe < 2 px)


# --------------------------------------------------------------------------- API


def _cluster_1d(vals: list[float], tol: float) -> list[float]:
    """Gom cac gia tri gan nhau (khoang cach lien tiep <= tol); tra ve trung binh moi cum, tang dan."""
    out: list[list[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _complete_axis(centers: list[float], pitch_min: float, lo: float, hi: float, half: float) -> list[float]:
    """Dien cac vi tri thieu o giua (boi nguyen cua buoc luoi) va mo rong ra hai dau trong [lo, hi].

    Cac vi tri 'moi' chua chac co the that: nguoi goi phai kiem tra bang `_occupied`.
    """
    if len(centers) < 2:
        return list(centers)
    diffs = [b - a for a, b in zip(centers, centers[1:])]
    cand = [d for d in diffs if d >= pitch_min]
    if not cand:
        return list(centers)
    pitch = min(cand)
    out = [centers[0]]
    for a, b in zip(centers, centers[1:]):
        k = round((b - a) / pitch)
        if k >= 2 and abs((b - a) - k * pitch) < 0.25 * pitch:
            out.extend(a + pitch * i for i in range(1, k))
        out.append(b)
    for _ in range(40):
        if out[0] - pitch - half >= lo:
            out.insert(0, out[0] - pitch)
        else:
            break
    for _ in range(40):
        if out[-1] + pitch + half <= hi:
            out.append(out[-1] + pitch)
        else:
            break
    return out


def _occupied(rgb: np.ndarray, x0: int, x1: int, y0: int, y1: int, gap: float, thr: float = 30.0) -> bool:
    """O luoi co the that khong? So mau trong o voi mau nen ngay trong khe canh o (nen khoi)."""
    h, w = rgb.shape[:2]
    if x0 < 0 or x1 > w or y0 < 0 or y1 > h or y1 - y0 < 8 or x1 - x0 < 8:
        return False
    half = max(1, int(round(gap / 2)))
    refs = []
    for xs in (slice(max(0, x0 - half - 1), max(0, x0 - half + 1)), slice(min(w, x1 + half - 1), min(w, x1 + half + 1))):
        if xs.stop > xs.start:
            refs.append(np.median(rgb[y0:y1, xs].reshape(-1, 3).astype(np.float32), axis=0))
    if not refs:
        return True
    # vung the tru dai nhan: nen the / hinh nhan vat
    inner = rgb[y0:y1, x0 + 1:x1 - 1].astype(np.float32)
    d = np.min([np.linalg.norm(inner - r, axis=2) for r in refs], axis=0)
    return float((d > thr).mean()) >= 0.6


def _detect(rgb: np.ndarray, min_channel: int, max_chroma: int) -> tuple[list[CardBox], int]:
    """Phat hien tren anh da thu nho. Tra ve (cac hop, so cot toi da/hang)."""
    h, w = rgb.shape[:2]
    strips = _dominant_cluster(_strip_candidates(rgb, min_channel, max_chroma))
    if not strips:
        return [], 0
    wm = sorted(s.w for s in strips)[len(strips) // 2]
    max_up = int(wm * 2.0)

    # chieu cao the chuan: trung vi cac chieu cao quet duoc den khe (the cung kich thuoc)
    heights_gap: list[int] = []
    heights_any: list[int] = []
    for s in strips:
        top, found_gap = _scan_top(rgb, s, max_up)
        hh = s.yb + 1 - top
        heights_any.append(hh)
        if found_gap:
            heights_gap.append(hh)
    if heights_gap:
        pool = sorted(heights_gap)
        big_h = pool[len(pool) // 2]
    else:
        big_h = max(heights_any)  # khong khe nao (hang cat o tren): lay chieu cao lon nhat

    # truc luoi tu cac day nhan that: cot theo tam x, hang theo yb
    col_c = _cluster_1d([s.x + s.w / 2 for s in strips], 0.35 * wm)
    row_y = _cluster_1d([float(s.yb) for s in strips], max(4.0, 0.12 * wm))
    col_diffs = [b - a for a, b in zip(col_c, col_c[1:])]
    gap_px = max(2.0, min([d for d in col_diffs if d >= 0.8 * wm] or [wm * 1.1]) - wm)
    cols = _complete_axis(col_c, 0.8 * wm, wm / 2, w - wm / 2, 0)
    rows = _complete_axis(row_y, 0.8 * big_h, big_h, h, 0)

    boxes: list[CardBox] = []
    ncols_used: set[int] = set()
    per_row: dict[int, int] = {}
    for ri, yb in enumerate(rows):
        yb_i = int(round(yb))
        for ci, cx in enumerate(cols):
            x0 = int(round(cx - wm / 2))
            real = any(abs(s.x + s.w / 2 - cx) <= 0.35 * wm and abs(s.yb - yb) <= max(4.0, 0.12 * wm) for s in strips)
            y0 = yb_i + 1 - big_h
            if y0 < 0 and (yb_i + 1) < _FULL_CARD_RATIO * big_h:
                continue  # the bi cat o mep tren khoi
            pad_top = max(0, -y0)
            y0 = max(0, y0)
            if not real and not _occupied(rgb, x0, x0 + wm, y0, yb_i + 1, gap_px):
                continue
            boxes.append(CardBox(max(0, x0), y0, min(w, x0 + wm), min(h, yb_i + 1), ri, ci, pad_top))
            ncols_used.add(ci)
            per_row[ri] = per_row.get(ri, 0) + 1
    # danh lai chi so hang/cot chi tinh cac hang/cot co the
    row_ids = {r: i for i, r in enumerate(sorted({b.row for b in boxes}))}
    col_ids = {c: i for i, c in enumerate(sorted({b.col for b in boxes}))}
    boxes = [CardBox(b.x0, b.y0, b.x1, b.y1, row_ids[b.row], col_ids[b.col], b.pad_top) for b in boxes]
    return boxes, len(col_ids)


def detect_cards(block: Image.Image, *, expected_cols: int | None = None) -> list[CardBox]:
    """Nhu `split_cards` nhung chi tra hop (toa do trong `block`), tien cho debug/ve len anh."""
    rgb0 = np.asarray(block.convert("RGB"))
    h0, w0 = rgb0.shape[:2]
    if h0 < 16 or w0 < 16:
        return []
    scale = min(1.0, _MAX_SIDE / max(h0, w0))
    rgb = rgb0 if scale == 1.0 else cv2.resize(rgb0, (round(w0 * scale), round(h0 * scale)), interpolation=cv2.INTER_AREA)

    boxes, ncols = _detect(rgb, 185, 55)
    if expected_cols is not None and ncols != expected_cols:
        alt, alt_cols = _detect(rgb, 165, 75)
        if abs(alt_cols - expected_cols) < abs(ncols - expected_cols):
            boxes, ncols = alt, alt_cols
        if ncols != expected_cols:
            log.warning("split_cards: phat hien %d cot, ky vong %d (giu ket qua tot nhat)", ncols, expected_cols)
    if scale != 1.0:
        inv = 1.0 / scale
        boxes = [
            CardBox(round(b.x0 * inv), round(b.y0 * inv), min(w0, round(b.x1 * inv)), min(h0, round(b.y1 * inv)), b.row, b.col, round(b.pad_top * inv))
            for b in boxes
        ]
    return boxes


def split_cards(block: Image.Image, *, expected_cols: int | None = None) -> list[Image.Image]:
    """Tach cac the trong mot khoi luoi, thu tu doc (hang -> cot), bo o trong. Xem docstring module de biet gia dinh.

    Tra ve danh sach anh RGB (moi the cat tu `block`, giu nguyen kich thuoc goc). Khoi khong co the nao -> [].
    """
    boxes = detect_cards(block, expected_cols=expected_cols)
    rgb = block.convert("RGB")
    out: list[Image.Image] = []
    for b in boxes:
        card = rgb.crop(b.box)
        if b.pad_top:
            arr = np.pad(np.asarray(card), ((b.pad_top, 0), (0, 0), (0, 0)), mode="edge")
            card = Image.fromarray(arr)
        out.append(card)
    return out
