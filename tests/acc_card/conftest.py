"""Sheet tổng hợp vẽ bằng code cho test gacard (không có UID thật, không gọi mạng)."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gacard.models import AccountSheet, Card, Item, ItemKind, Piece
from gacard.theme import font

RARITY_BG = {5: (196, 136, 64), 4: (130, 98, 178), 3: (78, 120, 160)}
ITEM_SIZES = {ItemKind.FATE: (100, 120), ItemKind.RESIN: (100, 120), ItemKind.BANNER_COUNTER: (170, 96),
              ItemKind.ACCOUNT_LINK: (240, 90), ItemKind.PORTRAIT: (180, 230), ItemKind.OTHER: (100, 120)}
PANEL_COLORS = [(40, 70, 60), (90, 30, 40), (40, 30, 90), (30, 50, 90)]


def _card(label: str, rarity: int, rng: random.Random, size=(140, 170)) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, RARITY_BG[rarity])
    d = ImageDraw.Draw(img)
    hue = tuple(rng.randint(60, 230) for _ in range(3))
    d.ellipse((w * 0.22, h * 0.12, w * 0.78, h * 0.62), fill=hue)
    d.rectangle((0, h * 0.80, w, h), fill=(236, 229, 216))
    d.text((w / 2, h * 0.90), label, font=font(20, "bold"), fill=(60, 60, 60), anchor="mm")
    return img


def _panel(i: int, lines: int, rng: random.Random) -> Image.Image:
    w, h = 420, 70 + 75 * lines
    img = Image.new("RGB", (w, h), PANEL_COLORS[i % len(PANEL_COLORS)])
    d = ImageDraw.Draw(img)
    for k in range(lines):
        y = 35 + 75 * k
        d.ellipse((20 + 30 * k, y, 80 + 30 * k, y + 60), outline=(230, 220, 170), width=4)
        d.text((100 + 30 * k, y + 30), f"Cung mệnh {i + 1}.{k + 1} Hạng ữ", font=font(22, "bold"),
               fill=(235, 235, 235), anchor="lm")
    d.rectangle((w - 52, h - 52, w - 6, h - 6), fill=tuple(rng.randint(80, 220) for _ in range(3)))
    return img


def _profile() -> Image.Image:
    img = Image.new("RGB", (600, 222), (60, 84, 140))
    d = ImageDraw.Draw(img)
    rows = [("Hạng Mạo Hiểm", "60"), ("EXP Mạo Hiểm", "340125 / 340125"),
            ("Cấp Thế Giới", "9"), ("Sinh Nhật", "Ngày 7 tháng 9")]
    for k, (a, b) in enumerate(rows):
        y = 18 + k * 50
        d.text((20, y), a, font=font(30, "bold"), fill="white")
        d.text((580, y), b, font=font(30, "bold"), fill="white", anchor="ra")
    return img


def _item(kind: ItemKind, i: int) -> Image.Image:
    size = ITEM_SIZES[kind]
    img = Image.new("RGB", size, (200, 150, 80) if kind != ItemKind.ACCOUNT_LINK else (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.text((size[0] / 2, size[1] / 2), f"{kind.value[:6]} {i}", font=font(16, "bold"), fill=(30, 30, 30), anchor="mm")
    return img


def make_sheet(root: Path, n_chars: int = 25, n_weapons: int = 16, n_panels: int = 3,
               items: list[ItemKind] | None = None, profile: bool = True, badges: bool = True,
               seed: int = 7) -> Path:
    """Ghi sheet.json + PNG vào `root`; trả về `root`."""
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)

    def save(img: Image.Image, name: str) -> Path:
        img.save(root / name)
        return Path(name)

    sheet = AccountSheet()
    if profile:
        sheet.profile = Piece(image=save(_profile(), "profile.png"))
    for i in range(n_chars):
        rarity = 5 if i % 3 == 0 else 4
        badge = rng.choice([None, None, 1, 2, 6]) if badges else None
        sheet.characters.append(Card(image=save(_card(f"Lv.{90 - i % 7 * 10}", rarity, rng), f"char_{i:02d}.png"),
                                     rarity=rarity, badge=badge, character=f"char-{i}"))
    for i in range(n_weapons):
        rarity = 5 if i % 2 == 0 else 4
        sheet.weapons.append(Card(image=save(_card("Lv.90", rarity, rng), f"weapon_{i:02d}.png"), rarity=rarity))
    for i in range(n_panels):
        lines = rng.choice([1, 1, 2, 3])
        badge = rng.choice([None, 2, 3]) if badges else None
        sheet.panels.append(Piece(image=save(_panel(i, lines, rng), f"panel_{i:02d}.png"), badge=badge))
    for i, kind in enumerate(items if items is not None else [ItemKind.BANNER_COUNTER, ItemKind.FATE]):
        sheet.items.append(Item(image=save(_item(kind, i), f"item_{i:02d}.png"), kind=kind))
    (root / "sheet.json").write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
    return root


def make_background(side: str = "left", size=(1920, 1080), tint=(40, 140, 120)) -> Image.Image:
    """Nền tổng hợp: chuyển sắc trơn + 'nhân vật' nhiều chi tiết ở bên `side`."""
    w, h = size
    img = Image.new("RGB", size, tint)
    d = ImageDraw.Draw(img)
    for y in range(0, h, 4):
        k = y / h
        d.rectangle((0, y, w, y + 4), fill=tuple(int(c * (1 - 0.5 * k) + 20 * k) for c in tint))
    cx = {"left": 0.2, "center": 0.5, "right": 0.8}[side] * w
    rng = random.Random(3)
    for _ in range(500):
        x = cx + rng.gauss(0, w * 0.06)
        y = rng.uniform(0, h)
        r = rng.uniform(4, 22)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randint(0, 255) for _ in range(3)))
    return img


@pytest.fixture
def sheet_factory(tmp_path):
    counter = iter(range(1000))

    def factory(**kwargs) -> Path:
        return make_sheet(tmp_path / f"sheet{next(counter)}", **kwargs)

    return factory
