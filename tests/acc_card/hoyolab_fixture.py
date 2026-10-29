"""Response HoYoLAB GIẢ, đúng khung/tên trường trong docs/acc-card/04-hoyolab-api.md (không có dữ liệu thật)."""

from __future__ import annotations

import random
from pathlib import Path

from gacard import characters

UPLOAD = "https://upload-os-bbs.mihoyo.com/game_record/genshin"
ELEMENT_NAME = {"pyro": "Pyro", "hydro": "Hydro", "anemo": "Anemo", "electro": "Electro",
                "dendro": "Dendro", "cryo": "Cryo", "geo": "Geo"}
WEAPONS = [  # (id, icon, rarity)
    (11509, "UI_EquipIcon_Sword_Narukami_Awaken", 5), (13509, "UI_EquipIcon_Pole_Narukami_Awaken", 5),
    (15502, "UI_EquipIcon_Bow_Amos_Awaken", 5), (13501, "UI_EquipIcon_Pole_Homa_Awaken", 5),
    (14514, "UI_EquipIcon_Catalyst_Troupe_Awaken", 5), (11512, "UI_EquipIcon_Sword_Regalis_Awaken", 5),
    (11416, "UI_EquipIcon_Sword_Exotic_Awaken", 4), (14416, "UI_EquipIcon_Catalyst_Exotic_Awaken", 4),
    (12416, "UI_EquipIcon_Claymore_Dragonfell_Awaken", 4), (15416, "UI_EquipIcon_Bow_Fossil_Awaken", 4),
    (13416, "UI_EquipIcon_Pole_Ruby_Awaken", 4), (15301, "UI_EquipIcon_Bow_Sling_Awaken", 3),
    (11101, "UI_EquipIcon_Sword_Blunt", 1),
]


def envelope(data, retcode: int = 0, message: str = "OK") -> dict:
    return {"retcode": retcode, "message": message, "data": data}


def pick_characters(n: int, icon_dir: Path | None = None) -> list[characters.Character]:
    """n nhân vật thật trong characters.json (không Nhà Lữ Hành); ưu tiên nhân vật có icon trong icon_dir."""
    pool = [c for c in characters.load_characters() if not c.is_traveler and c.icon]
    if icon_dir is not None and icon_dir.is_dir():
        have = {p.stem for p in icon_dir.glob("UI_AvatarIcon_*.png")}
        pool.sort(key=lambda c: not any(h == c.icon or h.startswith(c.icon + "Costume") for h in have))
    return pool[:n]


def _icon_for(c: characters.Character, icon_dir: Path | None) -> tuple[str, str]:
    icon, side = c.icon, c.side_icon
    if icon_dir is not None and not (icon_dir / f"{icon}.png").is_file():
        for p in sorted(icon_dir.glob(f"{icon}Costume*.png")):
            icon = p.stem
            side = icon.replace("UI_AvatarIcon_", "UI_AvatarIcon_Side_")
            break
    return icon, side


def make_character_list(n: int = 30, *, seed: int = 11, icon_dir: Path | None = None,
                        with_aloy: bool = False) -> dict:
    rng = random.Random(seed)
    chars = pick_characters(n, icon_dir)
    if with_aloy:
        aloy = characters.by_avatar_id(10000062)
        chars = [aloy] + [c for c in chars if c.avatar_id != 10000062][: n - 1]
    items = []
    for i, c in enumerate(chars):
        icon, side = _icon_for(c, icon_dir)
        wid, wicon, wr = WEAPONS[i % len(WEAPONS)]
        items.append({
            "id": c.avatar_id,
            "icon": f"{UPLOAD}/character_icon/{icon}.png",
            "name": c.name_en,
            "element": ELEMENT_NAME.get(c.element or "", "None"),
            "fetter": rng.choice([10, 8, 6]),
            "level": rng.choice([90, 90, 90, 80, 70, 60, 20]),
            "rarity": 105 if c.avatar_id == 10000062 else c.rarity,
            "actived_constellation_num": rng.choice([0, 0, 0, 1, 2, 6]),
            "image": f"{UPLOAD}/character_image/{icon.replace('AvatarIcon', 'Gacha_AvatarImg')}.png",
            "is_chosen": False,
            "side_icon": f"{UPLOAD}/character_side_icon/{side}.png",
            "weapon_type": 1,
            "weapon": {"id": wid, "icon": f"{UPLOAD}/equip/{wicon}.png", "type": 1, "rarity": wr,
                       "level": rng.choice([90, 90, 80, 70]), "affix_level": rng.choice([1, 1, 2, 5]),
                       "name": f"Vũ khí {wid}"},
        })
    return {"list": items}


def make_index(n_chars: int = 30, ar: int = 60) -> dict:
    return {
        "role": {"AvatarUrl": "", "nickname": "Lữ Khách Thử", "region": "os_asia", "level": ar,
                 "game_head_icon": ""},
        "avatars": [],
        "stats": {"active_day_number": 1000, "achievement_number": 900, "avatar_number": n_chars,
                  "way_point_number": 400, "domain_number": 60, "spiral_abyss": "12-3",
                  "precious_chest_number": 1, "luxurious_chest_number": 1, "exquisite_chest_number": 1,
                  "common_chest_number": 1, "magic_chest_number": 1, "anemoculus_number": 66,
                  "geoculus_number": 131, "electroculus_number": 181, "dendroculus_number": 271,
                  "hydroculus_number": 271, "pyroculus_number": 80, "iceculus_number": 0,
                  "moonoculus_number": 0, "full_fetter_avatar_num": 20, "role_combat": {},
                  "hard_challenge": {}},
        "world_explorations": [],
    }
