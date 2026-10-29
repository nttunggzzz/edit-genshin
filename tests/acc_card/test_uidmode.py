"""Test gacard.uidmode: vẽ thẻ kiểu game, dựng sheet từ JSON Enka (fixture), icon thiếu. Không gọi mạng."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from gacard import uidmode
from gacard.enka import EnkaClient, parse_profile
from gacard.models import AccountSheet, RenderOptions
from gacard.render import load_sheet, render_sheet
from gacard.uidmode import (CHAR_SIZE, PROFILE_SIZE, WEAPON_SIZE, IconCache, build_sheet_from_raw,
                            build_sheet_from_uid, draw_character_card, draw_profile_box, draw_weapon_card,
                            parse_showcase)

FIX = Path(__file__).parent / "fixtures_enka"
UID = "800000001"


def raw_with_weapons() -> dict:
    """detailed.json + vũ khí tổng hợp (fixture gốc không có equipList)."""
    raw = copy.deepcopy(json.loads((FIX / "detailed.json").read_text(encoding="utf-8")))
    specs = [  # (itemId, icon, rank, level, promote, affix)
        (11509, "UI_EquipIcon_Sword_Narukami", 5, 90, 6, 0),
        (11416, "UI_EquipIcon_Sword_Kaboshi", 4, 90, 6, 4),
        (15502, "UI_EquipIcon_Bow_Amos", 5, 80, 5, 1),
        (11101, "UI_EquipIcon_Sword_Blunt", 1, 1, 0, None),
    ]
    for av, (iid, icon, rank, lvl, promote, affix) in zip(raw["avatarInfoList"], specs):
        weapon: dict = {"level": lvl}
        if promote:
            weapon["promoteLevel"] = promote
        if affix is not None:
            weapon["affixMap"] = {str(100000 + iid): affix}
        av["equipList"] = [{"itemId": iid, "weapon": weapon,
                            "flat": {"icon": icon, "rankLevel": rank, "itemType": "ITEM_WEAPON"}}]
    return raw


def fake_icon(color, size=256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (size // 2, size // 2), (*color, 255)), (size // 4, size // 4))
    return img


@pytest.fixture
def icon_dir(tmp_path) -> Path:
    d = tmp_path / "icons"
    d.mkdir()
    for name in ["UI_AvatarIcon_Ayaka", "UI_AvatarIcon_Nilou", "UI_AvatarIcon_Ganyu",
                 "UI_AvatarIcon_Side_Ayaka", "UI_EquipIcon_Sword_Narukami_Awaken", "UI_Buff_Element_Water"]:
        fake_icon((200, 120, 80)).save(d / f"{name}.png")
    return d


# ------------------------------------------------------------------ vẽ
def test_character_card_size_and_corners():
    card = draw_character_card(fake_icon((10, 200, 10)), 5, "pyro", 90)
    assert card.size == CHAR_SIZE and card.mode == "RGBA"
    assert card.getpixel((0, 0))[3] == 0                    # góc bo trong suốt
    assert card.getpixel((CHAR_SIZE[0] // 2, CHAR_SIZE[1] // 2))[3] == 255


def test_character_card_rarity_background_differs():
    five = draw_character_card(None, 5, None, 90).convert("RGB").getpixel((30, 120))
    four = draw_character_card(None, 4, None, 90).convert("RGB").getpixel((30, 120))
    assert five[0] > five[2]           # cam
    assert four[2] > four[1]           # tím


def test_character_card_without_icon_or_level_does_not_crash():
    card = draw_character_card(None, None, "unknown-element", None)
    assert card.size == CHAR_SIZE


def test_weapon_card_size_and_options():
    for ref, owner in [(1, fake_icon((0, 0, 255), 128)), (5, None), (None, None)]:
        card = draw_weapon_card(fake_icon((255, 255, 0)), 4, 90, ref, owner)
        assert card.size == WEAPON_SIZE and card.mode == "RGBA"
    assert draw_weapon_card(None, None, None, None, show_owner=False).size == WEAPON_SIZE


def test_profile_box_unknown_values():
    assert draw_profile_box(None, None).size == PROFILE_SIZE
    nc = Image.new("RGB", (840, 400), (30, 60, 140))
    assert draw_profile_box(59, 9, "Ngày 1 tháng 1", namecard=nc, ar_exp=(10, 100)).size == PROFILE_SIZE


# ------------------------------------------------------------------ icon cache
def test_icon_cache_offline_missing_returns_none(tmp_path):
    cache = IconCache(tmp_path / "icons", offline=True)
    assert cache.get("UI_AvatarIcon_Nope") is None
    assert cache.get(None) is None


@pytest.mark.parametrize("bad", ["../secret", "a/b", "UI_X.exe", "", "C:\\x"])
def test_icon_cache_rejects_odd_names(bad):
    assert IconCache.filename(bad) is None


def test_icon_cache_name_forms():
    assert IconCache.filename("UI_AvatarIcon_Ayaka") == "UI_AvatarIcon_Ayaka.png"
    assert IconCache.filename("/ui/UI_NameCardPic_Furina_P.jpg") == "UI_NameCardPic_Furina_P.jpg"


def test_icon_cache_corrupt_file_returns_none(tmp_path):
    d = tmp_path / "icons"
    d.mkdir()
    (d / "UI_Bad.png").write_bytes(b"not a png")
    assert IconCache(d, offline=True).get("UI_Bad") is None


# ------------------------------------------------------------------ dữ liệu
def test_parse_showcase_weapons():
    raw = raw_with_weapons()
    chars, weapons = parse_showcase(raw, parse_profile(raw, UID))
    assert [c.slug for c in chars] == ["kamisato-ayaka", "nilou", "ganyu", "keqing"]
    assert chars[0].element == "cryo" and chars[0].rarity == 5 and chars[0].icon == "UI_AvatarIcon_Ayaka"
    w = {x.item_id: x for x in weapons}
    assert w[11509].icon == "UI_EquipIcon_Sword_Narukami_Awaken" and w[11509].refinement == 1
    assert w[11416].refinement == 5 and w[11416].rarity == 4
    assert w[11101].icon == "UI_EquipIcon_Sword_Blunt" and w[11101].refinement is None
    assert w[11509].owner_slug == "kamisato-ayaka" and w[11509].owner_side_icon == "UI_AvatarIcon_Side_Ayaka"


def test_parse_showcase_costume_from_store():
    raw = raw_with_weapons()
    raw["avatarInfoList"][0]["costumeId"] = 200201
    store = {"10000002": {"Costumes": {"200201": {"Icon": "/ui/UI_AvatarIcon_AyakaCostumeFruhling.png",
                                                  "SideIcon": "/ui/UI_AvatarIcon_Side_AyakaCostumeFruhling.png"}}}}
    chars, weapons = parse_showcase(raw, parse_profile(raw, UID), store)
    assert chars[0].icon == "UI_AvatarIcon_AyakaCostumeFruhling"
    assert weapons[0].owner_side_icon == "UI_AvatarIcon_Side_AyakaCostumeFruhling"


def test_build_sheet_from_raw(tmp_path, icon_dir):
    raw = raw_with_weapons()
    profile = parse_profile(raw, UID)
    work = tmp_path / "sheet"
    sheet, report = build_sheet_from_raw(raw, profile, work, IconCache(icon_dir, offline=True))
    # sheet.json hợp lệ, đủ file
    loaded, base = load_sheet(work)
    assert loaded == sheet and isinstance(sheet, AccountSheet)
    assert [c.character for c in sheet.characters] == ["kamisato-ayaka", "nilou", "ganyu", "keqing"]
    assert [c.badge for c in sheet.characters] == [0, 1, 0, 1]
    assert [c.rarity for c in sheet.weapons] == [5, 5, 4, 1]          # sắp như túi đồ game
    for piece in [sheet.profile, *sheet.characters, *sheet.weapons]:
        with Image.open(base / piece.image) as im:
            assert im.size in (CHAR_SIZE, WEAPON_SIZE, PROFILE_SIZE)
    # icon thiếu được báo, không làm hỏng
    assert "UI_AvatarIcon_Keqing" in report.missing_icons
    assert "UI_EquipIcon_Bow_Amos_Awaken" in report.missing_icons
    text = report.text_vi()
    assert "AR 60" in text and "Tủ trưng bày" in text and "Keqing Lv70 C1" in text


def test_build_sheet_renders_jpeg(tmp_path, icon_dir):
    raw = raw_with_weapons()
    sheet, _ = build_sheet_from_raw(raw, parse_profile(raw, UID), tmp_path / "s",
                                    IconCache(icon_dir, offline=True))
    data = render_sheet(sheet, tmp_path / "s", RenderOptions(code="1116", uid=UID))
    assert data[:2] == b"\xff\xd8"


def test_build_sheet_empty_showcase(tmp_path):
    raw = json.loads((FIX / "empty_showcase.json").read_text(encoding="utf-8"))
    sheet, report = build_sheet_from_raw(raw, parse_profile(raw, UID), tmp_path / "s",
                                         IconCache(tmp_path / "icons", offline=True))
    assert sheet.characters == [] and sheet.weapons == [] and sheet.profile is not None
    assert "trống" in report.text_vi()


def test_build_sheet_from_uid_offline_uses_cache(tmp_path, icon_dir, monkeypatch):
    monkeypatch.setenv("GACARD_DATA_DIR", str(tmp_path / "data"))
    client = EnkaClient(tmp_path / "data" / "enka-cache", "gacard-test/0.1 (pytest)")
    client._write_cache(UID, raw_with_weapons(), datetime.now(timezone.utc), 60)
    sheet, profile, report = build_sheet_from_uid(UID, tmp_path / "s", client=client,
                                                  icons=IconCache(icon_dir, offline=True), offline=True)
    assert profile.adventure_rank == 60 and len(sheet.characters) == 4 and len(sheet.weapons) == 4
    assert report.world_level == 9


def test_build_sheet_from_uid_offline_without_cache_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("GACARD_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(uidmode.EnkaError):
        build_sheet_from_uid(UID, tmp_path / "s", offline=True)
