from pathlib import Path

from gacard.models import AccountSheet, Card, Item, ItemKind, Piece, RenderOptions


def test_sheet_roundtrip_json():
    sheet = AccountSheet(
        profile=Piece(image=Path("profile.png")),
        characters=[Card(image=Path("c/01.png"), rarity=5, badge=2, character="columbina")],
        weapons=[Card(image=Path("w/01.png"))],
        panels=[Piece(image=Path("p/01.png"), badge=3)],
        items=[Item(image=Path("i/01.png"), kind=ItemKind.BANNER_COUNTER)],
    )
    again = AccountSheet.model_validate_json(sheet.model_dump_json())
    assert again == sheet
    assert again.characters[0].character == "columbina"
    assert again.items[0].kind is ItemKind.BANNER_COUNTER


def test_defaults():
    sheet = AccountSheet()
    assert sheet.version == 1 and sheet.profile is None
    assert sheet.characters == [] and sheet.items == []
    opts = RenderOptions()
    assert opts.family == "auto" and opts.height == 1080
    assert opts.code is None and opts.extra_lines == [] and opts.background is None
    assert Item(image=Path("x.png")).kind is ItemKind.OTHER
    card = Card(image=Path("x.png"))
    assert card.badge is None and card.rarity is None and card.character is None


def test_item_kind_values():
    assert {k.value for k in ItemKind} == {
        "resin", "fate", "banner_counter", "account_link", "portrait", "other"}
    assert Item.model_validate({"image": "a.png", "kind": "resin"}).kind is ItemKind.RESIN
