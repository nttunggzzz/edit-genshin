import io
import json
import random
import time

import pytest
from PIL import Image

from gacard.backgrounds import BackgroundError, BackgroundLibrary
from gacard.cli import main
from gacard.models import ItemKind, RenderOptions
from gacard.render import SheetError, format_code, load_options, load_sheet, render_image, render_sheet

from .conftest import make_background


def _bg_bytes(side="left", tint=(200, 40, 50)) -> bytes:
    buf = io.BytesIO()
    make_background(side, tint=tint).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_load_sheet_and_options(sheet_factory):
    d = sheet_factory(n_chars=5, n_weapons=3, n_panels=1)
    sheet, base = load_sheet(d)
    assert base == d and len(sheet.characters) == 5
    assert load_sheet(d / "sheet.json")[1] == d
    assert load_options(d) == RenderOptions()
    (d / "options.json").write_text(json.dumps({"code": "1043", "uid": "1"}), encoding="utf-8")
    assert load_options(d).code == "1043"
    with pytest.raises(SheetError):
        load_sheet(d / "missing")


def test_format_code():
    assert format_code("1043") == "#1043"
    assert format_code("CN#1076") == "CN#1076"
    assert format_code("  ") is None and format_code(None) is None


@pytest.mark.parametrize("kw", [
    dict(n_chars=30, n_weapons=16, n_panels=11, items=[ItemKind.FATE, ItemKind.BANNER_COUNTER,
                                                       ItemKind.RESIN, ItemKind.OTHER]),
    dict(n_chars=8, n_weapons=16, n_panels=0, items=[ItemKind.ACCOUNT_LINK]),
    dict(n_chars=40, n_weapons=48, n_panels=14, items=[ItemKind.PORTRAIT, ItemKind.RESIN]),
    dict(n_chars=0, n_weapons=0, n_panels=0, items=[], profile=False),
])
def test_render_sheet_jpeg(sheet_factory, kw):
    sheet, base = load_sheet(sheet_factory(**kw))
    opts = RenderOptions(code="1043", uid="850363095", extra_lines=["DIE ID APPLE"], watermark="@hoavu")
    t0 = time.perf_counter()
    data = render_sheet(sheet, base, opts)
    assert time.perf_counter() - t0 < 3.0
    assert len(data) <= 2_000_000
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG" and img.size == (1920, 1080)


def test_render_720(sheet_factory):
    sheet, base = load_sheet(sheet_factory(n_chars=10, n_weapons=8))
    data = render_sheet(sheet, base, RenderOptions(height=720))
    assert Image.open(io.BytesIO(data)).size == (1280, 720)


def test_layout_has_no_problems(sheet_factory):
    sheet, base = load_sheet(sheet_factory(n_chars=25, n_weapons=16, n_panels=3))
    _, layout, _ = render_image(sheet, base, RenderOptions(code="1", uid="2", watermark="@w"))
    assert layout.problems() == []
    assert len(layout.of("character")) == 25


def test_items_sorted_into_strip_order(sheet_factory):
    d = sheet_factory(n_chars=5, n_weapons=8, n_panels=0,
                      items=[ItemKind.ACCOUNT_LINK, ItemKind.FATE, ItemKind.PORTRAIT, ItemKind.RESIN])
    sheet, base = load_sheet(d)
    from gacard.render import sorted_items
    assert [i.kind for i in sorted_items(sheet)] == [ItemKind.PORTRAIT, ItemKind.FATE,
                                                    ItemKind.RESIN, ItemKind.ACCOUNT_LINK]


def test_background_auto_pick_random_in_account_and_mark_used(sheet_factory, tmp_path):
    lib = BackgroundLibrary(tmp_path / "bgs")
    other = lib.add(_bg_bytes(), "not-in-acc")
    mine = lib.add(_bg_bytes("right"), "char-3")
    sheet, base = load_sheet(sheet_factory(n_chars=6, n_weapons=8))
    img, layout, used = render_image(sheet, base, RenderOptions(), lib, rng=random.Random(0))
    assert used.id == mine.id                     # chỉ nền của nhân vật có trong acc
    assert layout.family == "left"                # nhân vật trên nền đứng bên phải
    render_sheet(sheet, base, RenderOptions(), lib, rng=random.Random(0))
    assert lib.get(mine.id).used_count == 1 and lib.get(other.id).used_count == 0
    # `characters`: toàn bộ nhân vật của acc (vd HoYoLAB) thay cho nhân vật trên thẻ
    _, _, used = render_image(sheet, base, RenderOptions(), lib, characters=["not-in-acc"], rng=random.Random(0))
    assert used.id == other.id


def test_background_explicit_and_unknown(sheet_factory, tmp_path):
    lib = BackgroundLibrary(tmp_path / "bgs")
    a = lib.add(_bg_bytes(), "zibai")
    sheet, base = load_sheet(sheet_factory(n_chars=3, n_weapons=3))
    _, _, used = render_image(sheet, base, RenderOptions(background=a.id), lib)
    assert used.id == a.id
    _, _, used = render_image(sheet, base, RenderOptions(background="zibai"), lib)
    assert used.id == a.id
    with pytest.raises(BackgroundError):
        render_image(sheet, base, RenderOptions(background="nobody"), lib)
    _, _, used = render_image(sheet, base, RenderOptions(), lib)       # không thẻ nào khớp → nền mặc định
    assert used is None


def test_missing_piece_raises(sheet_factory):
    d = sheet_factory(n_chars=3, n_weapons=3)
    (d / "char_01.png").unlink()
    sheet, base = load_sheet(d)
    with pytest.raises(SheetError):
        render_sheet(sheet, base, RenderOptions())


def test_piece_outside_sheet_dir_rejected(sheet_factory):
    d = sheet_factory(n_chars=1, n_weapons=0, n_panels=0, items=[])
    data = json.loads((d / "sheet.json").read_text(encoding="utf-8"))
    data["characters"][0]["image"] = "../../secret.png"
    (d / "sheet.json").write_text(json.dumps(data), encoding="utf-8")
    sheet, base = load_sheet(d)
    with pytest.raises(SheetError):
        render_sheet(sheet, base, RenderOptions())


def test_cli_render_and_check(sheet_factory, tmp_path, capsys):
    d = sheet_factory(n_chars=10, n_weapons=8, n_panels=2)
    bg = tmp_path / "bg.jpg"
    make_background("left").save(bg)
    out = tmp_path / "out" / "x.jpg"
    code = main(["render", str(d), "--code", "CN#1076", "--uid", "123", "--extra", "DIE ID APPLE",
                 "--bg-file", str(bg), "--family", "right", "--frame-color", "#FF66AA",
                 "--watermark", "@hoavu", "--height", "720", "--bg-root", str(tmp_path / "none"),
                 "--out", str(out)])
    assert code == 0
    assert Image.open(out).size == (1280, 720)
    assert "Đã lưu ảnh" in capsys.readouterr().out
    assert main(["sheet-check", str(d)]) == 0
    assert "Sheet hợp lệ" in capsys.readouterr().out
    (d / "weapon_00.png").unlink()
    assert main(["sheet-check", str(d)]) == 1
    assert "Thiếu file" in capsys.readouterr().out
    assert main(["render", str(tmp_path / "nope"), "--out", str(out)]) == 1
    assert "Lỗi" in capsys.readouterr().err
