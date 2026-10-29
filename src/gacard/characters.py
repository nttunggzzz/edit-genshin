"""Bảng nhân vật Genshin (avatar_id <-> slug <-> tên), tra cứu mờ và điểm "săn đón" (hype).

Dữ liệu: assets/acc-card/characters.json (sinh bởi scripts/acc_card/build_characters.py từ store Enka)
và assets/acc-card/hype.json (chủ dự án sửa tay).
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "acc-card"
CHARACTERS_PATH = ASSETS_DIR / "characters.json"
HYPE_PATH = ASSETS_DIR / "hype.json"

# Biệt danh/viết tắt hay gặp -> slug. Slug không tồn tại trong bảng sẽ bị bỏ qua.
ALIASES: dict[str, list[str]] = {
    "raiden-shogun": ["raiden", "shogun", "ei", "baal", "lei", "raiden ei"],
    "kaedehara-kazuha": ["kazuha", "kazu", "kaedehara"],
    "hu-tao": ["hutao", "ho dao", "hodao", "hu"],
    "kamisato-ayaka": ["ayaka", "ayaka kamisato"],
    "kamisato-ayato": ["ayato"],
    "sangonomiya-kokomi": ["kokomi", "coco"],
    "arataki-itto": ["itto"],
    "yae-miko": ["yae", "miko"],
    "shikanoin-heizou": ["heizou"],
    "tartaglia": ["childe", "ajax"],
    "wanderer": ["scaramouche", "scara", "kunikuzushi", "lang thang", "ke lang thang"],
    "zhongli": ["morax", "zhong li"],
    "neuvillette": ["neuv", "neuvi", "neuvilette", "neuvilet"],
    "wriothesley": ["wrio", "wriothsley"],
    "arlecchino": ["arle", "arlechino", "arlequino", "knave"],
    "kuki-shinobu": ["shinobu", "kuki"],
    "kujou-sara": ["sara", "kujou"],
    "yumemizuki-mizuki": ["mizuki"],
    "sigewinne": ["sige", "sigewin"],
    "mavuika": ["mavu"],
    "xiangling": ["xiang ling"],
    "xingqiu": ["xing qiu"],
    "yelan": ["ye lan"],
    "yun-jin": ["yunjin"],
    "lan-yan": ["lanyan"],
    "lumine": ["lumi", "nu lu khach"],
    "aether": ["nam lu khach"],
}

_SPLIT_RE = re.compile(r"[^a-z0-9]+")


class Character(BaseModel):
    avatar_id: int
    slug: str
    name_en: str
    name_vi: str
    rarity: int
    element: str | None = None
    weapon_type: str | None = None
    icon: str | None = None
    side_icon: str | None = None
    is_traveler: bool = False
    kind: str = "character"


def normalize(text: str) -> str:
    """Chữ thường, bỏ dấu (kể cả đ), ký tự lạ -> khoảng trắng đơn."""
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(t for t in _SPLIT_RE.split(text) if t)


@lru_cache(maxsize=None)
def _load(path: str) -> tuple[Character, ...]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(Character(**c) for c in data["characters"])


def load_characters(path: Path | None = None) -> list[Character]:
    return list(_load(str(path or CHARACTERS_PATH)))


@lru_cache(maxsize=None)
def _by_id(path: str) -> dict[int, Character]:
    return {c.avatar_id: c for c in _load(path)}


@lru_cache(maxsize=None)
def _by_slug(path: str) -> dict[str, Character]:
    return {c.slug: c for c in _load(path)}


def by_avatar_id(avatar_id: int, path: Path | None = None) -> Character | None:
    return _by_id(str(path or CHARACTERS_PATH)).get(int(avatar_id))


def by_slug(slug: str, path: Path | None = None) -> Character | None:
    return _by_slug(str(path or CHARACTERS_PATH)).get(slug)


def display_name(slug: str, path: Path | None = None) -> str:
    """Tên tiếng Việt (nếu có), không thì tên tiếng Anh, không biết slug thì trả lại slug."""
    c = by_slug(slug, path)
    if c is None:
        return slug
    return c.name_vi or c.name_en


# ---------------------------------------------------------------- tra cứu mờ
@lru_cache(maxsize=None)
def _index(path: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(compact -> {slug}), (token -> {slug}). compact = chuỗi đã chuẩn hóa, bỏ khoảng trắng."""
    chars = _load(path)
    known = {c.slug for c in chars}
    compact: dict[str, set[str]] = {}
    tokens: dict[str, set[str]] = {}

    def add(slug: str, text: str, with_tokens: bool) -> None:
        norm = normalize(text)
        if not norm:
            return
        compact.setdefault(norm.replace(" ", ""), set()).add(slug)
        if with_tokens:
            for t in norm.split():
                if len(t) >= 3:
                    tokens.setdefault(t, set()).add(slug)

    for c in chars:
        add(c.slug, c.slug, True)
        add(c.slug, c.name_en, True)
        add(c.slug, c.name_vi, True)
    for slug, names in ALIASES.items():
        if slug in known:
            for n in names:
                add(slug, n, False)
    return compact, tokens


def resolve_character(name: str, path: Path | None = None, *, cutoff: float = 0.8) -> str | None:
    """Tên người dùng gõ (Việt/Anh, có/không dấu, biệt danh, gõ sai nhẹ) -> slug.

    Trả None nếu không khớp hoặc mơ hồ (vd 'kamisato' ứng với cả Ayaka và Ayato, 'traveler').
    """
    p = str(path or CHARACTERS_PATH)
    norm = normalize(name or "")
    if not norm:
        return None
    compact, tokens = _index(p)
    key = norm.replace(" ", "")

    # 1. khớp nguyên cụm (slug / tên Anh / tên Việt / biệt danh)
    hit = compact.get(key)
    if hit:
        return next(iter(hit)) if len(hit) == 1 else None

    # 2. mọi từ người dùng gõ đều là từ trong tên của đúng một nhân vật ("shogun", "tao")
    qtokens = [t for t in norm.split() if t]
    if qtokens and all(len(t) >= 3 for t in qtokens):
        sets = [tokens.get(t) for t in qtokens]
        if all(sets):
            common = set.intersection(*[s for s in sets if s])
            if len(common) == 1:
                return next(iter(common))
            if len(common) > 1:
                return None

    # 3. gõ sai: difflib trên chuỗi liền, cần khác biệt rõ giữa ứng viên tốt nhất và nhì
    if len(key) < 4:
        return None
    scored: dict[str, float] = {}
    for cand, slugs in compact.items():
        r = difflib.SequenceMatcher(None, key, cand).ratio()
        if r >= cutoff:
            for s in slugs:
                scored[s] = max(scored.get(s, 0.0), r)
    if not scored:
        return None
    ranked = sorted(scored.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.05:
        return None
    return ranked[0][0]


# --------------------------------------------------------------------- hype
@lru_cache(maxsize=None)
def _hype(path: str) -> dict[str, int]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scores = {k: int(v) for k, v in data.get("scores", {}).items()}
    for i, slug in enumerate(data.get("hot", [])):
        scores[slug] = max(100 - i, 0)
    return scores


def load_hype(path: Path | None = None) -> dict[str, int]:
    """slug -> điểm 0-100. Danh sách 'hot' ghi đè 'scores' (100, 99, 98... theo thứ tự)."""
    return dict(_hype(str(path or HYPE_PATH)))


def clear_caches() -> None:
    """Dùng khi file hype.json/characters.json đổi lúc đang chạy (vd lệnh /uutien)."""
    for fn in (_load, _by_id, _by_slug, _index, _hype):
        fn.cache_clear()


def top_hype(slugs: Iterable[str], n: int = 1, hype: dict[str, int] | None = None) -> list[str]:
    """n slug có điểm hype cao nhất (hòa điểm thì giữ thứ tự đầu vào, bỏ trùng). Slug lạ = 0 điểm."""
    scores = load_hype() if hype is None else hype
    seen: dict[str, None] = dict.fromkeys(slugs)
    ordered = sorted(seen, key=lambda s: -scores.get(s, 0))  # sort ổn định
    return ordered[: max(n, 0)]
