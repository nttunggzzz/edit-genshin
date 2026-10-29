"""Test gacard.characters: bảng nhân vật, tra cứu mờ, hype."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gacard import characters as ch

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "acc-card"


def test_table_integrity():
    chars = ch.load_characters()
    assert len(chars) >= 100
    slugs = [c.slug for c in chars]
    ids = [c.avatar_id for c in chars]
    assert len(set(slugs)) == len(slugs) and len(set(ids)) == len(ids)
    assert all(s == s.lower() and s.isascii() and " " not in s for s in slugs)
    assert all(c.rarity in (4, 5) for c in chars)


def test_by_avatar_id_and_display_name():
    assert ch.by_avatar_id(10000125).slug == "columbina"
    assert ch.by_avatar_id(10000126).slug == "zibai"
    assert ch.by_avatar_id(10000046).slug == "hu-tao"
    assert ch.by_avatar_id(1) is None
    assert ch.display_name("lumine") == "Nhà Lữ Hành (Lumine)"
    assert ch.display_name("khong-co") == "khong-co"


def test_travelers_flagged():
    assert ch.by_avatar_id(10000005).is_traveler and ch.by_avatar_id(10000007).is_traveler
    assert not ch.by_avatar_id(10000002).is_traveler


@pytest.mark.parametrize(
    "query,slug",
    [
        ("columbina", "columbina"),
        ("  COLUMBINA ", "columbina"),
        ("Zibai", "zibai"),
        ("raiden", "raiden-shogun"),
        ("Raiden Shogun", "raiden-shogun"),
        ("shogun", "raiden-shogun"),
        ("kazuha", "kaedehara-kazuha"),
        ("hutao", "hu-tao"),
        ("Hu Tao", "hu-tao"),
        ("hu-tao", "hu-tao"),
        ("ayaka", "kamisato-ayaka"),
        ("Ayato", "kamisato-ayato"),
        ("kẻ lang thang", "wanderer"),   # tên Việt có dấu
        ("ke lang thang", "wanderer"),   # không dấu
        ("Kẻ Lang Thang", "wanderer"),
        ("Nhà Lữ Hành (Lumine)", "lumine"),
        ("furna", "furina"),             # gõ sai
        ("zibay", "zibai"),
        ("colombina", "columbina"),
        ("keqin", "keqing"),
        ("neuvilette", "neuvillette"),
        ("zhong li", "zhongli"),
        ("childe", "tartaglia"),
        ("kuki", "kuki-shinobu"),
    ],
)
def test_resolve_ok(query, slug):
    assert ch.resolve_character(query) == slug


@pytest.mark.parametrize("query", ["", "   ", "xyz", "kamisato", "traveler", "nhà lữ hành", "a", "qwertyuiop", "!!!"])
def test_resolve_none_when_unknown_or_ambiguous(query):
    assert ch.resolve_character(query) is None


def test_normalize_strips_diacritics():
    assert ch.normalize("Đặng  Kẻ-Lang_Thang") == "dang ke lang thang"


# ------------------------------------------------------------------- hype
def test_default_hype_file():
    data = json.loads((ASSETS / "hype.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["note_vi"] and data["hot"][:2] == ["columbina", "zibai"]
    known = {c.slug for c in ch.load_characters()}
    assert set(data["hot"]) <= known and set(data["scores"]) <= known
    assert all(0 <= v <= 100 for v in data["scores"].values())


def test_hot_overrides_scores_in_order():
    h = ch.load_hype()
    assert h["columbina"] == 100 and h["zibai"] == 99
    assert h["aether"] == 0 and h["lumine"] == 0
    # 5★ giới hạn mới > 5★ thường trú > 4★
    assert h["odette"] > h["diluc"] > h["kaeya"]


def test_top_hype_ordering():
    assert ch.top_hype(["kaeya", "zibai", "columbina", "diluc"], 3) == ["columbina", "zibai", "diluc"]
    assert ch.top_hype(["zibai", "columbina"]) == ["columbina"]
    assert ch.top_hype([], 2) == []
    assert ch.top_hype(["x", "y"], 5) == ["x", "y"]  # slug lạ = 0, giữ thứ tự đầu vào
    assert ch.top_hype(["kaeya", "kaeya"], 5) == ["kaeya"]


def test_hype_custom_file(tmp_path):
    p = tmp_path / "hype.json"
    p.write_text(json.dumps({"version": 1, "hot": ["b"], "scores": {"a": 90, "b": 10, "c": 50}}), encoding="utf-8")
    h = ch.load_hype(p)
    assert h == {"a": 90, "b": 100, "c": 50}
    assert ch.top_hype(["a", "b", "c"], 2, hype=h) == ["b", "a"]
