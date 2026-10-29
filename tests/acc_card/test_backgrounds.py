import io
import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from gacard.backgrounds import BackgroundError, BackgroundLibrary, estimate_subject_side

from .conftest import make_background


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


@pytest.fixture(scope="module")
def bg_bytes():
    return {side: _jpeg(make_background(side)) for side in ("left", "center", "right")}


def test_add_rejects_small_image(tmp_path):
    lib = BackgroundLibrary(tmp_path)
    with pytest.raises(BackgroundError) as err:
        lib.add(_jpeg(Image.new("RGB", (1280, 720))), "columbina")
    assert "1920" in err.value.message_vi
    assert lib.list() == []


def test_add_rejects_non_image_and_bad_slug(tmp_path, bg_bytes):
    lib = BackgroundLibrary(tmp_path)
    with pytest.raises(BackgroundError):
        lib.add(b"not an image", "columbina")
    with pytest.raises(BackgroundError):
        lib.add(bg_bytes["left"], "../evil")
    with pytest.raises(BackgroundError):
        lib.add(bg_bytes["left"], "")


def test_add_stores_jpeg_and_index(tmp_path, bg_bytes):
    lib = BackgroundLibrary(tmp_path)
    png = io.BytesIO()
    make_background("right").convert("RGBA").save(png, "PNG")
    bg = lib.add(png.getvalue(), "Columbina", source="telegram")
    assert bg.character == "columbina" and bg.width == 1920 and bg.height == 1080
    path = tmp_path / bg.file
    assert path.parent.name == "columbina" and path.suffix == ".jpg"
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.mode == "RGB"
    data = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert data["backgrounds"][0]["id"] == bg.id
    assert not list(tmp_path.glob(".index-*"))          # không để lại file tạm


def test_index_persists_across_instances(tmp_path, bg_bytes):
    a = BackgroundLibrary(tmp_path).add(bg_bytes["left"], "zibai")
    lib = BackgroundLibrary(tmp_path)
    assert [b.id for b in lib.list()] == [a.id]
    assert lib.get(a.id).character == "zibai"
    assert lib.list("zibai")[0].id == a.id and lib.list("other") == []
    lib.set_subject_side(a.id, "right")
    lib.mark_used(a.id)
    again = BackgroundLibrary(tmp_path).get(a.id)
    assert again.subject_side == "right" and again.used_count == 1 and again.last_used_at is not None
    img = lib.open_image(again)
    assert img.size == (1920, 1080)
    lib.remove(a.id)
    assert BackgroundLibrary(tmp_path).list() == []
    assert not (tmp_path / a.file).exists()
    with pytest.raises(BackgroundError):
        lib.get(a.id)
    with pytest.raises(BackgroundError):
        lib.remove(a.id)


def test_set_subject_side_validates(tmp_path, bg_bytes):
    lib = BackgroundLibrary(tmp_path)
    bg = lib.add(bg_bytes["center"], "nahida")
    with pytest.raises(BackgroundError):
        lib.set_subject_side(bg.id, "top")


def test_pick_random_only_among_account_characters(tmp_path, bg_bytes):
    """Luật chủ dự án: random.choice trong các nền có nhân vật thuộc acc; hype không còn ảnh hưởng."""
    lib = BackgroundLibrary(tmp_path)
    amber = lib.add(bg_bytes["left"], "amber")
    col = lib.add(bg_bytes["left"], "columbina")
    col2 = lib.add(bg_bytes["right"], "columbina")
    lib.add(bg_bytes["left"], "zibai")                                  # zibai không có trong acc
    seen = {lib.pick(["amber", "columbina"], random.Random(seed)).id for seed in range(40)}
    assert seen == {amber.id, col.id, col2.id}                          # chỉ nền của nhân vật trong acc, đủ cả
    assert lib.pick(["xiao", "hutao"], random.Random(1)) is None
    assert lib.pick([], random.Random(1)) is None


def test_pick_is_seedable_and_exclude_avoids_current(tmp_path, bg_bytes):
    lib = BackgroundLibrary(tmp_path)
    a = lib.add(bg_bytes["left"], "furina")
    b = lib.add(bg_bytes["right"], "furina")
    first = [lib.pick(["furina"], random.Random(7)).id for _ in range(3)]
    assert len(set(first)) == 1                                         # cùng seed → cùng kết quả
    assert lib.pick(["furina"], random.Random(3), exclude=[a.id]).id == b.id
    assert lib.pick(["furina"], random.Random(3), exclude=[b.id]).id == a.id
    assert lib.pick(["furina"], random.Random(3), exclude=[a.id, b.id]) is None
    assert {x.id for x in lib.matching(["furina", "nobody"])} == {a.id, b.id}


@pytest.mark.parametrize("side", ["left", "center", "right"])
def test_estimate_subject_side(side):
    assert estimate_subject_side(make_background(side)) == side
