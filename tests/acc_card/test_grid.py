"""Test gacard.grid.split_cards: luoi tong hop ve bang code + (neu co) anh mau that. Khong goi mang."""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from gacard.grid import detect_cards, split_cards

ROOT = Path(__file__).resolve().parents[2]
REF_DIR = ROOT / "data" / "acc-card" / "reference"

RARITY = {5: (176, 112, 52), 4: (120, 96, 160), 3: (70, 110, 160), 0: (150, 50, 55)}
BEIGE = (234, 231, 226)


def _card(w: int, h: int, rarity: int, seed: int, white_art: bool = False) -> Image.Image:
    """The tong hop: nen do hiem + hinh 'nhan vat' + day nhan be o duoi co chu + goc bo."""
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", (w, h), RARITY[rarity])
    d = ImageDraw.Draw(im)
    strip_h = max(10, int(h * 0.2))
    # nen do hiem co gradient nhe
    arr = np.asarray(im).astype(np.int16)
    arr += np.linspace(-14, 14, h, dtype=np.int16)[:, None, None]
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    # 'nhan vat': elip toi (hoac trang nhu toc trang) o giua, co the cham sat day nhan
    col = (245, 245, 245) if white_art else tuple(int(v) for v in rng.integers(20, 90, 3))
    d.ellipse([w * 0.22, h * 0.10, w * 0.78, h - strip_h + 2], fill=col)
    d.rectangle([w * 0.30, h * 0.55, w * 0.70, h - strip_h], fill=col)
    # icon goc tren
    d.ellipse([3, 3, 15, 15], fill=(60, 200, 230))
    d.polygon([(w - 14, 4), (w - 4, 4), (w - 9, 15)], fill=(250, 200, 30))
    # day nhan be + chu
    d.rectangle([0, h - strip_h, w, h], fill=BEIGE)
    d.text((w * 0.28, h - strip_h + strip_h * 0.15), f"Lv.{int(rng.integers(1, 91))}", fill=(70, 70, 70))
    # goc duoi bo tron: tra pixel goc ve mau nen (se duoc de len nen khoi nho mask)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=4, fill=255)
    out = Image.new("RGB", (w, h), (0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def make_grid(
    cols: int,
    rows: int,
    n: int | None = None,
    w: int = 90,
    h: int = 112,
    gap: int = 8,
    pad: int = 6,
    bg=(52, 40, 34),
    filter_bar: bool = False,
    extra_bottom: int = 0,
    jpeg_q: int | None = None,
    noise: float = 0.0,
    white_art: bool = False,
    overlay_digits: bool = False,
    seed: int = 1,
):
    """Tra ve (anh khoi, [hop that (x0,y0,x1,y1)] theo thu tu doc)."""
    n = cols * rows if n is None else n
    W = pad * 2 + cols * w + (cols - 1) * gap
    H = pad * 2 + rows * h + (rows - 1) * gap + extra_bottom
    rng = np.random.default_rng(seed)
    base = np.full((H, W, 3), bg, dtype=np.int16)
    base += rng.integers(-3, 4, size=(H, W, 1), dtype=np.int16)
    im = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    boxes = []
    for i in range(n):
        r, c = divmod(i, cols)
        x, y = pad + c * (w + gap), pad + r * (h + gap)
        card = _card(w, h, [5, 4, 3, 0][i % 4], seed + i, white_art=white_art and i % 3 == 0)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=4, fill=255)
        im.paste(card, (x, y), mask)
        boxes.append((x, y, x + w, y + h))
    d = ImageDraw.Draw(im)
    if overlay_digits:  # so cung menh to che mot phan day nhan cua vai the
        for i in range(0, n, 3):
            x0, y0, x1, y1 = boxes[i]
            d.text((x1 - 28, y1 - 30), "6", fill=(255, 255, 255), font=None)
            d.rectangle([x1 - 26, y1 - 30, x1 - 4, y1 - 4], fill=(40, 170, 230))
    if filter_bar:
        by = H - 34
        d.rounded_rectangle([pad, by, W - pad, by + 24], radius=12, fill=(232, 230, 226))
        d.text((pad + 20, by + 6), "Cap", fill=(60, 60, 60))
    if noise:
        arr = np.asarray(im).astype(np.float32) + rng.normal(0, noise, (H, W, 3))
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if jpeg_q:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=jpeg_q)
        im = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return im, boxes


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua


def _check(im, truth, *, expected_cols=None, min_iou=0.85):
    got = detect_cards(im, expected_cols=expected_cols)
    assert len(got) == len(truth)
    for g, t in zip(got, truth):
        assert _iou(g.box, t) >= min_iou, (g.box, t)
    cards = split_cards(im, expected_cols=expected_cols)
    assert len(cards) == len(truth)
    return got


@pytest.mark.parametrize(
    "cols,rows,w,h,gap",
    [(5, 6, 90, 112, 8), (5, 1, 90, 112, 8), (8, 2, 100, 124, 6), (8, 4, 70, 88, 5), (16, 3, 52, 66, 4), (5, 3, 160, 200, 12), (3, 3, 80, 100, 4)],
)
def test_clean_grids(cols, rows, w, h, gap):
    im, truth = make_grid(cols, rows, w=w, h=h, gap=gap)
    got = _check(im, truth, expected_cols=cols)
    assert max(g.col for g in got) + 1 == cols
    assert max(g.row for g in got) + 1 == rows


@pytest.mark.parametrize("n", [1, 2, 4, 7, 11, 13])
def test_partial_last_row(n):
    im, truth = make_grid(5, 3, n=n)
    # khoi co chieu cao du 3 hang (vung trong o duoi la nen)
    _check(im, truth, expected_cols=5 if n >= 5 else None)


@pytest.mark.parametrize("q,noise", [(70, 0.0), (45, 3.0), (85, 6.0)])
def test_jpeg_and_noise(q, noise):
    im, truth = make_grid(5, 5, jpeg_q=q, noise=noise)
    _check(im, truth, expected_cols=5)


def test_frame_remnant_and_padding():
    im, truth = make_grid(5, 4, pad=1)  # chi con 1 px padding (vien khung con sot)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, im.width - 1, im.height - 1], outline=(70, 195, 225))
    _check(im, truth, expected_cols=5)


def test_white_art_touching_strip():
    im, truth = make_grid(5, 5, white_art=True)
    _check(im, truth, expected_cols=5)


def test_covered_strips_are_recovered():
    """So cung menh to de len day nhan -> van phai thay du the (hoan thien luoi)."""
    im, truth = make_grid(5, 5, overlay_digits=True)
    _check(im, truth, expected_cols=5, min_iou=0.8)


def test_filter_bar_and_empty_area():
    """Giong #1056: it the, vung trong lon va thanh loc sang rong o duoi: chi tra ve the that."""
    im, truth = make_grid(5, 2, n=8, filter_bar=True, extra_bottom=300)
    _check(im, truth, expected_cols=5)


def test_empty_and_tiny_blocks():
    assert split_cards(Image.new("RGB", (400, 400), (50, 40, 30))) == []
    assert split_cards(Image.new("RGB", (8, 8), (255, 255, 255))) == []
    assert detect_cards(Image.new("RGB", (300, 300), (240, 238, 234))) == []  # toan sang, khong co khe


def test_card_pixels_come_from_block():
    im, truth = make_grid(5, 2)
    cards = split_cards(im)
    for card, t in zip(cards, truth):
        ref = im.crop(t)
        assert abs(card.width - ref.width) <= 3 and abs(card.height - ref.height) <= 4
        a = np.asarray(card.resize(ref.size)).astype(int)
        b = np.asarray(ref).astype(int)
        assert np.abs(a - b).mean() < 12


def test_expected_cols_mismatch_warns_not_raises(caplog):
    im, truth = make_grid(4, 3)
    with caplog.at_level(logging.WARNING, logger="gacard.grid"):
        cards = split_cards(im, expected_cols=5)
    assert len(cards) == len(truth)  # khong ep so cot
    assert any("ky vong 5" in r.message for r in caplog.records)


def test_speed():
    im, _ = make_grid(5, 6, jpeg_q=70, noise=2.0)
    split_cards(im)  # khoi dong
    t = time.perf_counter()
    for _ in range(5):
        split_cards(im, expected_cols=5)
    avg = (time.perf_counter() - t) / 5
    assert avg < 0.25, f"cham: {avg * 1000:.0f} ms/khoi"


def test_big_block_is_downscaled_but_boxes_in_original_coords():
    im, truth = make_grid(8, 6, w=260, h=330, gap=14, pad=20)  # ~2.3k px rong
    got = _check(im, truth, expected_cols=8, min_iou=0.9)
    assert got[-1].x1 <= im.width and got[-1].y1 <= im.height


# --------------------------------------------------------------------------- anh mau that (bo qua neu thieu)

EXPECTED_REF = {  # file -> (nhan vat, vu khi); dem bang mat tren anh mau
    "mau-1043.jpg": (30, 16),
    "ref-07.webp": (25, 16),
    "ref-09.webp": (40, 24),
    "ref-16.jpg": (35, 32),
    "ref-17.jpg": (25, 16),
    "ref-20.jpg": (30, 24),
    "ref-21.jpg": (25, 16),
    "ref-04.webp": (8, 16),
}


@pytest.mark.skipif(not (REF_DIR / "mau-1043.jpg").exists(), reason="khong co data/acc-card/reference")
@pytest.mark.parametrize("file", sorted(EXPECTED_REF))
def test_real_reference_blocks(file):
    spec_path = ROOT / "scripts" / "acc_card" / "regions.json"
    s = importlib.util.spec_from_file_location("extract_reference", ROOT / "scripts" / "acc_card" / "extract_reference.py")
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))["samples"][file]
    pil = Image.open(REF_DIR / file).convert("RGB")
    like = mod.frame_mask(np.asarray(pil), tuple(spec["frame_hue"]))
    n_chars, n_weapons = EXPECTED_REF[file]
    for key, expect, cols in (("chars", n_chars, 5), ("weapons", n_weapons, None)):
        inner, _ = mod.snap_rect(like, spec[key])
        block = pil.crop(inner)
        cards = split_cards(block, expected_cols=cols)
        assert len(cards) == expect, f"{file} {key}: {len(cards)} != {expect}"
        sizes = {c.size for c in cards}
        assert max(w for w, _ in sizes) - min(w for w, _ in sizes) <= 2
        assert max(h for _, h in sizes) - min(h for _, h in sizes) <= 2
