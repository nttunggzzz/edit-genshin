"""Chế độ "chỉ UID": từ một UID, lấy dữ liệu công khai của Enka.Network rồi TỰ VẼ các mảnh kiểu game
(thẻ nhân vật, thẻ vũ khí, ô hồ sơ AR) thành một AccountSheet, sau đó ghép bằng renderer có sẵn.

Mẫu để vẽ giống game: ảnh chụp thật của chủ dự án (data/acc-card/inbox/887630784/chars.webp, weapons.png,
profile.png) và bản ghép tay data/acc-card/psd/dep.psd. Số đo dưới đây lấy từ các ảnh đó, nhân 2 cho nét.

Giới hạn (Enka chỉ có Tủ trưng bày): không có nhân vật ngoài tủ, vũ khí trong túi, EXP Mạo Hiểm,
sinh nhật, Mối Duyên/nhựa/lượt roll banner, khoá vũ khí. Không bịa: thiếu thì để "–" hoặc bỏ.

CLI:  python -m gacard.uidmode <uid> --out x.jpg [--bg-file PATH] [--code N] [--work-dir DIR] [--offline]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from gacard import characters
from gacard.enka import EnkaClient, EnkaError, EnkaProfile, fetch_sync, parse_profile, validate_uid
from gacard.models import AccountSheet, Card, Piece, RenderOptions
from gacard.theme import font

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UA = "gacard/0.1 (genshin account card bot; contact via telegram)"
ENKA_UI = "https://enka.network/ui/"
STORE_URL = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/gi/"
MAX_ICON_BYTES = 5 * 1024 * 1024
LANCZOS = Image.Resampling.LANCZOS
RGB = tuple[int, int, int]

# ------------------------------------------------------------------ số đo (×2 so với ảnh chụp game)
CHAR_SIZE = (228, 300)        # thẻ nhân vật game ≈ 114×150
CHAR_STRIP = 60               # dải sáng "Lv.xx" ≈ 30 px
CHAR_NOTCH = 26               # bo góc dưới phải vùng ảnh
WEAPON_SIZE = (248, 304)      # thẻ vũ khí game ≈ 124×152
WEAPON_STRIP = 52
WEAPON_NOTCH = 32
CARD_RADIUS = 10
PROFILE_SIZE = (874, 336)     # ô hồ sơ game ≈ 437×168
PROFILE_COMPACT_ROW = 84      # ô hồ sơ gọn (2 hàng AR + Cấp Thế Giới): cao 1 hàng
PROFILE_COMPACT_PAD = 6

STRIP = (233, 230, 219)       # màu dải dưới thẻ
LV_TEXT = (73, 83, 102)       # chữ "Lv.90"
STAR = (255, 204, 50)
STAR_EDGE = (196, 128, 36)

# nền thẻ theo độ hiếm: (đỉnh trái, đáy phải) đo từ ảnh game
RARITY_BG: dict[int, tuple[RGB, RGB]] = {
    5: ((132, 87, 34), (194, 124, 42)),
    4: ((90, 84, 120), (158, 119, 182)),
    3: ((58, 82, 112), (82, 138, 172)),
    2: ((58, 96, 82), (88, 146, 112)),
    1: ((92, 94, 100), (136, 138, 146)),
}
# màu glyph nguyên tố khi không tải được icon
ELEMENT_COLOR: dict[str, RGB] = {
    "pyro": (239, 121, 56), "hydro": (75, 195, 241), "anemo": (116, 226, 193),
    "electro": (190, 150, 240), "dendro": (165, 210, 59), "cryo": (159, 214, 227), "geo": (250, 182, 44),
}
# slug nguyên tố → tên Enka (icon UI_Buff_Element_<tên>; Ice không có trên Enka → glyph)
ELEMENT_ENKA = {"pyro": "Fire", "hydro": "Water", "anemo": "Wind", "electro": "Electric",
                "dendro": "Grass", "cryo": "Ice", "geo": "Rock"}
ENKA_ELEMENT = {v: k for k, v in ELEMENT_ENKA.items()}

_ICON_NAME_RE = re.compile(r"[A-Za-z0-9_]{3,120}(\.(png|jpg))?")


def data_dir() -> Path:
    return Path(os.environ.get("GACARD_DATA_DIR") or PROJECT_ROOT / "data" / "acc-card")


# =================================================================== icon cache
class IconCache:
    """Tải icon Enka (https://enka.network/ui/<tên>.png) một lần vào thư mục cache.
    Không bao giờ ném lỗi: tải hỏng / offline / tên lạ → None (người gọi vẽ placeholder)."""

    def __init__(self, cache_dir: Path, user_agent: str = DEFAULT_UA, timeout: float = 10.0, *,
                 base_url: str = ENKA_UI, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.user_agent = user_agent
        self.timeout = timeout
        self.base_url = base_url.rstrip("/") + "/"
        self.offline = offline
        self._failed: set[str] = set()

    @staticmethod
    def filename(name: str) -> str | None:
        name = (name or "").strip().removeprefix("/ui/")
        if not _ICON_NAME_RE.fullmatch(name):
            return None
        return name if name.endswith((".png", ".jpg")) else f"{name}.png"

    def path(self, name: str) -> Path | None:
        fn = self.filename(name)
        return self.cache_dir / fn if fn else None

    def _download(self, fn: str, dest: Path) -> bool:
        req = urllib.request.Request(self.base_url + fn, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (URL cố định Enka)
                data = resp.read(MAX_ICON_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("Không tải được icon %s: %s", fn, exc)
            return False
        if len(data) > MAX_ICON_BYTES:
            return False
        try:
            with Image.open(BytesIO(data)) as im:
                im.verify()
        except (OSError, Image.DecompressionBombError, SyntaxError):
            return False
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
        except OSError:
            return False
        return True

    def ensure(self, name: str | None) -> Path | None:
        """Đường dẫn file icon trong cache (tải nếu chưa có); None nếu không có được."""
        if not name:
            return None
        fn = self.filename(name)
        if fn is None or fn in self._failed:
            return None
        dest = self.cache_dir / fn
        if dest.is_file():
            return dest
        if self.offline or not self._download(fn, dest):
            self._failed.add(fn)
            return None
        return dest

    def get(self, name: str | None) -> Image.Image | None:
        p = self.ensure(name)
        if p is None:
            return None
        try:
            with Image.open(p) as im:
                return im.convert("RGBA")
        except (OSError, Image.DecompressionBombError):
            return None

    def prefetch(self, names: list[str], workers: int = 6) -> None:
        todo = list(dict.fromkeys(n for n in names if n))
        if not todo or self.offline:
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self.ensure, todo))


def load_store(name: str, store_dir: Path | None = None, *, download: bool = True,
               timeout: float = 15.0) -> dict:
    """Một file store Enka (avatars.json, namecards.json...) trong data/acc-card/enka-store; thiếu thì
    tải một lần. Lỗi → {} (chỉ mất tính năng phụ: trang phục, namecard, nguyên tố Nhà Lữ Hành)."""
    path = (store_dir or data_dir() / "enka-store") / name
    if not path.is_file() and download:
        try:
            req = urllib.request.Request(STORE_URL + name, headers={"User-Agent": DEFAULT_UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                data = resp.read(20 * 1024 * 1024)
            json.loads(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("Không tải được store %s: %s", name, exc)
            return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# =================================================================== dữ liệu từ JSON Enka
@dataclass
class ShowcaseChar:
    avatar_id: int
    slug: str | None
    name: str
    rarity: int | None
    element: str | None          # slug nguyên tố (pyro...)
    level: int | None
    constellation: int | None
    icon: str | None             # tên icon Enka (đã tính trang phục)
    side_icon: str | None


@dataclass
class WeaponInfo:
    item_id: int
    icon: str | None
    rarity: int | None
    level: int | None
    refinement: int | None       # 1–5; None với vũ khí 1–2★ (không tinh luyện được)
    owner_slug: str | None
    owner_side_icon: str | None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _icon_name(path: str | None) -> str | None:
    if not path:
        return None
    return path.rsplit("/", 1)[-1].removesuffix(".png")


def parse_showcase(raw: dict, profile: EnkaProfile, avatars_store: dict | None = None
                   ) -> tuple[list[ShowcaseChar], list[WeaponInfo]]:
    """Nhân vật trưng bày (đúng thứ tự profile.avatars) + vũ khí đang trang bị của họ."""
    store = avatars_store or {}
    info = raw.get("playerInfo") or {}
    show = {_int(s.get("avatarId")): s for s in info.get("showAvatarInfoList") or [] if isinstance(s, dict)}
    details = {_int(d.get("avatarId")): d for d in raw.get("avatarInfoList") or [] if isinstance(d, dict)}

    chars: list[ShowcaseChar] = []
    weapons: list[WeaponInfo] = []
    for av in profile.avatars:
        meta = characters.by_avatar_id(av.avatar_id)
        detail = details.get(av.avatar_id) or {}
        s = show.get(av.avatar_id) or {}
        costume = _int(detail.get("costumeId")) or _int(s.get("costumeId"))
        st = store.get(str(av.avatar_id)) or {}
        depot = _int(detail.get("skillDepotId"))
        if depot is not None and f"{av.avatar_id}-{depot}" in store:   # Nhà Lữ Hành: nguyên tố theo depot
            st = {**st, **store[f"{av.avatar_id}-{depot}"]}
        icon = meta.icon if meta else None
        side = meta.side_icon if meta else None
        if not icon and st.get("SideIconName"):
            side = _icon_name(st["SideIconName"])
            icon = side.replace("_Side_", "_") if side else None
        cost = (st.get("Costumes") or {}).get(str(costume)) if costume else None
        if cost:
            icon = _icon_name(cost.get("Icon")) or icon
            side = _icon_name(cost.get("SideIcon")) or side
        element = (meta.element if meta else None) or ENKA_ELEMENT.get(st.get("Element", ""))
        rarity = meta.rarity if meta else {"QUALITY_ORANGE": 5, "QUALITY_PURPLE": 4,
                                           "QUALITY_ORANGE_SP": 5}.get(st.get("QualityType", ""))
        name = characters.display_name(av.character) if av.character else f"id {av.avatar_id}"
        chars.append(ShowcaseChar(av.avatar_id, av.character, name, rarity, element, av.level,
                                  av.constellation, icon, side))
        for eq in detail.get("equipList") or []:
            w = eq.get("weapon") if isinstance(eq, dict) else None
            if not isinstance(w, dict):
                continue
            flat = eq.get("flat") or {}
            affix = w.get("affixMap") or {}
            ref = None
            if affix:
                ref = (_int(next(iter(affix.values()))) or 0) + 1
            icon_w = flat.get("icon")
            if icon_w and (_int(w.get("promoteLevel")) or 0) >= 2:
                icon_w = f"{icon_w}_Awaken"   # game đổi hình vũ khí sau đột phá 2 (như Enka)
            weapons.append(WeaponInfo(_int(eq.get("itemId")) or 0, icon_w, _int(flat.get("rankLevel")),
                                      _int(w.get("level")), ref, av.character, side))
    return chars, weapons


# =================================================================== vẽ
def _gradient(size: tuple[int, int], top_left: RGB, bottom_right: RGB) -> Image.Image:
    """Chuyển sắc chủ yếu theo dọc, hơi chéo (trên-trái tối → dưới-phải sáng) như nền thẻ game."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = np.clip(0.8 * yy / max(h - 1, 1) + 0.2 * xx / max(w - 1, 1), 0, 1)[..., None]
    a, b = np.asarray(top_left, np.float32), np.asarray(bottom_right, np.float32)
    arr = a + (b - a) * t
    return Image.fromarray(np.round(arr).astype(np.uint8), "RGB").convert("RGBA")


def _round_mask(size: tuple[int, int], radius: int, corners=(True, True, True, True)) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255,
                                        corners=corners)
    return m


def _star_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    pts = []
    for i in range(10):
        ang = -np.pi / 2 + i * np.pi / 5
        rr = r if i % 2 == 0 else r * 0.47
        pts.append((cx + rr * np.cos(ang), cy + rr * np.sin(ang)))
    return pts


def _draw_star(img: Image.Image, cx: float, cy: float, r: float, ss: int = 4) -> None:
    """Ngôi sao vàng viền cam (vẽ phóng to rồi thu nhỏ cho mịn)."""
    size = int(2 * r + 6)
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c = size * ss / 2
    d.polygon(_star_points(c, c, (r + 1.2) * ss), fill=(*STAR_EDGE, 255))
    d.polygon(_star_points(c, c, r * ss), fill=(*STAR, 255))
    small = big.resize((size, size), LANCZOS)
    img.alpha_composite(small, (round(cx - size / 2), round(cy - size / 2)))


def _text_center(img: Image.Image, center: tuple[float, float], text: str, size: int, fill,
                 weight: str = "display") -> None:
    f = font(size, weight)
    l, t, r, b = f.getbbox(text)
    x = center[0] - (r - l) / 2 - l
    y = center[1] - (b - t) / 2 - t
    ImageDraw.Draw(img).text((round(x), round(y)), text, font=f, fill=fill)


def placeholder_icon(size: int = 256) -> Image.Image:
    """Bóng người xám mờ khi thiếu icon nhân vật/vũ khí."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (255, 255, 255, 90)
    d.ellipse((size * 0.34, size * 0.18, size * 0.66, size * 0.50), fill=c)
    d.pieslice((size * 0.14, size * 0.55, size * 0.86, size * 1.25), 180, 360, fill=c)
    return im


def element_glyph(element: str | None, size: int = 52) -> Image.Image | None:
    """Glyph nguyên tố tự vẽ (khi không có icon Enka): hình thoi màu nguyên tố, viền sáng."""
    color = ELEMENT_COLOR.get(element or "")
    if color is None:
        return None
    ss = 4
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    s = size * ss
    d.ellipse((s * 0.08, s * 0.08, s * 0.92, s * 0.92), fill=(*color, 70))
    d.polygon([(s / 2, s * 0.14), (s * 0.86, s / 2), (s / 2, s * 0.86), (s * 0.14, s / 2)],
              fill=(*color, 255), outline=(255, 255, 255, 200), width=ss * 2)
    d.ellipse((s * 0.40, s * 0.40, s * 0.60, s * 0.60), fill=(255, 255, 255, 220))
    return big.resize((size, size), LANCZOS)


def _card_base(size: tuple[int, int], strip_h: int, notch: int, rarity: int | None,
               art: Image.Image | None, art_scale: float = 1.0) -> Image.Image:
    """Nền thẻ: vùng ảnh (gradient độ hiếm + icon vuông canh đáy) bo góc dưới phải, dải sáng dưới."""
    w, h = size
    art_h = h - strip_h
    top, bottom = RARITY_BG.get(rarity or 0, RARITY_BG[1])
    area = _gradient((w, art_h), top, bottom)
    icon = art if art is not None else placeholder_icon()
    side = round(w * art_scale)
    icon = icon.convert("RGBA").resize((side, side), LANCZOS)
    area.alpha_composite(icon, ((w - side) // 2, art_h - side) if art_h >= side else ((w - side) // 2, 0))
    card = Image.new("RGBA", size, (*STRIP, 255))
    card.paste(area, (0, 0), _round_mask((w, art_h), notch, (False, False, True, False)))
    card.putalpha(_round_mask(size, CARD_RADIUS))
    return card


def draw_character_card(icon: Image.Image | None, rarity: int | None, element: str | None,
                        level: int | None, element_icon: Image.Image | None = None) -> Image.Image:
    """Thẻ nhân vật kiểu game (chars.webp): nền cam 5★ / tím 4★, chân dung, icon nguyên tố góc trên
    trái, sao góc trên phải nếu 5★, dải sáng "Lv.90" chữ tối. Cung mệnh do renderer đè (Card.badge)."""
    w, h = CHAR_SIZE
    card = _card_base(CHAR_SIZE, CHAR_STRIP, CHAR_NOTCH, rarity, icon)
    el = element_icon.convert("RGBA").resize((52, 52), LANCZOS) if element_icon else element_glyph(element)
    if el is not None:
        card.alpha_composite(el, (12, 14))
    if rarity == 5:   # game: sao vàng trên ô vuông tối mờ
        box = Image.new("RGBA", (36, 36), (0, 0, 0, 0))
        ImageDraw.Draw(box).rounded_rectangle((0, 0, 35, 35), radius=5, fill=(40, 28, 16, 60))
        card.alpha_composite(box, (w - 48, 22))
        _draw_star(card, w - 30, 40, 14)
    art_h = h - CHAR_STRIP
    _text_center(card, (w / 2, art_h + CHAR_STRIP / 2 + 1), f"Lv.{level}" if level else "Lv.–", 34, LV_TEXT)
    return card


def _owner_bubble(card: Image.Image, side_icon: Image.Image | None, cx: int, cy: int, r: int) -> None:
    """Ô tròn chủ vũ khí góc trên phải: nền xám nhạt, viền trắng, side icon; cắt theo mép thẻ."""
    ss = 4
    d = 2 * r
    bub = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    disk = Image.new("L", (d * ss, d * ss), 0)
    ImageDraw.Draw(disk).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=255)
    disk = disk.resize((d, d), LANCZOS)
    inner = Image.new("RGBA", (d, d), (196, 196, 204, 255))
    if side_icon is not None:
        s = round(d * 1.18)
        face = side_icon.convert("RGBA").resize((s, s), LANCZOS)
        inner.alpha_composite(face, ((d - s) // 2, d - s + round(d * 0.06)))
    bub.paste(inner, (0, 0), disk)
    ring = Image.new("L", (d * ss, d * ss), 0)
    ImageDraw.Draw(ring).ellipse((0, 0, d * ss - 1, d * ss - 1), outline=255, width=3 * ss)
    ring = ring.resize((d, d), LANCZOS)
    bub.paste(Image.new("RGBA", (d, d), (248, 248, 250, 255)), (0, 0), ring)
    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    layer.alpha_composite(bub, (cx - r, cy - r))
    alpha = np.minimum(np.asarray(layer.getchannel("A")), np.asarray(card.getchannel("A")))
    layer.putalpha(Image.fromarray(alpha))
    card.alpha_composite(layer)


def draw_weapon_card(icon: Image.Image | None, rarity: int | None, level: int | None,
                     refinement: int | None, owner_side_icon: Image.Image | None = None,
                     *, show_owner: bool = True) -> Image.Image:
    """Thẻ vũ khí kiểu game (weapons.png): ô tinh luyện góc trên trái, hàng sao, ô chủ vũ khí góc trên
    phải, dải "Lv.90". Không vẽ ổ khoá (Enka không có trạng thái khoá)."""
    w, h = WEAPON_SIZE
    card = _card_base(WEAPON_SIZE, WEAPON_STRIP, WEAPON_NOTCH, rarity, icon)
    art_h = h - WEAPON_STRIP
    if refinement:
        box = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        ImageDraw.Draw(box).rounded_rectangle((0, 0, 39, 39), radius=5, fill=(40, 28, 16, 130))
        card.alpha_composite(box, (10, 10))
        color = (255, 196, 96) if refinement >= 5 else (238, 232, 220)
        _text_center(card, (30, 30), str(refinement), 34, color)
    if show_owner:
        _owner_bubble(card, owner_side_icon, w - 30, 28, 36)
    n = max(0, min(rarity or 0, 5))
    step, r = 40, 19
    x0 = w / 2 - step * (n - 1) / 2
    for i in range(n):
        _draw_star(card, x0 + i * step, art_h - 16, r)
    _text_center(card, (w / 2, art_h + WEAPON_STRIP / 2 + 1), f"Lv.{level}" if level else "Lv.–", 34, LV_TEXT)
    return card


def _info_icon(img: Image.Image, cx: int, cy: int, r: int) -> None:
    ss = 4
    d = 2 * r
    big = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=(236, 229, 214, 255))
    img.alpha_composite(big.resize((d, d), LANCZOS), (cx - r, cy - r))
    _text_center(img, (cx, cy + 1), "i", round(r * 1.4), (70, 62, 56))


def _shadow_text(img: Image.Image, xy: tuple[float, float], text: str, size: int, anchor: str) -> None:
    """Chữ trắng có bóng mờ nhẹ như UI game. anchor: 'lm' (trái-giữa) hoặc 'rm' (phải-giữa)."""
    f = font(size, "display")
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((xy[0] + 2, xy[1] + 2), text, font=f, fill=(10, 20, 50, 170), anchor=anchor)
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))
    ImageDraw.Draw(img).text(xy, text, font=f, fill=(255, 255, 255, 255), anchor=anchor)


def _profile_background(size: tuple[int, int], namecard: Image.Image | None) -> Image.Image:
    w, h = size
    if namecard is not None:
        nc = namecard.convert("RGB")
        scale = max(w / nc.width, h / nc.height)
        nc = nc.resize((round(nc.width * scale) + 1, round(nc.height * scale) + 1), LANCZOS)
        left, top = (nc.width - w) // 2, (nc.height - h) // 2
        box = nc.crop((left, top, left + w, top + h)).convert("RGBA")
        box.alpha_composite(Image.new("RGBA", (w, h), (8, 20, 60, 70)))
        return box
    return _gradient((w, h), (28, 62, 140), (70, 112, 196))


def draw_profile_box_compact(ar: int | None, world_level: int | None,
                             namecard: Image.Image | None = None) -> Image.Image:
    """Ô hồ sơ gọn: chỉ Hạng Mạo Hiểm và Cấp Thế Giới (không EXP, không Sinh Nhật), cùng kiểu chữ /
    nền namecard như draw_profile_box. Không biết Cấp Thế Giới → bỏ hẳn hàng đó (không vẽ "–")."""
    rows = [("Hạng Mạo Hiểm", ar)]
    if world_level is not None:
        rows.append(("Cấp Thế Giới", world_level))
    w = PROFILE_SIZE[0]
    h = PROFILE_COMPACT_PAD * 2 + PROFILE_COMPACT_ROW * len(rows)
    box = _profile_background((w, h), namecard)
    for i, (label, value) in enumerate(rows):
        cy = PROFILE_COMPACT_PAD + PROFILE_COMPACT_ROW * i + PROFILE_COMPACT_ROW // 2
        if i:
            ImageDraw.Draw(box).line((18, cy - PROFILE_COMPACT_ROW // 2, w - 18, cy - PROFILE_COMPACT_ROW // 2),
                                     fill=(255, 255, 255, 60), width=2)
        _shadow_text(box, (18, cy), label, 50, "lm")
        _shadow_text(box, (w - 84, cy), str(value) if value is not None else "–", 54, "rm")
        _info_icon(box, w - 40, cy, 22)
    return box


def draw_profile_box(ar: int | None, world_level: int | None, birthday: str | None = None,
                     namecard: Image.Image | None = None, ar_exp: tuple[int, int] | None = None) -> Image.Image:
    """Ô hồ sơ như profile.png: Hạng Mạo Hiểm, EXP Mạo Hiểm, Cấp Thế Giới, Sinh Nhật trên nền namecard.
    Giá trị không biết → "–"; không biết EXP thì không vẽ thanh EXP (không bịa)."""
    w, h = PROFILE_SIZE
    box = _profile_background((w, h), namecard)
    dash = "–"
    _shadow_text(box, (18, 44), "Hạng Mạo Hiểm", 50, "lm")
    _shadow_text(box, (w - 84, 44), str(ar) if ar is not None else dash, 54, "rm")
    _info_icon(box, w - 40, 44, 22)
    # hàng EXP: ô "AR" nhỏ + nhãn; số và thanh chỉ khi biết
    ImageDraw.Draw(box).rounded_rectangle((18, 92, 58, 132), radius=6, fill=(118, 150, 62, 255))
    _text_center(box, (38, 112), "AR", 20, (238, 246, 214))
    _shadow_text(box, (72, 112), "EXP Mạo Hiểm", 32, "lm")
    if ar_exp:
        cur, total = ar_exp
        _shadow_text(box, (w - 16, 112), f"{cur} / {total}", 32, "rm")
        d = ImageDraw.Draw(box)
        d.rounded_rectangle((72, 138, w - 16, 148), radius=5, fill=(16, 40, 96, 220))
        frac = max(0.0, min(1.0, cur / total)) if total else 0.0
        if frac > 0:
            d.rounded_rectangle((72, 138, 72 + round((w - 88) * frac), 148), radius=5, fill=(204, 255, 102, 255))
    else:
        _shadow_text(box, (w - 16, 112), dash, 32, "rm")
    _shadow_text(box, (18, 206), "Cấp Thế Giới", 50, "lm")
    _shadow_text(box, (w - 84, 206), str(world_level) if world_level is not None else dash, 54, "rm")
    _info_icon(box, w - 40, 206, 22)
    _shadow_text(box, (18, 290), "Sinh Nhật", 50, "lm")
    _shadow_text(box, (w - 16, 290), birthday or dash, 50, "rm")
    return box


# =================================================================== sheet
@dataclass
class UidModeReport:
    adventure_rank: int | None = None
    world_level: int | None = None
    characters: list[str] = field(default_factory=list)       # "Tên Lv90 C0"
    weapons: list[str] = field(default_factory=list)           # "UI_EquipIcon_... R1 Lv90 (chủ)"
    no_constellation: list[str] = field(default_factory=list)
    missing_icons: list[str] = field(default_factory=list)
    unknown_avatars: list[int] = field(default_factory=list)
    not_available: list[str] = field(default_factory=lambda: [
        "nhân vật ngoài Tủ trưng bày",
        "vũ khí trong túi (chỉ có vũ khí đang trang bị cho nhân vật trưng bày)",
        "EXP Mạo Hiểm và Sinh Nhật (Enka không trả)",
        "Mối Duyên, nhựa, số lượt roll banner, liên kết tài khoản",
        "panel cung mệnh, khoá vũ khí",
    ])

    def text_vi(self) -> str:
        ar = self.adventure_rank if self.adventure_rank is not None else "?"
        wl = self.world_level if self.world_level is not None else "?"
        lines = [f"Chế độ chỉ UID: AR {ar}, Cấp Thế Giới {wl}.",
                 f"Nhân vật trưng bày ({len(self.characters)}): " + (", ".join(self.characters) or "không có"),
                 f"Vũ khí đang trang bị ({len(self.weapons)})."]
        if not self.characters:
            lines.append("Tủ trưng bày trống hoặc bị ẩn: không có thẻ nhân vật/vũ khí nào.")
        if self.no_constellation:
            lines.append("Không có cung mệnh (chủ acc ẩn chi tiết): " + ", ".join(self.no_constellation))
        if self.unknown_avatars:
            lines.append("Nhân vật chưa có trong bảng: " + ", ".join(map(str, self.unknown_avatars)))
        if self.missing_icons:
            lines.append(f"Thiếu {len(self.missing_icons)} icon (đã vẽ ô thay thế): "
                         + ", ".join(self.missing_icons))
        lines.append("Chế độ này KHÔNG có: " + "; ".join(self.not_available) + ".")
        return "\n".join(lines)


def _weapon_sort_key(w: WeaponInfo) -> tuple:
    """Như túi đồ game: độ hiếm giảm dần, rồi cấp, rồi tinh luyện."""
    return (-(w.rarity or 0), -(w.level or 0), -(w.refinement or 0))


def build_sheet_from_raw(raw: dict, profile: EnkaProfile, work_dir: Path, icons: IconCache, *,
                         avatars_store: dict | None = None, namecards: dict | None = None
                         ) -> tuple[AccountSheet, UidModeReport]:
    """Vẽ mảnh vào work_dir (char_XX.png, weapon_XX.png, profile.png) + ghi sheet.json."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    chars, weapons = parse_showcase(raw, profile, avatars_store)
    weapons.sort(key=_weapon_sort_key)
    report = UidModeReport(adventure_rank=profile.adventure_rank, world_level=profile.world_level)

    namecard_icon = None
    nc_id = (raw.get("playerInfo") or {}).get("nameCardId")
    if namecards and nc_id is not None:   # namecard đang dùng = nền ô hồ sơ trong game
        namecard_icon = str((namecards.get(str(nc_id)) or {}).get("Icon") or "").rsplit("/", 1)[-1] or None
    elements = {f"UI_Buff_Element_{ELEMENT_ENKA[c.element]}" for c in chars
                if c.element in ELEMENT_ENKA and c.element != "cryo"}
    icons.prefetch([c.icon for c in chars] + [w.icon for w in weapons] + [w.owner_side_icon for w in weapons]
                   + sorted(elements) + ([namecard_icon] if namecard_icon else []))

    def get_icon(name: str | None, label: str) -> Image.Image | None:
        img = icons.get(name)
        if img is None:
            report.missing_icons.append(name or label)
        return img

    sheet = AccountSheet()
    prof = draw_profile_box(profile.adventure_rank, profile.world_level,
                            namecard=icons.get(namecard_icon) if namecard_icon else None)
    prof.save(work_dir / "profile.png")
    sheet.profile = Piece(image=Path("profile.png"))

    for i, c in enumerate(chars):
        el_icon = None
        if c.element in ELEMENT_ENKA and c.element != "cryo":
            el_icon = icons.get(f"UI_Buff_Element_{ELEMENT_ENKA[c.element]}")
        img = draw_character_card(get_icon(c.icon, c.name), c.rarity, c.element, c.level, el_icon)
        name = f"char_{i:02d}.png"
        img.save(work_dir / name)
        sheet.characters.append(Card(image=Path(name), rarity=c.rarity, character=c.slug,
                                     badge=c.constellation))
        cons = f"C{c.constellation}" if c.constellation is not None else "C?"
        report.characters.append(f"{c.name} Lv{c.level or '?'} {cons}")
        if c.constellation is None:
            report.no_constellation.append(c.name)
        if c.slug is None:
            report.unknown_avatars.append(c.avatar_id)

    for i, wpn in enumerate(weapons):
        side = icons.get(wpn.owner_side_icon) if wpn.owner_side_icon else None
        img = draw_weapon_card(get_icon(wpn.icon, f"vũ khí {wpn.item_id}"), wpn.rarity, wpn.level,
                               wpn.refinement, side)
        name = f"weapon_{i:02d}.png"
        img.save(work_dir / name)
        sheet.weapons.append(Card(image=Path(name), rarity=wpn.rarity))
        ref = f"R{wpn.refinement}" if wpn.refinement else "R–"
        report.weapons.append(f"{wpn.rarity or '?'}★ {ref} Lv{wpn.level or '?'}")

    (work_dir / "sheet.json").write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
    return sheet, report


def _raw_from_cache(client: EnkaClient, uid: str) -> dict | None:
    cached = client._read_cache(uid)   # EnkaClient lưu nguyên JSON Enka; EnkaProfile không có vũ khí
    return cached[0] if cached else None


def build_sheet_from_uid(uid: str, work_dir: Path, *, client: EnkaClient | None = None,
                         icons: IconCache | None = None, offline: bool = False
                         ) -> tuple[AccountSheet, EnkaProfile, UidModeReport]:
    """UID → (sheet đã vẽ trong work_dir, hồ sơ Enka, báo cáo). Lỗi Enka → EnkaError."""
    uid = validate_uid(uid)
    root = data_dir()
    ua = os.environ.get("GACARD_ENKA_USER_AGENT") or DEFAULT_UA
    client = client or EnkaClient(root / "enka-cache", ua)
    if offline:
        raw = _raw_from_cache(client, uid)
        if raw is None:
            raise EnkaError("network", "Chưa có dữ liệu Enka trong cache cho UID này (đang chạy offline).")
        profile = parse_profile(raw, uid)
    else:
        profile = fetch_sync(client, uid)
        raw = _raw_from_cache(client, uid)
        if raw is None:
            raise EnkaError("bad_response")
    icons = icons or IconCache(root / "icons", ua, offline=offline)
    store_dir = root / "enka-store"
    sheet, report = build_sheet_from_raw(
        raw, profile, work_dir, icons,
        avatars_store=load_store("avatars.json", store_dir, download=not offline),
        namecards=load_store("namecards.json", store_dir, download=not offline))
    return sheet, profile, report


# =================================================================== CLI
def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m gacard.uidmode", description="Ảnh acc chỉ từ UID (Enka)")
    p.add_argument("uid")
    p.add_argument("--out", required=True, help="file JPG đầu ra")
    p.add_argument("--bg-file", help="ảnh nền")
    p.add_argument("--code", help='mã acc, vd 1116 hoặc "CN#1076"')
    p.add_argument("--work-dir", help="thư mục ghi các mảnh đã vẽ (mặc định: <out>.parts/)")
    p.add_argument("--frame-color", help="#RRGGBB")
    p.add_argument("--watermark")
    p.add_argument("--offline", action="store_true", help="chỉ dùng cache Enka và icon đã tải")
    args = p.parse_args(argv)

    from gacard.render import SheetError, render_sheet

    out = Path(args.out)
    work = Path(args.work_dir) if args.work_dir else out.with_name(out.stem + ".parts")
    try:
        sheet, profile, report = build_sheet_from_uid(args.uid, work, offline=args.offline)
        options = RenderOptions(code=args.code, uid=profile.uid, frame_color=args.frame_color,
                                watermark=args.watermark)
        data = render_sheet(sheet, work, options,
                            background_file=Path(args.bg_file) if args.bg_file else None)
    except EnkaError as exc:
        print(f"Lỗi Enka ({exc.kind}): {exc.message_vi}", file=sys.stderr)
        return 1
    except SheetError as exc:
        print(f"Lỗi: {exc.message_vi}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(report.text_vi())
    print(f"Đã lưu ảnh: {out} ({len(data) / 1024:.0f} KB); mảnh trong {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
