"""Test ô đếm banner: khung mẫu (PNG + JSON), điền N / tên, chọn kiểu, nối vào sheet HoYoLAB và bố cục."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gacard import bannerbox
from gacard.bannerbox import (choose_kind, fallback_template, get_template, load_template, render_banner_box,
                              save_template)
from gacard.hoyolab import UrlIconCache, build_sheet_from_data
from gacard.layout import TEXT_STROKE, LayoutSpec, compute_layout
from gacard.models import ItemKind, RenderOptions
from gacard.render import build_spec, load_pieces, render_image
from gacard.uidmode import IconCache
from gacard.wish import BannerCounter

from .hoyolab_fixture import make_character_list, make_index

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("offline (test)")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def changed_bbox(a: Image.Image, b: Image.Image):
    diff = np.asarray(a.convert("RGBA"), np.int16) - np.asarray(b.convert("RGBA"), np.int16)
    ys, xs = np.nonzero(np.abs(diff).sum(axis=2) > 0)
    return None if len(xs) == 0 else (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def inside(inner, outer) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


# ------------------------------------------------------------------ chọn kiểu, khung tạm
def test_choose_kind():
    assert choose_kind("Skirk", True) == "limited"
    assert choose_kind("Keqing", False) == "lech"
    assert choose_kind(None, None) == "limited"


@pytest.mark.parametrize("kind,extra", [("limited", "ANH"), ("lech", "TEN")])
def test_fallback_templates_have_slots_inside_image(kind, extra):
    t = fallback_template(kind)
    assert t.source == "fallback" and t.own_frame and {"N", extra} <= set(t.slots)
    full = (0, 0, *t.size)
    assert all(inside(s.bbox, full) for s in t.slots.values())
    assert t.image.getpixel((0, 0))[3] == 0            # góc ngoài trong suốt (dáng bậc thang như mẫu)


def test_missing_or_broken_template_falls_back_with_warning(tmp_path):
    t, warn = get_template("lech", tmp_path)
    assert t.source == "fallback" and "lech" in warn and "khung tạm" in warn
    (tmp_path / "lech.json").write_text("{không phải json", encoding="utf-8")
    t, warn = get_template("lech", tmp_path)
    assert t.source == "fallback" and "hỏng" in warn


def test_template_round_trip_and_path_escape_rejected(tmp_path):
    src = fallback_template("lech")
    src.slots["N"].stroke, src.slots["N"].stroke_color = 3, (10, 20, 30)
    save_template(src, tmp_path)
    t, warn = get_template("lech", tmp_path)
    assert warn is None and t.source == "file" and t.size == src.size and t.own_frame
    assert t.slots["N"].stroke == 3 and t.slots["N"].stroke_color == (10, 20, 30)
    assert t.slots["TEN"].bbox == src.slots["TEN"].bbox and t.slots["TEN"].max_lines == 2
    spec = json.loads((tmp_path / "lech.json").read_text(encoding="utf-8"))
    spec["background"] = "../outside.png"
    (tmp_path / "lech.json").write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError):
        load_template("lech", tmp_path)


def test_template_requires_n_slot(tmp_path):
    t = fallback_template("limited")
    del t.slots["N"]
    save_template(t, tmp_path)
    assert get_template("limited", tmp_path)[1] is not None


# ------------------------------------------------------------------ điền chữ
@pytest.mark.parametrize("n", [1, 15, 16])
def test_n_only_touches_n_slot(n):
    t = fallback_template("limited")
    box = render_banner_box(t, n)
    assert box.size == t.size
    bb = changed_bbox(box, t.image)
    assert bb is not None and inside(bb, t.slots["N"].bbox)


@pytest.mark.parametrize("name", ["Keqing", "Yumemizuki Mizuki", "Nhân Vật Có Tên Rất Rất Dài Ở Đây Nè"])
def test_lech_name_two_lines_fits_slot(name):
    t = fallback_template("lech")
    base = render_banner_box(t, 1)
    box = render_banner_box(t, 1, name)
    bb = changed_bbox(box, base)
    assert bb is not None and inside(bb, t.slots["TEN"].bbox)
    # hai dòng: có điểm chữ ở cả nửa trên và nửa dưới của slot
    x0, y0, x1, y1 = t.slots["TEN"].bbox
    mid = (y0 + y1) // 2
    ys = np.nonzero((np.abs(np.asarray(box, np.int16) - np.asarray(base, np.int16)).sum(axis=2) > 0).any(axis=1))[0]
    assert ys.min() < mid < ys.max()


def test_limited_only_fills_n_and_keeps_template_art():
    t = fallback_template("limited")
    box = render_banner_box(t, 3, "Zibai")
    x0, y0, x1, y1 = t.slots["ANH"].bbox
    assert box.crop((x0, y0, x1, y1)).tobytes() == t.image.crop((x0, y0, x1, y1)).tobytes()
    assert changed_bbox(box, render_banner_box(t, 4, "Zibai")) is not None


# ------------------------------------------------------------------ nối vào sheet HoYoLAB
def _sheet(tmp_path, wish, templates=None):
    index, lst = make_index(30), make_character_list(30)
    return build_sheet_from_data(index, lst, tmp_path / "work", UrlIconCache(tmp_path / "h", offline=True),
                                 IconCache(tmp_path / "e", offline=True), None, RenderOptions(uid="800000001"),
                                 wish, ["Lịch sử roll: thử"], banner_templates=templates or tmp_path / "none")


def test_sheet_without_wish_has_no_banner_item(tmp_path):
    sheet, report = _sheet(tmp_path, None)
    assert sheet.items == [] and "Lịch sử roll: thử" in report.text_vi()


def test_sheet_limited_and_lech_items(tmp_path):
    sheet, report = _sheet(tmp_path, BannerCounter(n=15, pity=79, total=100, last5_name="Skirk", limited=True))
    (item,) = sheet.items
    assert item.kind == ItemKind.BANNER_COUNTER and item.own_frame and (tmp_path / "work" / item.image).is_file()
    text = report.text_vi()
    assert "N = 15" in text and "khung limited" in text and "khung tạm" in text
    sheet2, report2 = _sheet(tmp_path / "b", BannerCounter(n=1, pity=3, total=9, last5_name="Keqing", limited=False))
    assert "khung lech" in report2.text_vi() and "Keqing (5 sao)" in report2.text_vi()
    assert sheet2.items[0].kind == ItemKind.BANNER_COUNTER


def test_sheet_uses_owner_template_when_present(tmp_path):
    tdir = tmp_path / "tpl"
    t = fallback_template("limited")
    save_template(t, tdir)
    _, report = _sheet(tmp_path, BannerCounter(n=2, pity=10, total=10, last5_name="Skirk", limited=True), tdir)
    assert "(file)" in report.text_vi() and "khung tạm" not in report.text_vi()


def test_render_draws_no_theme_frame_for_own_frame_item(tmp_path):
    sheet, _ = _sheet(tmp_path, BannerCounter(n=1, pity=0, total=1, last5_name=None, limited=None))
    options = RenderOptions(code="1116", uid="800000001")
    img, layout, _ = render_image(sheet, tmp_path / "work", options)
    item = layout.of("item")[0].rect
    pieces = load_pieces(sheet, tmp_path / "work")
    assert ("item", 0) in pieces.own_frame
    # ô nằm ngay trên lưới vũ khí, góc ngoài trong suốt để lộ nền (không có khung theme chữ nhật bao quanh)
    assert layout.problems(build_spec(sheet, pieces, options, None)) == []
    assert img.size == (1920, 1080) and item.w > 0


# ------------------------------------------------------------------ bố cục wide
def test_wide_strip_sits_left_of_uid_text_on_weapons_left_edge():
    chars, weapons = [(228, 300)] * 45, [(248, 304)] * 27
    spec = LayoutSpec(profile=(874, 180), characters=chars, weapons=weapons, items=[(528, 222)],
                      code="#1116", uid_line="CHECK BUILD UID 887630784", watermark="@sora kiwi")
    layout = compute_layout(spec, "wide")
    assert layout.family == "wide" and layout.problems(spec) == []
    item, uid = layout.of("item")[0].rect, layout.of("uid")[0].rect
    grid = layout.of("weapons")[0].rect
    assert item.x == grid.x and item.bottom <= grid.y - layout.gap          # thẳng mép trái lưới vũ khí, ngay trên nó
    assert uid.x >= item.right + layout.gap and uid.x + TEXT_STROKE > grid.x   # dòng UID bên phải ô, không chồng
    assert uid.gap_to(item) >= layout.gap


# ------------------------------------------------------------------ script chuyển PSD
def _converter():
    spec = importlib.util.spec_from_file_location("banner_template", ROOT / "scripts" / "acc_card" / "banner_template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_slot_from_layer_reads_text_info():
    conv = _converter()
    text = {"runs": [{"font": "HYWenHei-85W", "size": 40, "fill": "#BE6E32"}], "effective_size_px": 52.4,
            "justification": "2"}
    slot, warn = conv.slot_from_layer("TEN", (10, 5, 400, 120), text, [{"type": "Stroke", "size": 3, "color": "#112233"}])
    assert slot["font"] == "display" and slot["size"] == 52 and slot["color"] == "#BE6E32"
    assert slot["align"] == "center" and slot["stroke"] == 3 and slot["stroke_color"] == "#112233"
    assert slot["max_lines"] == 2 and warn == []
    anh, _ = conv.slot_from_layer("ANH", (0, 0, 50, 20))
    assert anh == {"bbox": [0, 0, 50, 20]}
    slot, warn = conv.slot_from_layer("N", (10, 5, 100, 85), None)
    assert slot["size"] == 56 and warn            # không có thông tin chữ → cỡ theo chiều cao + cảnh báo


def test_convert_psd_with_named_layers(tmp_path):
    psd_tools = pytest.importorskip("psd_tools")
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer

    psd = PSDImage.new("RGB", (200, 80), color=(240, 230, 210))
    for name, box, color in (("Nen", (0, 0, 200, 80), (240, 230, 210)), ("Khung", (0, 0, 200, 10), (200, 20, 100)), ("N", (80, 30, 120, 70), (0, 0, 0)),
                             ("TEN", (20, 5, 180, 28), (150, 80, 20)), ("ANH", (20, 30, 70, 70), (10, 90, 200))):
        im = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (*color, 255))
        psd.append(PixelLayer.frompil(im, psd, name=name, top=box[1], left=box[0]))
    path = tmp_path / "lech.psd"
    psd.save(path)
    conv = _converter()
    png, js, warnings = conv.convert(path, tmp_path / "out", "lech")
    t = load_template("lech", tmp_path / "out")
    assert t.size == (200, 80) and set(t.slots) == {"N", "TEN", "ANH"} and t.own_frame
    assert t.slots["N"].bbox == (80, 30, 120, 70) and t.slots["TEN"].bbox == (20, 5, 180, 28)
    assert any("không phải layer chữ" in w for w in warnings)
    # ảnh nền đã bỏ N / TEN / ANH nhưng giữ layer khác
    assert t.image.getpixel((100, 50))[:3] == (240, 230, 210) and t.image.getpixel((5, 5))[:3] == (200, 20, 100)


# ------------------------------------------------------------------ khung của chủ dự án (banner.psd, git-ignored)
OWNER_PSD = ROOT / "data" / "acc-card" / "templates" / "banner" / "banner.psd"


@pytest.mark.skipif(not OWNER_PSD.is_file(), reason="không có banner.psd của chủ dự án (git-ignored)")
def test_owner_psd_finished_layers_convert(tmp_path):
    pytest.importorskip("psd_tools")
    conv = _converter()
    conv.build_finished(OWNER_PSD, tmp_path, ["limited", "lech"], conv.FINISHED_LAYERS)
    lim, warn = get_template("limited", tmp_path)
    lech, warn2 = get_template("lech", tmp_path)
    assert warn is None and warn2 is None and lim.own_frame and lech.own_frame
    assert lim.anh_default == "Skirk" and lech.two_line is not None
    assert lech.two_line.size[1] > lech.size[1] and lech.two_line.size[0] == lech.size[0]
    # chữ mẫu đã bị xoá: thanh số và nhãn không còn nét tối / nét cam
    for t, slot in ((lim, "N"), (lech, "N"), (lech, "TEN")):
        x0, y0, x1, y1 = t.slots[slot].bbox
        arr = np.asarray(t.image.crop((x0, y0, x1, y1)).convert("RGB"), np.int16)
        assert arr.min() > 190, (t.kind, slot)


@pytest.mark.skipif(not OWNER_PSD.is_file(), reason="không có banner.psd của chủ dự án (git-ignored)")
def test_owner_template_lech_picks_two_lines_for_long_names_and_limited_blanks_foreign_art(tmp_path):
    pytest.importorskip("psd_tools")
    conv = _converter()
    conv.build_finished(OWNER_PSD, tmp_path, ["limited", "lech"], conv.FINISHED_LAYERS)
    lech, _ = get_template("lech", tmp_path)
    short = render_banner_box(lech, 1, "Skirk")
    long = render_banner_box(lech, 15, "Yumemizuki Mizuki")
    assert short.size == lech.size and long.size == lech.two_line.size
    lim, _ = get_template("limited", tmp_path)
    own = render_banner_box(lim, 1, "Skirk")
    other = render_banner_box(lim, 1, "Zibai")                    # ảnh của chủ giữ nguyên với mọi nhân vật
    x0, y0, x1, y1 = lim.slots["ANH"].bbox
    assert own.crop((x0, y0, x1, y1)).tobytes() == other.crop((x0, y0, x1, y1)).tobytes()
    assert changed_bbox(own, lim.image) is not None                # chỉ N khác ảnh nền
