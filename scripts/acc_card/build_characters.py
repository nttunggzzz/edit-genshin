"""Sinh assets/acc-card/characters.json (và hype.json mặc định) từ dữ liệu store công khai của Enka.Network.

Nguồn: https://github.com/EnkaNetwork/API-docs/tree/master/store/gi  (avatars.json + locs.json)

Cách chạy (từ thư mục gốc repo):
    .venv\\Scripts\\python.exe scripts/acc_card/build_characters.py              # dùng bản đã tải nếu có
    .venv\\Scripts\\python.exe scripts/acc_card/build_characters.py --refresh    # tải lại store mới nhất
    .venv\\Scripts\\python.exe scripts/acc_card/build_characters.py --write-hype # tạo hype.json nếu chưa có
    .venv\\Scripts\\python.exe scripts/acc_card/build_characters.py --write-hype --force-hype  # GHI ĐÈ hype.json

Bản tải về nằm ở data/acc-card/enka-store/ (git-ignored) để dựng lại offline.
Slug ổn định: nếu avatar_id đã có trong characters.json cũ thì giữ nguyên slug cũ.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = ROOT / "data" / "acc-card" / "enka-store"
ASSETS_DIR = ROOT / "assets" / "acc-card"
OUT_CHARACTERS = ASSETS_DIR / "characters.json"
OUT_HYPE = ASSETS_DIR / "hype.json"

BASE_URL = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/gi/"
STORE_FILES = ("avatars.json", "locs.json")
USER_AGENT = "gacard-build-characters/0.1 (personal Telegram showcase bot)"

ELEMENTS = {
    "Ice": "cryo",
    "Wind": "anemo",
    "Electric": "electro",
    "Water": "hydro",
    "Fire": "pyro",
    "Rock": "geo",
    "Grass": "dendro",
}
WEAPONS = {
    "WEAPON_SWORD_ONE_HAND": "sword",
    "WEAPON_CLAYMORE": "claymore",
    "WEAPON_POLE": "polearm",
    "WEAPON_BOW": "bow",
    "WEAPON_CATALYST": "catalyst",
}
# Nhân vật 5★ thường trú (không phải 5★ giới hạn). Chủ dự án có thể sửa trong hype.json.
STANDARD_5 = {"diluc", "jean", "mona", "qiqi", "keqing", "tighnari", "dehya"}
# Người chơi (Traveler): Enka dùng avatarId 10000005 (nam) / 10000007 (nữ) kèm skillDepotId.
TRAVELER_SLUGS = {10000005: ("aether", "Aether"), 10000007: ("lumine", "Lumine")}
MANNEQUIN_IDS = {10000117: "mannequin-boy", 10000118: "mannequin-girl"}
HOT_DEFAULT = ["columbina", "zibai"]
LANG_VI, LANG_EN = "vi", "en"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def download(refresh: bool) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    for name in STORE_FILES:
        dest = STORE_DIR / name
        if dest.exists() and not refresh:
            continue
        print(f"Tải {BASE_URL}{name} ...")
        req = urllib.request.Request(BASE_URL + name, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - URL cố định, HTTPS
            data = resp.read()
        json.loads(data)  # kiểm tra là JSON hợp lệ trước khi ghi
        dest.write_bytes(data)


def build(existing: dict[int, str]) -> list[dict]:
    avatars = json.loads((STORE_DIR / "avatars.json").read_text(encoding="utf-8"))
    locs = json.loads((STORE_DIR / "locs.json").read_text(encoding="utf-8"))
    en, vi = locs[LANG_EN], locs.get(LANG_VI, {})

    rows: dict[int, dict] = {}
    for key, av in avatars.items():
        avatar_id = int(key.split("-")[0])
        if avatar_id in rows:  # các biến thể "10000005-501"... chỉ là skillDepot của cùng một id
            continue
        h = str(av["NameTextMapHash"])
        name_en = en.get(h) or key
        name_vi = vi.get(h) or name_en
        quality = av.get("QualityType", "")
        traveler = avatar_id in TRAVELER_SLUGS
        mannequin = avatar_id in MANNEQUIN_IDS
        if traveler:
            slug, tag = TRAVELER_SLUGS[avatar_id]
            name_en = f"Traveler ({tag})"
            name_vi = f"Nhà Lữ Hành ({tag})"
        elif mannequin:
            slug = MANNEQUIN_IDS[avatar_id]
        else:
            slug = slugify(name_en)
        slug = existing.get(avatar_id, slug)
        side = (av.get("SideIconName") or "").rsplit("/", 1)[-1].removesuffix(".png")
        rows[avatar_id] = {
            "avatar_id": avatar_id,
            "slug": slug,
            "name_en": name_en,
            "name_vi": name_vi,
            "rarity": 4 if quality == "QUALITY_PURPLE" else 5,
            "element": ELEMENTS.get(av.get("Element", "")),
            "weapon_type": WEAPONS.get(av.get("WeaponType", "")),
            "icon": side.replace("_Side_", "_") if side else None,
            "side_icon": side or None,
            "is_traveler": traveler,
            "kind": "traveler" if traveler else "mannequin" if mannequin else "character",
        }
    out = sorted(rows.values(), key=lambda r: r["avatar_id"])
    slugs = [r["slug"] for r in out]
    dup = {s for s in slugs if slugs.count(s) > 1}
    if dup:
        raise SystemExit(f"Slug trùng: {sorted(dup)}")
    return out


def default_hype(chars: list[dict]) -> dict:
    """Điểm mặc định: 5★ giới hạn theo độ mới (id lớn = mới) 60-95; 5★ thường trú 40; 4★ 10-30; Traveler 0."""
    limited = [
        c
        for c in chars
        if c["kind"] == "character" and c["rarity"] == 5 and c["slug"] not in STANDARD_5 and c["slug"] != "aloy"
    ]
    four = [c for c in chars if c["kind"] == "character" and c["rarity"] == 4]

    def spread(items: list[dict], lo: int, hi: int) -> dict[str, int]:
        n = len(items)
        return {c["slug"]: round(lo + (hi - lo) * i / max(n - 1, 1)) for i, c in enumerate(items)}

    scores: dict[str, int] = {}
    scores.update(spread(limited, 60, 95))
    scores.update(spread(four, 10, 30))
    for c in chars:
        if c["kind"] != "character":
            scores[c["slug"]] = 0
        elif c["slug"] in STANDARD_5:
            scores[c["slug"]] = 40
        elif c["slug"] == "aloy":
            scores[c["slug"]] = 30
    known = {c["slug"] for c in chars}
    return {
        "version": 1,
        "note_vi": (
            "Độ săn đón của nhân vật (0-100), dùng để chọn ảnh nền: bot lấy nhân vật CÓ TRONG ACC và CÓ NỀN trong kho "
            "với điểm cao nhất. 'hot' là danh sách bạn ưu tiên: đứng đầu danh sách = 100, rồi 99, 98... và luôn thắng 'scores'. "
            "'scores' là điểm gốc: 5★ giới hạn mới ra điểm cao (60-95), 5★ thường trú 40, 4★ 10-30, Traveler 0. "
            "Đây chỉ là điểm khởi đầu - bạn cứ sửa tay (hoặc dùng lệnh /uutien của bot). Khóa là slug trong characters.json."
        ),
        "hot": [s for s in HOT_DEFAULT if s in known],
        "scores": dict(sorted(scores.items())),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="tải lại store từ GitHub")
    ap.add_argument("--write-hype", action="store_true", help="tạo hype.json mặc định nếu chưa có")
    ap.add_argument("--force-hype", action="store_true", help="với --write-hype: ghi đè cả khi đã có")
    args = ap.parse_args()

    download(args.refresh)
    existing: dict[int, str] = {}
    if OUT_CHARACTERS.exists():
        old = json.loads(OUT_CHARACTERS.read_text(encoding="utf-8"))
        existing = {c["avatar_id"]: c["slug"] for c in old["characters"]}
    chars = build(existing)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "source": BASE_URL, "characters": chars}
    OUT_CHARACTERS.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Đã ghi {len(chars)} nhân vật -> {OUT_CHARACTERS}")
    by_slug = {c["slug"] for c in chars}
    for s in HOT_DEFAULT:
        print(f"  {s}: {'có' if s in by_slug else 'KHÔNG có'}")

    if args.write_hype:
        if OUT_HYPE.exists() and not args.force_hype:
            print(f"{OUT_HYPE} đã tồn tại - giữ nguyên (dùng --force-hype để ghi đè).")
        else:
            OUT_HYPE.write_text(json.dumps(default_hype(chars), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"Đã ghi {OUT_HYPE}")


if __name__ == "__main__":
    main()
