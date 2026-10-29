from __future__ import annotations

import random

from datetime import datetime

import pytest

from gacard.bot.services import (
    FixtureCropper,
    apply_constellations,
    build_bg_plan,
    characters_in_account,
    enka_constellations,
    find_default_fixture,
    format_enka_summary,
    normalize_code,
    normalize_uid,
    output_filename,
    parse_caption_options,
    parse_cm_args,
)

from .test_bot_support import FakeCard, FakeChars, FakeSheet, FakeStore, make_profile

CH = FakeChars()


def test_parse_caption_options_full_and_partial():
    o = parse_caption_options("ma 1043 uid 850363095 nen columbina")
    assert (o.code, o.uid, o.nen, o.errors) == ("1043", "850363095", "columbina", [])
    assert not o.only_nen
    o = parse_caption_options("nen hu tao")
    assert o.nen == "hu tao" and o.only_nen
    o = parse_caption_options("/themnen Columbina")
    assert o.nen == "Columbina" and o.only_nen
    o = parse_caption_options("ma CN#1076 uid 850363095")
    assert o.code == "CN#1076"
    assert parse_caption_options(None).nen is None
    assert parse_caption_options("hello world").errors == []


def test_parse_caption_errors():
    o = parse_caption_options("uid 12 ma nen")
    assert len(o.errors) == 3
    assert o.uid is None and o.code is None


def test_parse_cm_args_variants():
    pairs, errs = parse_cm_args("columbina 2 zibai 0", CH.resolve_character)
    assert pairs == {"columbina": 2, "zibai": 0} and errs == []
    pairs, errs = parse_cm_args("Hu Tao 1, Furina 6", CH.resolve_character)
    assert pairs == {"hutao": 1, "furina": 6} and errs == []
    pairs, errs = parse_cm_args("columbina 7 xxx 2 3 zibai", CH.resolve_character)
    assert pairs == {}
    assert len(errs) == 4  # 7>6, xxx lạ, số đứng một mình, zibai thiếu số
    assert parse_cm_args(None, CH.resolve_character) == ({}, [])


def test_normalizers():
    assert normalize_uid(" 850363095 ") == "850363095" and normalize_uid("12345678") is None
    assert normalize_uid("1850363095") == "1850363095"
    assert normalize_code("CN#1076") == "CN#1076" and normalize_code("") is None and normalize_code("a b") is None


def test_output_filename_has_no_uid_and_is_safe():
    when = datetime(2026, 10, 21, 9, 7)
    assert output_filename("1043", when) == "acc-1043-20261021-0907.jpg"
    assert output_filename("CN#1076", when) == "acc-CN1076-20261021-0907.jpg"
    assert output_filename(None, when) == "acc-20261021-0907.jpg"
    assert "/" not in output_filename("../x", when)


def test_apply_constellations_precedence():
    sheet = FakeSheet([FakeCard("columbina"), FakeCard("zibai"), FakeCard("hutao", badge=3), FakeCard(None), FakeCard("nahida")])
    profile = make_profile(avatars=[("columbina", 1), ("zibai", None), ("nahida", 0)])
    rep = apply_constellations(sheet, profile, {"columbina": 4, "furina": 2})
    assert [c.badge for c in sheet.characters] == [4, None, 3, None, 0]
    assert rep.from_manual == {"columbina": 4}
    assert rep.from_enka == {"nahida": 0}  # C0 vẫn là số hợp lệ
    assert rep.missing == ["zibai"]  # hutao giữ badge sẵn có nên không thiếu
    assert rep.unmatched_manual == ["furina"]


def test_apply_without_profile():
    sheet = FakeSheet([FakeCard("zibai")])
    rep = apply_constellations(sheet, None, {})
    assert rep.missing == ["zibai"] and sheet.characters[0].badge is None


def test_characters_in_account_union_ordered():
    sheet = FakeSheet([FakeCard("hutao"), FakeCard("columbina"), FakeCard(None)])
    profile = make_profile(avatars=[("columbina", 1), (None, None), ("zibai", None)])
    assert characters_in_account(profile, sheet) == ["columbina", "zibai", "hutao"]
    assert characters_in_account(None, sheet) == ["hutao", "columbina"]
    assert enka_constellations(profile) == {"columbina": 1}


def test_build_bg_plan_random_in_account_and_override():
    store = FakeStore()
    col1 = store.seed("columbina", used_count=3)
    col2 = store.seed("columbina", used_count=0)
    zib = store.seed("zibai")
    nah = store.seed("nahida")  # không có trong acc → không bao giờ được chọn tự động
    picked = set()
    for seed in range(40):
        plan = build_bg_plan(store, CH, ["columbina", "zibai"], rng=random.Random(seed))
        assert len(plan.choices) == 1 and plan.notes == []
        picked.add(plan.choices[0].background.id)
        assert plan.choices[0].reason.endswith("chọn ngẫu nhiên trong các nhân vật có trong acc")
    assert picked == {col1.id, col2.id, zib.id}

    plan = build_bg_plan(store, CH, ["columbina", "zibai"], override_slug="nahida", rng=random.Random(1))
    assert plan.choices[0].background.id == nah.id and plan.choices[0].reason.endswith("bạn chọn bằng nen")

    plan = build_bg_plan(store, CH, ["zibai"], override_slug="furina", rng=random.Random(1))
    assert plan.choices[0].background.id == zib.id and "Chưa có nền nào của Furina" in plan.notes[0]

    plan = build_bg_plan(FakeStore(), CH, ["zibai"], rng=random.Random(1))
    assert [c.background for c in plan.choices] == [None]  # chỉ có nền mặc định
    assert plan.choices[0].reason.startswith("Nền mặc định")


def test_format_enka_summary_sections():
    text = format_enka_summary(make_profile(avatars=[("columbina", 2), ("zibai", None), (None, 1)]), CH)
    assert "Columbina C2" in text and "Zibai" in text and "chưa có trong bảng nhân vật" in text
    assert "KHÔNG đặt trong Tủ trưng bày" in text
    assert "trống" in format_enka_summary(make_profile(avatars=[]), CH)


def test_fixture_cropper_copies_sheet_and_ignores_images(tmp_path):
    fx = tmp_path / "fx" / "1043"
    fx.mkdir(parents=True)
    (fx / "sheet.json").write_text("{}", encoding="utf-8")
    (fx / "a.png").write_bytes(b"png")
    work = tmp_path / "work"
    work.mkdir()
    (work / "stale.txt").write_text("x")
    loaded = []
    c = FixtureCropper(fx, loader=lambda d: loaded.append(d) or "SHEET")
    assert c.build_sheet([tmp_path / "ignored.jpg"], work) == "SHEET"
    assert (work / "a.png").read_bytes() == b"png" and not (work / "stale.txt").exists()
    assert loaded == [work] and not c.needs_images


def test_fixture_cropper_missing_fixture_has_vietnamese_error(tmp_path):
    with pytest.raises(Exception) as ei:
        FixtureCropper(tmp_path / "nope").build_sheet([], tmp_path / "w")
    assert "fixtures" in ei.value.message_vi


def test_find_default_fixture(tmp_path):
    assert find_default_fixture(tmp_path / "none") is None
    for n in ("b", "a"):
        (tmp_path / n).mkdir()
        (tmp_path / n / "sheet.json").write_text("{}")
    (tmp_path / "c").mkdir()
    assert find_default_fixture(tmp_path).name == "a"
    assert find_default_fixture(tmp_path, "b").name == "b"
    assert find_default_fixture(tmp_path, "c") is None
