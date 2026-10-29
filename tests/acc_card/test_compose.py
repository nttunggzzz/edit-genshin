import io

import numpy as np
import pytest
from PIL import Image

from gacard.compose import CARD_BADGE, PieceImages, compose, cover_fit, default_background
from gacard.export import to_jpeg
from gacard.layout import BLOCK_KINDS, FRAME_WIDTH, LayoutSpec, compute_layout
from gacard.theme import (FONT_DIR, PALETTE, Theme, _luminance, font, gradient_pair, mix, parse_hex,
                          pick_theme, theme_from_color)

from .conftest import make_background

CARD = (140, 170)


def _pieces(n_chars=10, n_weapons=8, badge=None):
    card = Image.new("RGB", CARD, (196, 136, 64))
    p = PieceImages(profile=Image.new("RGB", (600, 222), (60, 84, 140)),
                    characters=[card] * n_chars, weapons=[card] * n_weapons)
    if badge is not None:
        p.badges[("character", 0)] = badge
    return p


def _layout(pieces, **kw):
    spec = LayoutSpec(profile=pieces.profile.size, characters=[CARD] * len(pieces.characters),
                      weapons=[CARD] * len(pieces.weapons), **kw)
    return compute_layout(spec)


def test_compose_size_and_default_background():
    pieces = _pieces()
    layout = _layout(pieces, code="#1043", uid_line="CHECK BUILD UID 1", watermark="@hoavu")
    img = compose(layout, pieces, theme_from_color(PALETTE["cyan"]))
    assert img.size == (1920, 1080) and img.mode == "RGB"
    corner = np.asarray(img.crop((900, 0, 960, 20)), dtype=np.float32).mean()
    assert corner < 80          # nền mặc định tối


def test_frame_outside_block_with_vertical_gradient():
    pieces = _pieces()
    layout = _layout(pieces)
    theme = theme_from_color(PALETTE["red"])
    img = compose(layout, pieces, theme)
    grid = layout.of("characters")[0].rect
    near = lambda a, b, tol=40: max(abs(x - y) for x, y in zip(a, b)) < tol
    top = img.getpixel((grid.x - 2, grid.y + 1))
    bottom = img.getpixel((grid.x - 2, grid.bottom - 2))
    assert near(top, theme.frame_top) and near(bottom, theme.frame_bottom)
    assert not near(top, bottom)                         # gradient thật, không phải một màu
    inside = img.getpixel((grid.x + 1, grid.y + grid.h // 2))
    assert not near(inside, theme.frame_bottom, 60)      # viền không đè vào trong khối


def test_no_drop_shadow_outside_frames():
    pieces = _pieces()
    layout = _layout(pieces)
    theme = theme_from_color(PALETTE["cyan"])
    img = np.asarray(compose(layout, pieces, theme), np.int16)
    bg = np.asarray(default_background((1920, 1080), theme), np.int16)
    for b in layout.boxes:
        if b.kind in BLOCK_KINDS:
            r = b.rect
            x, y = r.x + r.w // 2, r.bottom + FRAME_WIDTH + 3      # ngay dưới viền
            if y < 1080:
                assert np.abs(img[y, x] - bg[y, x]).max() <= 2


def _white_bbox(img, r):
    region = np.asarray(img.crop((r.x - 8, r.y - 8, r.right + 8, r.bottom + 8)))
    ys, xs = np.nonzero((region > 240).all(axis=2))
    return xs.min() + r.x - 8, ys.min() + r.y - 8, xs.max() + r.x - 7, ys.max() + r.y - 7


def test_badge_bottom_right_white():
    pieces = _pieces(badge=6)
    layout = _layout(pieces)
    img = compose(layout, pieces, theme_from_color(PALETTE["cyan"]))
    cell = next(b.rect for b in layout.of("character") if b.index == 0)
    region = np.asarray(img.crop((cell.x + cell.w // 2, cell.y + cell.h // 2, cell.right, cell.bottom)))
    white = ((region > 235).all(axis=2)).mean()
    assert white > 0.05
    plain = compose(layout, _pieces(), theme_from_color(PALETTE["cyan"]))
    region2 = np.asarray(plain.crop((cell.x + cell.w // 2, cell.y + cell.h // 2, cell.right, cell.bottom)))
    assert ((region2 > 235).all(axis=2)).mean() < 0.01


def test_card_badge_size_and_position():
    pieces = _pieces(badge=6)
    layout = _layout(pieces)
    img = compose(layout, pieces, theme_from_color(PALETTE["cyan"]))
    cell = next(b.rect for b in layout.of("character") if b.index == 0)
    x0, y0, x1, y1 = _white_bbox(img, cell)
    assert abs((y1 - y0) - CARD_BADGE * cell.h) <= 0.08 * CARD_BADGE * cell.h   # ≈ 33 % cao thẻ
    assert abs(x1 - cell.right) <= 4 and abs(y1 - cell.bottom) <= 4   # glyph sát góc, chỉ viền tràn


def test_panel_badge_size_and_position():
    theme = theme_from_color(PALETTE["cyan"])
    panel = Image.new("RGB", (420, 145), (40, 30, 90))
    spec = LayoutSpec(panels=[(420, 145)])
    layout = compute_layout(spec)
    r = layout.of("panel")[0].rect
    img = compose(layout, PieceImages(panels=[panel], badges={("panel", 0): 3}), theme)
    x0, y0, x1, y1 = _white_bbox(img, r)
    target = min(r.w * 0.145, r.h * 0.5)
    assert abs((y1 - y0) - target) <= 0.08 * target
    assert abs((x0 + x1) / 2 - (r.x + r.w / 2)) <= 3
    assert (y0 + y1) / 2 < r.y + r.h / 2                       # hơi cao hơn tâm


def test_cover_fit_fills_without_distortion():
    bg = Image.new("RGB", (3000, 1000), (10, 20, 30))
    out = cover_fit(bg, (1920, 1080), 0.3)
    assert out.size == (1920, 1080)


@pytest.mark.parametrize("tint,expected", [((200, 30, 40), "red"), ((40, 160, 70), "green"),
                                           ((130, 60, 200), "purple")])
def test_theme_follows_background(tint, expected):
    bg = Image.new("RGB", (320, 180), tint)
    frame = pick_theme(bg, "auto").frame
    hue = lambda c: Image.new("RGB", (1, 1), c).convert("HSV").getpixel((0, 0))[0]
    assert abs(hue(frame) - hue(PALETTE[expected])) <= 3


def test_theme_override_and_default(monkeypatch):
    monkeypatch.delenv("GACARD_FRAME_COLOR", raising=False)
    assert pick_theme(make_background(), "#FF0000").frame == (255, 0, 0)
    assert pick_theme(None).frame == PALETTE["cyan"]
    assert pick_theme(None, "auto").frame == PALETTE["cyan"]
    gray = Image.new("RGB", (64, 36), (128, 128, 128))
    assert pick_theme(gray, "auto").frame == PALETTE["cyan"]
    t = pick_theme(Image.new("RGB", (320, 180), (200, 30, 40)))   # nền đỏ, không "auto" → vẫn cyan
    assert (t.frame_top, t.frame_bottom) == ((0x68, 0xE0, 0xCF), (0x20, 0x9C, 0xFF))


def test_theme_env_default(monkeypatch):
    red = Image.new("RGB", (320, 180), (200, 30, 40))
    monkeypatch.setenv("GACARD_FRAME_COLOR", "#00FF00")
    assert pick_theme(red).frame == (0, 255, 0)
    assert pick_theme(red, "#FF0000").frame == (255, 0, 0)      # tham số thắng biến môi trường
    monkeypatch.setenv("GACARD_FRAME_COLOR", "auto")
    frame = pick_theme(red).frame                                # đoán theo nền đỏ
    assert frame != PALETTE["cyan"] and frame[0] > max(frame[1], frame[2])
    t = theme_from_color(PALETTE["cyan"])
    assert t.outline == mix(t.frame_top, t.frame_bottom)      # viền chữ = màu giữa gradient, không làm tối
    with pytest.raises(ValueError):
        parse_hex("#12")


def test_bundled_font_has_vietnamese_glyphs():
    assert (FONT_DIR / "BeVietnamPro-ExtraBold.ttf").is_file()
    assert (FONT_DIR / "OFL.txt").is_file()
    f = font(40)
    tofu = bytes(f.getmask(""))          # vùng riêng: không có glyph → ô vuông
    for ch in "ạẠếỂữỮỵỴẳẲđĐơƠưƯ":
        mask = f.getmask(ch)
        assert mask.getbbox() is not None and bytes(mask) != tofu, ch


def test_jpeg_export_limits():
    rng = np.random.default_rng(0)
    noise = Image.fromarray(rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8))
    data = to_jpeg(noise)                       # nhiễu thuần: có thể vượt cả q80, chỉ cần đã giảm
    q90 = io.BytesIO()
    noise.save(q90, "JPEG", quality=90, subsampling="4:2:0", optimize=True)
    assert len(data) <= len(q90.getvalue())
    img = Image.open(io.BytesIO(data))
    assert img.size == (1920, 1080) and img.mode == "RGB"
    assert not img.getexif()


def test_jpeg_steps_down_quality_until_fits():
    rng = np.random.default_rng(1)
    img = Image.fromarray(rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)).resize((1920, 1080))
    sizes = {}
    for q in (90, 85, 80):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, subsampling="4:2:0", optimize=True)
        sizes[q] = len(buf.getvalue())
    limit = (sizes[85] + sizes[90]) // 2
    assert len(to_jpeg(img, max_bytes=limit)) == sizes[85]


def test_jpeg_720():
    img = Image.new("RGB", (1920, 1080), (50, 60, 70))
    out = Image.open(io.BytesIO(to_jpeg(img, 720)))
    assert out.size == (1280, 720)
    with pytest.raises(ValueError):
        to_jpeg(img, 900)


def test_zero_badge_not_drawn():
    base = _pieces()
    layout = _layout(base)
    theme = theme_from_color(PALETTE["cyan"])
    plain = np.asarray(compose(layout, base, theme))
    zero = _pieces(badge=0)
    zero.panels = []
    assert np.array_equal(np.asarray(compose(layout, zero, theme)), plain)


def test_zero_panel_badge_not_drawn():
    theme = theme_from_color(PALETTE["cyan"])
    panel = Image.new("RGB", (420, 145), (40, 30, 90))
    spec = LayoutSpec(panels=[(420, 145)])
    layout = compute_layout(spec)
    plain = PieceImages(panels=[panel])
    zero = PieceImages(panels=[panel], badges={("panel", 0): 0})
    three = PieceImages(panels=[panel], badges={("panel", 0): 3})
    a, b, c = (np.asarray(compose(layout, p, theme)) for p in (plain, zero, three))
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_gradient_pairs_from_psd_and_derived():
    assert gradient_pair(PALETTE["cyan"]) == (parse_hex("#68E0CF"), parse_hex("#209CFF"))
    assert gradient_pair(PALETTE["pink"]) == (parse_hex("#EF0A6A"), parse_hex("#B6359C"))
    for color in [*PALETTE.values(), (255, 0, 0), (250, 200, 40), (40, 200, 90)]:
        top, bottom = gradient_pair(color)
        assert _luminance(top) > _luminance(bottom)            # đỉnh sáng, đáy đậm
    t = Theme(frame=(10, 120, 200), outline=(0, 0, 0), fill=(0, 0, 0, 0), badge=(0, 0, 0))
    assert (t.frame_top, t.frame_bottom) == gradient_pair((10, 120, 200))   # Theme cũ vẫn dựng được
