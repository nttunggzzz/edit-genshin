"""Chế độ HoYoLAB: đọc Sổ Tay Hành Trình (Battle Chronicle) Genshin của CHÍNH chủ acc qua cookie
HoYoLAB (mọi nhân vật đang có, không chỉ Tủ trưng bày như Enka) rồi vẽ bằng các hàm vẽ của uidmode.
Như ảnh mẫu của chủ dự án, không vẽ hết acc: hết 5★ + 4★ nổi bật, lưới đủ hàng (select_characters,
select_weapons); ô hồ sơ gọn chỉ AR + Cấp Thế Giới (lấy từ Enka vì HoYoLAB không trả).

Xác thực: CHỈ bằng cookie chủ acc tự chép từ trình duyệt đang đăng nhập của họ vào .env
(GACARD_HOYOLAB_COOKIE = nguyên chuỗi header Cookie, phải có ltuid_v2 và ltoken_v2).
Tool KHÔNG BAO GIỜ nhận tài khoản/mật khẩu, không tự đăng nhập, không chụp/chuyển/giải captcha.
Gặp yêu cầu xác minh (geetest): DỪNG; CLI có thể mở trang Battle Chronicle THẬT trên trình duyệt mặc
định để người dùng tự kéo captcha ở đó, đợi Enter rồi gọi lại đúng request đó MỘT lần.
Không bao giờ log/in giá trị cookie; lỗi luôn đi qua redact().

Endpoint và tên trường: docs/acc-card/04-hoyolab-api.md (xác minh bằng HAR của chủ dự án).
Header DS / x-rpc-*: theo genshin.py (github.com/thesadru/genshin.py) — genshin/utility/ds.py
(generate_dynamic_secret, get_ds_headers nhánh OVERSEAS), genshin/constants.py (DS_SALT OVERSEAS),
genshin/utility/uid.py (recognize_genshin_server).

CLI:  python -m gacard.hoyolab <uid> [--code N] [--bg-file PATH | --bg slug] [--watermark @x] [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from gacard import characters
from gacard.layout import CHAR_COLS, DEFAULT_WEAPON_COLS
from gacard.models import AccountSheet, Card, Item, ItemKind, Piece, RenderOptions
from gacard.theme import font
from gacard.uidmode import (CHAR_SIZE, ELEMENT_ENKA, MAX_ICON_BYTES, PROJECT_ROOT, WEAPON_SIZE, IconCache,
                            data_dir, draw_character_card, draw_profile_box_compact, draw_weapon_card)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gacard.wish import BannerCounter

# ------------------------------------------------------------------ hằng số API
API_HOST = "https://sg-act-public-api.hoyolab.com"
API_BASE = API_HOST + "/event/game_record/genshin/api"
BATTLE_CHRONICLE_URL = ("https://act.hoyolab.com/app/community-game-records-sea/index.html"
                        "?bbs_presentation_style=fullscreen&v=350&gid=2&utm_source=hoyolab&utm_medium=tools"
                        "&bbs_theme=light&bbs_theme_device=1#/ys")
DS_SALT_OVERSEAS = "6s25p5ox5y14umn1p61aqyyvbvvl3lrt"   # genshin.py constants.DS_SALT[OVERSEAS]
APP_VERSION = "1.5.0"                                     # genshin.py get_ds_headers (OVERSEAS)
CLIENT_TYPE = "5"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
ORIGIN = "https://act.hoyolab.com"
CACHE_TTL = 600            # giây
# Chọn thẻ để vẽ (chủ dự án): hết 5★ + 4★ "mạnh", hàng cuối luôn đủ; vũ khí 1–3★ không bao giờ vẽ
STRONG_CHAR_LEVEL = 70     # nhân vật 4★ mạnh: cấp ≥ 70 ...
STRONG_CHAR_CONS = 4       # ... hoặc cung mệnh ≥ 4
STRONG_WEAPON_REF = 3      # vũ khí 4★ mạnh: tinh luyện ≥ 3 ...
STRONG_WEAPON_LEVEL = 80   # ... hoặc cấp ≥ 80
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# genshin.py utility/uid.py GENSHIN_SERVER_RANGE (chỉ máy chủ quốc tế; CN dùng Miyoushe, không hỗ trợ)
SERVER_BY_PREFIX = {"6": "os_usa", "7": "os_euro", "8": "os_asia", "18": "os_asia", "9": "os_cht"}
CN_PREFIXES = {"1", "2", "3", "5"}

RETCODE_COOKIE = {-100, 10001}
RETCODE_PRIVATE = {10102}
RETCODE_NOT_LINKED = {10103}
RETCODE_RATE = {10101}
RETCODE_VERIFY = {1034, 10035, 5003}
_VERIFY_WORDS = ("geetest", "verification", "verify", "captcha", "xác minh")

MESSAGES = {
    "no_cookie": ("Chưa có GACARD_HOYOLAB_COOKIE trong .env. Cách lấy: đăng nhập hoyolab.com trên trình "
                  "duyệt của bạn → F12 → Application (Chrome) / Storage (Firefox) → Cookies → "
                  "https://www.hoyolab.com → chép nguyên chuỗi cookie (phải có ltuid_v2 và ltoken_v2) "
                  "vào dòng GACARD_HOYOLAB_COOKIE=... trong .env. Không gửi cookie cho ai."),
    "bad_cookie": ("Cookie HoYoLAB thiếu ltuid_v2 hoặc ltoken_v2 (hoặc có ký tự xuống dòng). Chép lại "
                   "nguyên chuỗi cookie từ trình duyệt đang đăng nhập hoyolab.com."),
    "cookie_invalid": ("Cookie HoYoLAB không hợp lệ hoặc đã hết hạn. Đăng nhập lại hoyolab.com trên "
                       "trình duyệt của bạn rồi chép cookie mới vào GACARD_HOYOLAB_COOKIE."),
    "private": ("Dữ liệu acc này chưa công khai. Chủ acc vào HoYoLAB → Sổ Tay Hành Trình (Battle "
                "Chronicle) → Cài đặt, bật công khai Sổ Tay Hành Trình và chi tiết nhân vật, rồi chạy lại."),
    "not_linked": ("Cookie đúng nhưng tài khoản HoYoLAB này chưa liên kết acc Genshin (hoặc UID không "
                   "thuộc tài khoản này). Kiểm tra UID và liên kết game trong HoYoLAB."),
    "rate_limit": ("HoYoLAB đang giới hạn lượt tra cứu (quá nhiều lần / quá nhiều UID trong ngày). "
                   "Đợi một lúc (hoặc sang ngày mai) rồi chạy lại."),
    "verification": ("HoYoLAB yêu cầu xác minh (captcha). Mở Sổ Tay Hành Trình (Battle Chronicle) trên "
                     "CHÍNH trình duyệt của bạn: " + BATTLE_CHRONICLE_URL + " — tự kéo captcha ở đó cho "
                     "xong, rồi chạy lại lệnh này."),
    "invalid_uid": "UID không hợp lệ (cần 9–10 chữ số).",
    "unsupported_server": ("UID máy chủ Trung Quốc (Miyoushe) chưa được hỗ trợ; chỉ máy chủ quốc tế "
                           "(Mỹ 6, Âu 7, Á 8/18, TW-HK-MO 9)."),
    "network": "Không kết nối được HoYoLAB (mạng lỗi hoặc quá thời gian). Thử lại sau.",
    "bad_response": "HoYoLAB trả dữ liệu lạ, không đọc được.",
    "api": "HoYoLAB báo lỗi.",
}

VERIFY_PROMPT = ("HoYoLAB cần xác minh. Mình đã mở trang Battle Chronicle trên trình duyệt của bạn — "
                 "hãy tự kéo captcha ở đó, xong quay lại đây nhấn Enter.")


class HoyolabError(Exception):
    """Lỗi HoYoLAB; `message_vi` hiển thị cho người dùng (không bao giờ chứa cookie)."""

    def __init__(self, kind: str, message_vi: str | None = None, retcode: int | None = None):
        self.kind = kind
        self.retcode = retcode
        self.message_vi = message_vi or MESSAGES.get(kind, MESSAGES["api"])
        super().__init__(self.message_vi)


# ------------------------------------------------------------------ cookie, DS, server
def parse_cookie(cookie: str | None) -> dict[str, str]:
    """Chuỗi header Cookie → dict. Thiếu → no_cookie; thiếu ltuid_v2/ltoken_v2 → bad_cookie."""
    raw = (cookie or "").strip()
    if not raw:
        raise HoyolabError("no_cookie")
    if "\r" in raw or "\n" in raw:
        raise HoyolabError("bad_cookie")
    jar: dict[str, str] = {}
    for part in raw.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip():
            jar[k.strip()] = v.strip()
    if not jar.get("ltuid_v2") or not jar.get("ltoken_v2"):
        raise HoyolabError("bad_cookie")
    return jar


def redact(text: str, cookie: str | None) -> str:
    """Xoá mọi giá trị cookie (và cả chuỗi cookie) khỏi một đoạn chữ trước khi in/log."""
    out = str(text)
    if not cookie:
        return out
    out = out.replace(cookie, "***")
    values = []
    for part in cookie.split(";"):
        _, sep, v = part.strip().partition("=")
        if sep and len(v.strip()) >= 4:
            values.append(v.strip())
    for v in sorted(values, key=len, reverse=True):
        out = out.replace(v, "***")
    return out


def generate_ds(salt: str = DS_SALT_OVERSEAS, *, t: int | None = None, r: str | None = None) -> str:
    """DS quốc tế như genshin.py generate_dynamic_secret: "t,r,md5(salt=..&t=..&r=..)"."""
    t = int(time.time()) if t is None else int(t)
    r = r if r is not None else "".join(random.choices(string.ascii_letters, k=6))
    h = hashlib.md5(f"salt={salt}&t={t}&r={r}".encode()).hexdigest()
    return f"{t},{r},{h}"


def validate_uid(uid: str | int) -> str:
    s = str(uid).strip()
    if not re.fullmatch(r"\d{9,10}", s):
        raise HoyolabError("invalid_uid")
    return s


def recognize_server(uid: str | int) -> str:
    """Như genshin.py recognize_genshin_server: tiền tố = mọi chữ số trừ 8 số cuối."""
    s = validate_uid(uid)
    prefix = s[:-8]
    if prefix in SERVER_BY_PREFIX:
        return SERVER_BY_PREFIX[prefix]
    if prefix in CN_PREFIXES:
        raise HoyolabError("unsupported_server")
    raise HoyolabError("invalid_uid")


def build_headers(cookie: str, lang: str) -> dict[str, str]:
    return {
        "Cookie": cookie,
        "DS": generate_ds(),
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": CLIENT_TYPE,
        "x-rpc-language": lang,
        "x-rpc-lang": lang,
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }


# ------------------------------------------------------------------ transport
Transport = Callable[[str, str, dict, bytes | None, float], tuple[int, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None,
                     timeout: float) -> tuple[int, bytes]:
    """(HTTP status, body). Lỗi mạng → HoyolabError('network') (không kèm header nào)."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (host cố định)
            return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            data = exc.read(MAX_RESPONSE_BYTES + 1)
        except OSError:
            data = b""
        return exc.code, data
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HoyolabError("network", f"{MESSAGES['network']} ({type(exc).__name__})") from None


def classify_retcode(retcode: int, message: str) -> str | None:
    """retcode → kind lỗi (None = 0, thành công)."""
    if retcode == 0:
        return None
    low = (message or "").lower()
    if retcode in RETCODE_VERIFY or any(w in low for w in _VERIFY_WORDS):
        return "verification"
    if retcode in RETCODE_COOKIE:
        return "cookie_invalid"
    if retcode in RETCODE_PRIVATE:
        return "private"
    if retcode in RETCODE_NOT_LINKED:
        return "not_linked"
    if retcode in RETCODE_RATE:
        return "rate_limit"
    return "api"


class HoyolabClient:
    """Client Battle Chronicle Genshin (đồng bộ, urllib). Cache JSON 10 phút trong
    data/acc-card/hoyolab-cache/ (không chứa cookie).

    on_verification: hàm gọi khi HoYoLAB đòi xác minh; trả True (người dùng đã tự giải trên trình
    duyệt của họ) → gọi lại đúng request đó MỘT lần. None → ném HoyolabError('verification') ngay."""

    def __init__(self, cookie: str, lang: str = "vi-vn", timeout: float = 15, *,
                 cache_dir: Path | None = None, cache_ttl: int = CACHE_TTL,
                 transport: Transport | None = None,
                 on_verification: Callable[[], bool] | None = None):
        parse_cookie(cookie)
        self._cookie = cookie.strip()
        self.lang = lang
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else data_dir() / "hoyolab-cache"
        self.cache_ttl = cache_ttl
        self._transport = transport
        self.on_verification = on_verification
        self.calls = 0               # số request thật đã gửi (cho test)

    def __repr__(self) -> str:      # không bao giờ lộ cookie
        return f"HoyolabClient(lang={self.lang!r}, cookie=***)"

    # ---------------------------------------------------------- cache
    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', key)}.json"

    def _read_cache(self, key: str) -> Any | None:
        p = self._cache_path(key)
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(blob, dict) or time.time() - float(blob.get("saved_at", 0)) > self.cache_ttl:
            return None
        return blob.get("data")

    def _write_cache(self, key: str, data: Any) -> None:
        p = self._cache_path(key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"saved_at": time.time(), "data": data}, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(p)
        except OSError as exc:
            log.info("Không ghi được cache HoYoLAB: %s", exc)

    # ---------------------------------------------------------- request
    def _send_once(self, method: str, path: str, params: dict | None, body: dict | None) -> Any:
        url = f"{API_BASE}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = build_headers(self._cookie, self.lang)
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json;charset=UTF-8"
        transport = self._transport or urllib_transport
        self.calls += 1
        try:
            status, raw = transport(method, url, headers, payload, self.timeout)
        except HoyolabError:
            raise
        except Exception as exc:  # noqa: BLE001 — không để lỗi lạ kéo theo header/cookie ra ngoài
            raise HoyolabError("network", redact(f"{MESSAGES['network']} ({type(exc).__name__})",
                                                 self._cookie)) from None
        if status == 429:
            raise HoyolabError("rate_limit")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise HoyolabError("bad_response")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            if status != 200:
                raise HoyolabError("network", f"{MESSAGES['network']} (HTTP {status})") from None
            raise HoyolabError("bad_response") from None
        if not isinstance(doc, dict) or "retcode" not in doc:
            raise HoyolabError("bad_response")
        try:
            retcode = int(doc.get("retcode"))
        except (TypeError, ValueError):
            raise HoyolabError("bad_response") from None
        message = str(doc.get("message") or "")
        kind = classify_retcode(retcode, message)
        if kind is None:
            data = doc.get("data")
            if not isinstance(data, dict):
                raise HoyolabError("bad_response")
            return data
        text = MESSAGES[kind]
        if kind == "api":
            text = redact(f"{MESSAGES['api']} retcode {retcode}: {message[:200]}", self._cookie)
        raise HoyolabError(kind, text, retcode)

    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
                 cache_key: str | None = None) -> dict:
        if cache_key:
            cached = self._read_cache(cache_key)
            if isinstance(cached, dict):
                return cached
        try:
            data = self._send_once(method, path, params, body)
        except HoyolabError as exc:
            if exc.kind != "verification" or self.on_verification is None:
                raise
            if not self.on_verification():
                raise
            data = self._send_once(method, path, params, body)   # đúng MỘT lần; còn đòi → ném lỗi
        if cache_key:
            self._write_cache(cache_key, data)
        return data

    # ---------------------------------------------------------- endpoint
    def index(self, uid: str | int) -> dict:
        uid = validate_uid(uid)
        server = recognize_server(uid)
        return self._request("GET", "index", params={"avatar_list_type": 1, "server": server, "role_id": uid},
                             cache_key=f"{uid}-index-{self.lang}")

    def character_list(self, uid: str | int) -> dict:
        uid = validate_uid(uid)
        server = recognize_server(uid)
        return self._request("POST", "character/list", body={"role_id": uid, "server": server},
                             cache_key=f"{uid}-character_list-{self.lang}")

    def character_detail(self, uid: str | int, character_ids: list[int]) -> dict:
        """Chi tiết (thánh di vật, kỹ năng...). Tool ảnh acc chưa cần; HAR gọi 1 id/lần."""
        uid = validate_uid(uid)
        server = recognize_server(uid)
        ids = [int(i) for i in character_ids]
        key = f"{uid}-character_detail-{'-'.join(map(str, ids))}-{self.lang}" if len(ids) <= 4 else None
        return self._request("POST", "character/detail",
                             body={"server": server, "role_id": uid, "character_ids": ids}, cache_key=key)


def interactive_verification(opener: Callable[[str], Any] | None = None,
                             reader: Callable[[], str] | None = None) -> bool:
    """Mở trang Battle Chronicle THẬT trên trình duyệt mặc định để người dùng tự giải captcha, đợi Enter.
    Không đọc/chụp/chuyển captcha. EOF (không có bàn phím) → False."""
    (opener or webbrowser.open)(BATTLE_CHRONICLE_URL)
    print(VERIFY_PROMPT, flush=True)
    try:
        (reader or input)()
    except (EOFError, KeyboardInterrupt):
        return False
    return True


# ------------------------------------------------------------------ icon theo URL
ALLOWED_ICON_HOSTS = ("hoyolab.com", "hoyoverse.com", "enka.network")
ALLOWED_ICON_EXACT = {"upload-os-bbs.mihoyo.com", "act-upload.mihoyo.com", "upload-bbs.mihoyo.com"}


def allowed_icon_url(url: str | None) -> bool:
    if not url or not isinstance(url, str) or len(url) > 1000:
        return False
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.username or u.password or u.port not in (None, 443):
        return False
    return host in ALLOWED_ICON_EXACT or any(host == h or host.endswith("." + h) for h in ALLOWED_ICON_HOSTS)


def icon_stem(url: str | None) -> str | None:
    """'https://.../UI_AvatarIcon_Ayaka.png' → 'UI_AvatarIcon_Ayaka' (để lấy icon cùng tên từ Enka)."""
    if not url:
        return None
    name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    name = re.sub(r"\.(png|jpg|jpeg|webp)$", "", name, flags=re.I)
    return name if re.fullmatch(r"UI_[A-Za-z0-9_]{3,100}", name) else None


class UrlIconCache:
    """Tải icon theo URL HoYoLAB (đã kiểm whitelist host) một lần vào cache. Không bao giờ ném lỗi."""

    def __init__(self, cache_dir: Path, timeout: float = 10.0, *, offline: bool = False):
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.offline = offline
        self._failed: set[str] = set()

    def path(self, url: str) -> Path:
        ext = ".jpg" if urllib.parse.urlsplit(url).path.lower().endswith((".jpg", ".jpeg")) else ".png"
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:24] + ext)

    def _download(self, url: str, dest: Path) -> bool:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (đã whitelist)
                if not allowed_icon_url(resp.geturl()):
                    return False
                data = resp.read(MAX_ICON_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.info("Không tải được icon HoYoLAB: %s", type(exc).__name__)
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

    def ensure(self, url: str | None) -> Path | None:
        if not allowed_icon_url(url) or url in self._failed:
            return None
        dest = self.path(url)
        if dest.is_file():
            return dest
        if self.offline or not self._download(url, dest):
            self._failed.add(url)
            return None
        return dest

    def get(self, url: str | None) -> Image.Image | None:
        p = self.ensure(url)
        if p is None:
            return None
        try:
            with Image.open(p) as im:
                return im.convert("RGBA")
        except (OSError, Image.DecompressionBombError):
            return None

    def prefetch(self, urls: list[str | None], workers: int = 6) -> None:
        todo = list(dict.fromkeys(u for u in urls if allowed_icon_url(u)))
        if not todo or self.offline:
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(self.ensure, todo))


# ------------------------------------------------------------------ dữ liệu
@dataclass
class HoyoWeapon:
    weapon_id: int
    name: str
    icon_url: str | None
    rarity: int | None
    level: int | None
    refinement: int | None
    owner_slug: str | None
    owner_side_url: str | None
    owner_side_name: str | None      # tên icon Enka dự phòng


@dataclass
class HoyoChar:
    avatar_id: int
    slug: str | None
    name: str
    rarity: int | None
    element: str | None
    level: int | None
    constellation: int | None
    icon_url: str | None
    icon_name: str | None            # tên icon Enka dự phòng
    side_url: str | None
    side_name: str | None
    weapon: HoyoWeapon | None = None
    image_url: str | None = None     # ảnh nhân vật lớn (nền trong suốt), dùng cho ô đếm banner


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _rarity(v: Any) -> int | None:
    r = _int(v)
    if r is None:
        return None
    return 5 if r > 5 else r          # Aloy: HoYoLAB trả 105 (5★ đặc biệt)


def _url(v: Any) -> str | None:
    return v if isinstance(v, str) and allowed_icon_url(v) else None


def parse_character_list(data: dict) -> list[HoyoChar]:
    """data của character/list → nhân vật đã sắp như game: độ hiếm ↓, cấp ↓, cung mệnh ↓, id ↑."""
    out: list[HoyoChar] = []
    for it in data.get("list") or []:
        if not isinstance(it, dict):
            continue
        aid = _int(it.get("id"))
        if aid is None:
            continue
        meta = characters.by_avatar_id(aid)
        slug = meta.slug if meta else None
        icon_url, side_url = _url(it.get("icon")), _url(it.get("side_icon"))
        icon_name = icon_stem(icon_url) or (meta.icon if meta else None)
        side_name = icon_stem(side_url) or (meta.side_icon if meta else None)
        element = str(it.get("element") or "").strip().lower() or (meta.element if meta else None)
        name = characters.display_name(slug) if slug else str(it.get("name") or f"id {aid}")
        c = HoyoChar(aid, slug, name, _rarity(it.get("rarity")) or (meta.rarity if meta else None),
                     element, _int(it.get("level")), _int(it.get("actived_constellation_num")),
                     icon_url, icon_name, side_url, side_name)
        c.image_url = _url(it.get("image"))
        w = it.get("weapon")
        if isinstance(w, dict) and _int(w.get("id")) is not None:
            wr = _rarity(w.get("rarity"))
            ref = _int(w.get("affix_level"))
            if wr is not None and wr <= 2:
                ref = None            # vũ khí 1–2★ không tinh luyện
            w_url = _url(w.get("icon"))
            c.weapon = HoyoWeapon(_int(w.get("id")) or 0, str(w.get("name") or ""), w_url, wr,
                                  _int(w.get("level")), ref, slug, side_url, side_name)
        out.append(c)
    out.sort(key=lambda c: (-(c.rarity or 0), -(c.level or 0), -(c.constellation or 0), c.avatar_id))
    return out


def _weapon_sort_key(w: HoyoWeapon) -> tuple:
    return (-(w.rarity or 0), -(w.level or 0), -(w.refinement or 0), w.weapon_id)


def _four_star_rank(w: HoyoWeapon) -> tuple:
    """Ưu tiên giữ vũ khí 4★: tinh luyện ↓, cấp ↓, id ↑."""
    return (-(w.refinement or 0), -(w.level or 0), w.weapon_id)


WeaponFit = Callable[[int], tuple[int, bool]]   # số thẻ → (số cột layout chọn, lưới phủ hết bề rộng?)


def _fixed_fit(total: int) -> tuple[int, bool]:
    """Mặc định khi không có bố cục để hỏi: 8 cột (dep.psd)."""
    return min(total, DEFAULT_WEAPON_COLS), True


def layout_weapon_fit(profile: tuple[int, int] | None, characters: list[tuple[int, int]],
                      weapon_size: tuple[int, int], options: RenderOptions | None = None,
                      explorations: list[tuple[int, int]] = ()) -> WeaponFit:
    """Hỏi bố cục thật (cùng kích thước mảnh + chữ như lúc render) xem `t` thẻ vũ khí sẽ xếp mấy cột;
    có nhớ kết quả theo t."""
    from gacard.layout import LayoutSpec, weapon_grid_fit
    from gacard.render import spec_texts

    options = options or RenderOptions()
    texts = spec_texts(options)
    cache: dict[int, tuple[int, bool]] = {}

    def fit(total: int) -> tuple[int, bool]:
        if total not in cache:
            spec = LayoutSpec(profile=profile, characters=list(characters), weapons=[weapon_size] * total,
                              explorations=list(explorations), **texts)
            cache[total] = weapon_grid_fit(spec, options.family)
        return cache[total]

    return fit


def layout_exploration_count(profile: tuple[int, int] | None, characters: list[tuple[int, int]],
                             weapons: list[tuple[int, int]], explorations: list[tuple[int, int]],
                             options: RenderOptions | None = None) -> int:
    """Số thẻ khám phá bố cục thật đặt được (các thẻ đầu danh sách; phần còn lại bị bỏ vì thiếu chỗ)."""
    from gacard.layout import LayoutSpec, compute_layout
    from gacard.render import spec_texts

    if not explorations:
        return 0
    options = options or RenderOptions()
    spec = LayoutSpec(profile=profile, characters=list(characters), weapons=list(weapons),
                      explorations=list(explorations), **spec_texts(options))
    return len(compute_layout(spec, options.family).of("exploration"))


def _complete_weapon_rows(base: list, spare: list, fit: WeaponFit, weakest: Callable) -> list:
    """Như _complete_rows nhưng số cột lấy từ bố cục (`fit`), không cố định: ưu tiên tổng t mà lưới
    vừa đủ hàng VỪA "đẹp" (phủ bề rộng, thẻ gần cỡ tối đa — layout.weapon_grid_fit): trước hết lấp bằng
    `spare`; không được thì bớt thẻ yếu nhất, tối đa một hàng (vd acc 887630784: 30 → 27 = 9×3 thay vì
    10×3 thẻ nhỏ); không có thì chỉ cần đủ hàng (lấp, rồi bớt)."""
    def full(t: int) -> bool:
        cols, _ = fit(t)
        return cols > 0 and t % cols == 0

    def perfect(t: int) -> bool:
        return full(t) and fit(t)[1]

    n = len(base)
    if n == 0 or perfect(n):
        return list(base)
    for t in range(n + 1, n + len(spare) + 1):
        if perfect(t):
            return list(base) + list(spare[:t - n])
    for t in range(n - 1, 0, -1):
        if n - t > fit(t)[0]:            # bớt tối đa một hàng
            break
        if perfect(t):
            drop = {id(x) for x in sorted(base, key=weakest)[:n - t]}
            return [x for x in base if id(x) not in drop]
    return _complete_rows(base, spare, full, weakest)


def _complete_rows(base: list, spare: list, ok: Callable[[int], bool], weakest: Callable) -> list:
    """Hàng cuối luôn đủ: `base` (5★ + 4★ mạnh) đã đủ hàng → giữ nguyên; thiếu → lấp bằng `spare`
    (4★ còn lại, mạnh nhất trước) tới tổng đủ hàng gần nhất; `spare` không đủ để lấp → bỏ các thẻ
    yếu nhất của `base` (thứ tự `weakest`: 4★ trước 5★) tới tổng đủ hàng gần nhất bên dưới."""
    n = len(base)
    if n == 0 or ok(n):
        return list(base)
    for t in range(n + 1, n + len(spare) + 1):
        if ok(t):
            return list(base) + list(spare[:t - n])
    t = next((t for t in range(n - 1, 0, -1) if ok(t)), 0)
    drop = {id(x) for x in sorted(base, key=weakest)[:n - t]}
    return [x for x in base if id(x) not in drop]


def strong_weapon(w: HoyoWeapon) -> bool:
    """Vũ khí 4★ "mạnh": tinh luyện ≥ STRONG_WEAPON_REF hoặc cấp ≥ STRONG_WEAPON_LEVEL."""
    return (w.refinement or 0) >= STRONG_WEAPON_REF or (w.level or 0) >= STRONG_WEAPON_LEVEL


def select_weapons(weapons: list[HoyoWeapon], fit: WeaponFit | None = None) -> list[HoyoWeapon]:
    """Bỏ mọi vũ khí 1–3★ (và không rõ độ hiếm); giữ hết 5★ + 4★ mạnh (strong_weapon), không giới hạn
    số lượng; hàng cuối lưới luôn đủ theo đúng số cột bố cục chọn (`fit`, xem layout_weapon_fit; mặc
    định 8 cột): lấp bằng 4★ kế tiếp (tinh luyện ↓, cấp ↓), ưu tiên tổng làm lưới phủ hết bề rộng; hết
    4★ thì bỏ thẻ yếu nhất. Trả về theo thứ tự vẽ."""
    fives = [w for w in weapons if w.rarity == 5]
    fours = sorted((w for w in weapons if w.rarity == 4), key=_four_star_rank)
    base = fives + [w for w in fours if strong_weapon(w)]
    spare = [w for w in fours if not strong_weapon(w)]
    weakest = lambda w: (w.rarity or 0, w.refinement or 0, w.level or 0, -w.weapon_id)  # noqa: E731
    return sorted(_complete_weapon_rows(base, spare, fit or _fixed_fit, weakest), key=_weapon_sort_key)


def _char_sort_key(c: HoyoChar) -> tuple:
    return (-(c.rarity or 0), -(c.level or 0), -(c.constellation or 0), c.avatar_id)


def strong_character(c: HoyoChar) -> bool:
    """Nhân vật 4★ "mạnh": cấp ≥ STRONG_CHAR_LEVEL hoặc cung mệnh ≥ STRONG_CHAR_CONS."""
    return (c.level or 0) >= STRONG_CHAR_LEVEL or (c.constellation or 0) >= STRONG_CHAR_CONS


MIN_DRAWN_CHARACTERS = 25    # lưới nhân vật tối thiểu 5 hàng × 5 cột
MAX_DRAWN_ROWS = 10          # tối đa 10 hàng × 5 cột trên một khối (vượt quá thì bố cục tách hai khối)


def select_characters(chars: list[HoyoChar]) -> list[HoyoChar]:
    """Chủ yếu 5★: giữ HẾT 5★ (không bao giờ bỏ). Lưới luôn đủ 5 cột một hàng, tối thiểu 5 hàng
    (25 thẻ) và tối đa 10 hàng (50 thẻ) trên một khối:
    - Dưới 25 thẻ 5★: thêm 4★ mạnh nhất (cung mệnh ↓, cấp ↓, avatar_id ↑) cho đủ 25 thẻ.
    - Từ 25 thẻ 5★ trở lên: chỉ thêm 4★ khi cần lấp đủ hàng cuối của lưới 5 cột
    (5★ chia 5 dư 3 → thêm đúng 2 thẻ 4★); không thêm 4★ nào ngoài số cần để lấp.
    - Không đủ 4★ để lấp/đủ 25 thì thêm hết số 4★ có (không bỏ 5★, không bịa nhân vật).
    - > 50 thẻ thì bố cục tách hai khối cân bằng (xem layout._char_groups).
    Trả về theo thứ tự game."""
    fives = [c for c in chars if (c.rarity or 0) >= 5]
    fours = sorted((c for c in chars if (c.rarity or 0) < 5),
                   key=lambda c: (-(c.constellation or 0), -(c.level or 0), c.avatar_id))
    take: list[HoyoChar] = []
    if len(fives) < MIN_DRAWN_CHARACTERS:
        take = fours[:MIN_DRAWN_CHARACTERS - len(fives)]
    elif len(fives) % CHAR_COLS:
        up = CHAR_COLS - len(fives) % CHAR_COLS                    # số thẻ thiếu để đủ hàng
        if len(fours) >= up:
            take = fours[:up]
    return sorted(fives + take, key=_char_sort_key)


# ------------------------------------------------------------------ khám phá bản đồ
EXPLORATION_MAX_PERMILLE = 400        # chỉ vẽ vùng khám phá < 40 % (API trả phần nghìn: 670 = 67,0 %)
EXPLORATION_SIZE = (760, 200)         # mảnh thẻ khám phá (rộng/cao = layout.EXPLORATION_ASPECT)
EXPLORATION_BASE = (112, 54, 46)      # nền đỏ nâu như thẻ khám phá trong game (natlan-card-sample.png)
EXPLORATION_ALPHA = 228               # hơi trong để nền ảnh lộ nhẹ
EXPLORATION_BAR = (238, 222, 186)     # thanh tiến độ màu kem
EXPLORATION_TRACK = (36, 16, 12, 150)


@dataclass
class HoyoRegion:
    region_id: int
    name: str
    permille: int                    # 0–1000
    icon_url: str | None
    background_url: str | None


def parse_explorations(index_data: dict) -> list[HoyoRegion]:
    """index.data.world_explorations → các vùng cấp cao nhất (parent_id = 0); vùng con (parent_id ≠ 0,
    vd «Trầm Ngọc Cốc - Thượng Cốc») bị bỏ. Vùng cha có vùng con mà tự nó báo 0 (HoYoLAB chỉ tính ở
    vùng con, vd Trầm Ngọc Cốc) → lấy trung bình vùng con."""
    raw = [w for w in index_data.get("world_explorations") or [] if isinstance(w, dict)]
    children: dict[int, list[int]] = {}
    for w in raw:
        parent, pct = _int(w.get("parent_id")), _int(w.get("exploration_percentage"))
        if parent and pct is not None:
            children.setdefault(parent, []).append(pct)
    out = []
    for w in raw:
        rid, pct = _int(w.get("id")), _int(w.get("exploration_percentage"))
        name = str(w.get("name") or "").strip()
        if rid is None or pct is None or not name or (_int(w.get("parent_id")) or 0) != 0:
            continue
        if pct == 0 and children.get(rid):
            pct = round(sum(children[rid]) / len(children[rid]))
        out.append(HoyoRegion(rid, name, max(0, min(1000, pct)), _url(w.get("icon")),
                              _url(w.get("background_image"))))
    return out


def select_explorations(regions: list[HoyoRegion],
                        limit: int = EXPLORATION_MAX_PERMILLE) -> list[HoyoRegion]:
    """Vùng khám phá < limit (phần nghìn), % tăng dần (bố cục thiếu chỗ thì bỏ từ cuối = % cao nhất)."""
    return sorted((r for r in regions if r.permille < limit), key=lambda r: (r.permille, r.region_id))


def format_percent(permille: int) -> str:
    """670 → '67%', 355 → '35.5%'."""
    return f"{permille / 10:.1f}".rstrip("0").rstrip(".") + "%"


def _placeholder_emblem(size: int) -> Image.Image:
    """Huy hiệu mờ khi vùng không có icon (sao 8 cánh trắng mờ)."""
    ss = 4
    s = size * ss
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pts = []
    for i in range(16):
        ang = -math.pi / 2 + i * math.pi / 8
        rr = s * (0.46 if i % 2 == 0 else 0.2)
        pts.append((s / 2 + rr * math.cos(ang), s / 2 + rr * math.sin(ang)))
    d = ImageDraw.Draw(im)
    d.polygon(pts, fill=(255, 255, 255, 110))
    d.ellipse((s * 0.4, s * 0.4, s * 0.6, s * 0.6), fill=(0, 0, 0, 0))
    return im.resize((size, size), Image.Resampling.LANCZOS)


def draw_exploration_card(name: str, permille: int, icon: Image.Image | None = None,
                          background: Image.Image | None = None) -> Image.Image:
    """Thẻ khám phá kiểu game (natlan-card-sample.png): nền đỏ nâu hơi trong (ảnh nền vùng tối đi, canh
    phải), huy hiệu vùng bên trái, tên vùng chữ trắng đậm, % canh phải cùng hàng, thanh tiến độ dưới.
    API không có dòng phụ nên không vẽ dòng phụ (không bịa chữ)."""
    w, h = EXPLORATION_SIZE
    card = Image.new("RGBA", (w, h), (*EXPLORATION_BASE, 255))
    if background is not None:
        bg = background.convert("RGBA")
        k = max(h / bg.height, (w * 0.75) / bg.width)
        bg = bg.resize((max(1, round(bg.width * k)), max(1, round(bg.height * k))), Image.Resampling.LANCZOS)
        top = (bg.height - h) // 2
        bg = bg.crop((0, top, bg.width, top + h))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.alpha_composite(bg, (w - bg.width, 0))
        alpha = np.asarray(layer.getchannel("A"), np.float32) * 0.42
        layer.putalpha(Image.fromarray(alpha.astype(np.uint8)))
        card.alpha_composite(layer)
        card.alpha_composite(Image.new("RGBA", (w, h), (*EXPLORATION_BASE, 70)))   # pha lại tông đỏ nâu
    side = round(h * 0.8)
    emblem = icon.convert("RGBA").resize((side, side), Image.Resampling.LANCZOS) if icon is not None \
        else _placeholder_emblem(round(side * 0.8))
    card.alpha_composite(emblem, ((h - emblem.width) // 2 + 6, (h - emblem.height) // 2))
    d = ImageDraw.Draw(card)
    tx, rx = h + 14, w - 26
    size = 58
    f_name = font(size, "display")
    while size > 30 and d.textlength(name, font=f_name) > rx - tx:
        size -= 2
        f_name = font(size, "display")
    d.text((tx, round(h * 0.36)), name, font=f_name, fill=(255, 255, 255, 255), anchor="lm")
    # hàng dưới như game: thanh tiến độ, % canh phải ở cuối hàng
    pct = format_percent(permille)
    f_pct = font(44, "display")
    bh = 14
    row = round(h * 0.72)
    d.text((rx, row), pct, font=f_pct, fill=(255, 255, 255, 240), anchor="rm")
    bx1 = rx - round(d.textlength(pct, font=f_pct)) - 22
    by = row - bh // 2
    d.rounded_rectangle((tx, by, bx1, by + bh), radius=bh // 2, fill=EXPLORATION_TRACK)
    fill_w = round((bx1 - tx) * max(0, min(1000, permille)) / 1000)
    if fill_w >= bh:
        d.rounded_rectangle((tx, by, tx + fill_w, by + bh), radius=bh // 2, fill=(*EXPLORATION_BAR, 255))
    a = np.asarray(card.getchannel("A"), np.float32) * (EXPLORATION_ALPHA / 255)
    card.putalpha(Image.fromarray(a.astype(np.uint8)))
    return card


# ------------------------------------------------------------------ Enka (Cấp Thế Giới, namecard)
@dataclass
class EnkaExtras:
    """Thứ HoYoLAB index không trả nhưng Enka có, cho cùng UID."""
    world_level: int | None = None
    namecard_icon: str | None = None      # tên icon Enka của namecard đang dùng
    source: str = "Enka"                  # "Enka" | "cache Enka"


def fetch_enka_extras(uid: str, *, offline: bool = False) -> EnkaExtras | None:
    """Cấp Thế Giới + namecard từ Enka (gọi API, lỗi thì dùng cache Enka cũ). Không bao giờ ném lỗi."""
    try:
        from gacard.enka import EnkaClient, EnkaError, fetch_sync
        from gacard.uidmode import DEFAULT_UA, load_store

        root = data_dir()
        client = EnkaClient(root / "enka-cache", os.environ.get("GACARD_ENKA_USER_AGENT") or DEFAULT_UA)
        source = "cache Enka"
        if not offline:
            try:
                fetch_sync(client, uid)
                source = "Enka"
            except EnkaError as exc:
                log.info("Enka lỗi (%s); thử cache Enka", exc.kind)
        cached = client._read_cache(uid)
        if not cached:
            return None
        info = cached[0].get("playerInfo") or {}
        extras = EnkaExtras(world_level=_int(info.get("worldLevel")), source=source)
        nc_id = info.get("nameCardId")
        if nc_id is not None:
            namecards = load_store("namecards.json", root / "enka-store", download=not offline) or {}
            icon = str((namecards.get(str(nc_id)) or {}).get("Icon") or "").rsplit("/", 1)[-1]
            extras.namecard_icon = icon or None
        return extras
    except Exception as exc:  # noqa: BLE001 — Enka chỉ là phần thêm, không được làm hỏng chế độ HoYoLAB
        log.info("Không lấy được dữ liệu Enka: %s", type(exc).__name__)
        return None


@dataclass
class HoyolabReport:
    nickname: str | None = None
    adventure_rank: int | None = None
    region: str | None = None
    avatar_number: int | None = None          # stats.avatar_number (index)
    world_level: int | None = None
    world_level_source: str | None = None     # "Enka" | "cache Enka" | None (ẩn hàng)
    characters: list[str] = field(default_factory=list)       # nhân vật đã vẽ
    account_characters: list[str] = field(default_factory=list)  # slug MỌI nhân vật acc có (chọn nền)
    characters_total: int = 0                                  # tổng nhân vật acc có (API)
    characters_5: int = 0                                      # số nhân vật 5★ đã vẽ
    weapons: list[str] = field(default_factory=list)          # vũ khí đã vẽ
    weapons_equipped: int = 0                                  # tổng vũ khí đang trang bị (API)
    weapons_dropped_low: int = 0                               # 1–3★ bỏ
    weapons_dropped_4: int = 0                                 # 4★ bỏ để lưới đủ hàng
    weapon_rarity: dict[int, int] = field(default_factory=dict)   # độ hiếm → số đã vẽ
    missing_icons: list[str] = field(default_factory=list)
    unknown_avatars: list[int] = field(default_factory=list)
    explorations: list[str] = field(default_factory=list)          # thẻ khám phá đã vẽ ("Tên 35.5%")
    explorations_dropped: list[str] = field(default_factory=list)  # < 40 % nhưng thiếu chỗ
    wish_lines: list[str] = field(default_factory=list)            # pity + ô banner (hoặc lý do bỏ qua)
    not_available: list[str] = field(default_factory=lambda: [
        "vũ khí trong túi (API chỉ trả vũ khí đang trang bị cho nhân vật)",
        "Mối Duyên, nhựa (số lượt roll banner chỉ có khi có GACARD_HOYOLAB_COOKIE_TOKEN để đọc lịch sử roll)",
        "EXP Mạo Hiểm, Sinh Nhật (không trả; ô hồ sơ bỏ hai hàng này)",
        "liên kết tài khoản, panel cung mệnh, khoá vũ khí",
    ])

    def text_vi(self) -> str:
        ar = self.adventure_rank if self.adventure_rank is not None else "?"
        wl = (f", Cấp Thế Giới {self.world_level} (từ {self.world_level_source})"
              if self.world_level is not None else "")
        lines = [f"Chế độ HoYoLAB: AR {ar}{wl}" + (f", máy chủ {self.region}" if self.region else "") + "."]
        if self.world_level is None:
            lines.append("Không có Cấp Thế Giới (HoYoLAB không trả, Enka cũng không có): ô hồ sơ chỉ còn AR.")
        lines.append(f"Nhân vật ({len(self.characters)}): " + (", ".join(self.characters) or "không có"))
        total = max(self.characters_total, len(self.characters))
        if total > len(self.characters):
            n4 = len(self.characters) - self.characters_5
            lines.append(f"Đã vẽ {len(self.characters)}/{total} nhân vật ({self.characters_5}×5★ + {n4}×4★: "
                         f"giữ hết 5★, thêm 4★ mạnh nhất (cung ↓, cấp ↓) để đủ tối thiểu 5 hàng và lấp đủ hàng 5); "
                         f"bỏ {total - len(self.characters)} nhân vật 4★.")
        if self.avatar_number is not None and self.avatar_number != total:
            lines.append(f"Lưu ý: index báo {self.avatar_number} nhân vật nhưng danh sách có {total}.")
        by = " + ".join(f"{self.weapon_rarity[r]}×{r}★" for r in sorted(self.weapon_rarity, reverse=True))
        line = f"Vũ khí: vẽ {len(self.weapons)}" + (f" ({by})" if by else "")
        line += f" / {self.weapons_equipped} đang trang bị"
        dropped = []
        if self.weapons_dropped_low:
            dropped.append(f"{self.weapons_dropped_low} vũ khí 1–3★")
        if self.weapons_dropped_4:
            dropped.append(f"{self.weapons_dropped_4} vũ khí 4★ yếu (giữ 4★ tinh luyện ≥ {STRONG_WEAPON_REF} "
                           f"hoặc cấp ≥ {STRONG_WEAPON_LEVEL}, lấp/bớt cho hàng cuối đủ, không ô trống)")
        lines.append(line + ("; bỏ " + ", ".join(dropped) if dropped else "") + ".")
        limit = format_percent(EXPLORATION_MAX_PERMILLE)
        if self.explorations or self.explorations_dropped:
            text = f"Khám phá < {limit}: " + (", ".join(self.explorations) or "không vẽ thẻ nào")
            if self.explorations_dropped:
                text += " (thiếu chỗ, bỏ: " + ", ".join(self.explorations_dropped) + ")"
            lines.append(text + ".")
        else:
            lines.append(f"Không vùng nào khám phá < {limit}: không vẽ thẻ khám phá.")
        lines.extend(self.wish_lines)
        if self.unknown_avatars:
            lines.append("Nhân vật chưa có trong bảng characters.json: " + ", ".join(map(str, self.unknown_avatars)))
        if self.missing_icons:
            lines.append(f"Thiếu {len(self.missing_icons)} icon (đã vẽ ô thay thế): "
                         + ", ".join(self.missing_icons[:20]) + (" …" if len(self.missing_icons) > 20 else ""))
        lines.append("HoYoLAB KHÔNG cung cấp: " + "; ".join(self.not_available) + ".")
        return "\n".join(lines)


def _add_banner_counter(sheet: AccountSheet, report: HoyolabReport, work_dir: Path, wish: BannerCounter,
                        all_chars: list[HoyoChar], icons: UrlIconCache,
                        templates: Path | None = None) -> None:
    """Ô đếm banner dựng từ khung mẫu của chủ dự án (bannerbox): 5★ gần nhất là nhân vật thường trú (lệch
    50/50) → khung `lech` điền "<Tên>\n(5 sao)" + N; còn lại → khung `limited` điền N (ảnh nhân vật 5★
    giới hạn: giữ nguyên ảnh của chủ trong mẫu). Thiếu khung mẫu → khung tạm + cảnh báo trong báo cáo."""
    from gacard import bannerbox

    name = wish.last5_name
    kind = bannerbox.choose_kind(name, wish.limited)
    template, warning = bannerbox.get_template(kind, templates)
    img = bannerbox.render_banner_box(template, wish.n, name)
    img.save(work_dir / "item_00.png")
    sheet.items.append(Item(image=Path("item_00.png"), kind=ItemKind.BANNER_COUNTER,
                            own_frame=template.own_frame))
    top = ("tên " + name + " (5 sao)") if kind == "lech" else "ảnh theo mẫu của chủ"
    report.wish_lines.append(f"Ô banner: N = {wish.n} (pity banner nhân vật {wish.pity} // 5, tối thiểu 1); "
                             f"khung {kind} ({template.source}), phần trên: {top}.")
    if warning:
        report.wish_lines.append(warning)


def _add_weapon_banner(sheet: AccountSheet, report: HoyolabReport, work_dir: Path, wish: BannerCounter,
                       templates: Path | None = None) -> None:
    """Ô banner vũ khí: y hệt ô banner nhân vật, khung `weapon` (từ PSD "banner wp"), N = max(1, pity_vukhi // 5)."""
    from gacard import bannerbox

    template, warning = bannerbox.get_template("weapon", templates)
    bannerbox.render_banner_box(template, wish.n).save(work_dir / "item_wp.png")
    sheet.items.append(Item(image=Path("item_wp.png"), kind=ItemKind.BANNER_COUNTER, own_frame=template.own_frame))
    report.wish_lines.append(f"Ô banner vũ khí: N = {wish.n} (pity banner vũ khí {wish.pity} // 5, tối thiểu 1); "
                             f"khung weapon ({template.source}).")
    if warning:
        report.wish_lines.append(warning)


def _add_input_frames(sheet: AccountSheet, report: HoyolabReport, work_dir: Path, options: RenderOptions | None,
                      templates: Path | None = None) -> None:
    """Khung thẻ tháng (chữ nhập ngoài) và khung số roll còn (số nhập ngoài). Trống/None → KHÔNG vẽ gì."""
    from gacard import bannerbox

    text = (options.month_card or "").strip() if options else ""
    if text:
        template, warning = bannerbox.get_template("monthcard", templates)
        bannerbox.render_text_box(template, text).save(work_dir / "item_month.png")
        # thẻ tháng là ô lớn (kind PORTRAIT: cao hơn các ô nhỏ) để chữ đủ đọc
        sheet.items.append(Item(image=Path("item_month.png"), kind=ItemKind.PORTRAIT, own_frame=template.own_frame))
        report.wish_lines.append(f"Khung thẻ tháng: \"{text}\" ({template.source}).")
        if warning:
            report.wish_lines.append(warning)
    if options is not None and options.rolls_left is not None:
        template, warning = bannerbox.get_template("roll", templates)
        bannerbox.render_banner_box(template, options.rolls_left).save(work_dir / "item_roll.png")
        sheet.items.append(Item(image=Path("item_roll.png"), kind=ItemKind.FATE, own_frame=template.own_frame))
        report.wish_lines.append(f"Khung số roll còn: {options.rolls_left} ({template.source}).")
        if warning:
            report.wish_lines.append(warning)


def build_sheet_from_data(index_data: dict, list_data: dict, work_dir: Path, icons: UrlIconCache,
                          enka_icons: IconCache | None = None, extras: EnkaExtras | None = None,
                          options: RenderOptions | None = None, wish: BannerCounter | None = None,
                          wish_lines: list[str] | None = None,
                          banner_templates: Path | None = None,
                          weapon_wish: BannerCounter | None = None) -> tuple[AccountSheet, HoyolabReport]:
    """Hàm thuần (không gọi API): vẽ mảnh vào work_dir + sheet.json. Icon: URL HoYoLAB, hỏng thì icon
    cùng tên trong cache Enka, không có nữa thì ô thay thế. `extras` (Enka, cùng UID): Cấp Thế Giới
    và namecard cho ô hồ sơ; không có thì ô hồ sơ chỉ còn AR. `options`: tuỳ chọn render sẽ dùng (mã,
    UID, watermark, family) — để hỏi bố cục đúng số cột vũ khí, hàng cuối đủ. `wish` (wish.banner_counter):
    có thì thêm ô đếm banner "‹ N ›" (bannerbox: khung mẫu của chủ dự án, chỉ điền N / tên); `wish_lines`: dòng báo cáo về lịch sử roll (pity, lý do bỏ qua);
    `banner_templates`: thư mục khung mẫu (mặc định data/acc-card/templates/banner)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    role = index_data.get("role") or {}
    stats = index_data.get("stats") or {}
    all_chars = parse_character_list(list_data)
    chars = select_characters(all_chars)
    equipped = [c.weapon for c in all_chars if c.weapon]
    world_level = extras.world_level if extras else None
    namecard_icon = extras.namecard_icon if extras else None
    if enka_icons is not None:
        elements = {f"UI_Buff_Element_{ELEMENT_ENKA[c.element]}" for c in chars
                    if c.element in ELEMENT_ENKA and c.element != "cryo"}
        enka_icons.prefetch(sorted(elements) + ([namecard_icon] if namecard_icon else []))
    namecard = enka_icons.get(namecard_icon) if enka_icons is not None and namecard_icon else None
    prof = draw_profile_box_compact(_int(role.get("level")), world_level, namecard=namecard)
    regions = select_explorations(parse_explorations(index_data))
    expl_sizes = [EXPLORATION_SIZE] * len(regions)
    fit = layout_weapon_fit(prof.size, [CHAR_SIZE] * len(chars), WEAPON_SIZE, options, expl_sizes)
    weapons = select_weapons(equipped, fit)
    shown = layout_exploration_count(prof.size, [CHAR_SIZE] * len(chars), [WEAPON_SIZE] * len(weapons),
                                     expl_sizes, options)
    regions, dropped_regions = regions[:shown], regions[shown:]
    report = HoyolabReport(nickname=role.get("nickname"), adventure_rank=_int(role.get("level")),
                           region=role.get("region"), avatar_number=_int(stats.get("avatar_number")),
                           world_level=world_level,
                           world_level_source=extras.source if extras and world_level is not None else None,
                           characters_total=len(all_chars),
                           account_characters=list(dict.fromkeys(c.slug for c in all_chars if c.slug)),
                           characters_5=sum(1 for c in chars if (c.rarity or 0) >= 5),
                           weapons_equipped=len(equipped),
                           weapons_dropped_low=sum(1 for w in equipped if (w.rarity or 0) < 4),
                           weapons_dropped_4=sum(1 for w in equipped if w.rarity == 4)
                           - sum(1 for w in weapons if w.rarity == 4))
    for w in weapons:
        report.weapon_rarity[w.rarity or 0] = report.weapon_rarity.get(w.rarity or 0, 0) + 1

    report.explorations_dropped = [f"{r.name} {format_percent(r.permille)}" for r in dropped_regions]
    icons.prefetch([c.icon_url for c in chars] + [c.side_url for c in chars] + [w.icon_url for w in weapons]
                   + [r.icon_url for r in regions] + [r.background_url for r in regions])

    def get_icon(url: str | None, name: str | None, label: str) -> Image.Image | None:
        img = icons.get(url)
        if img is None and enka_icons is not None and name:
            img = enka_icons.get(name)
        if img is None:
            report.missing_icons.append(name or label)
        return img

    def get_quiet(url: str | None, name: str | None) -> Image.Image | None:
        img = icons.get(url)
        if img is None and enka_icons is not None and name:
            img = enka_icons.get(name)
        return img

    sheet = AccountSheet()
    prof.save(work_dir / "profile.png")
    sheet.profile = Piece(image=Path("profile.png"))
    report.wish_lines.extend(wish_lines or [])
    if wish is not None:
        _add_banner_counter(sheet, report, work_dir, wish, all_chars, icons, banner_templates)
    if weapon_wish is not None:
        _add_weapon_banner(sheet, report, work_dir, weapon_wish, banner_templates)
    _add_input_frames(sheet, report, work_dir, options, banner_templates)

    for i, c in enumerate(chars):
        el_icon = None
        if enka_icons is not None and c.element in ELEMENT_ENKA and c.element != "cryo":
            el_icon = enka_icons.get(f"UI_Buff_Element_{ELEMENT_ENKA[c.element]}")
        img = draw_character_card(get_icon(c.icon_url, c.icon_name, c.name), c.rarity, c.element, c.level, el_icon)
        name = f"char_{i:02d}.png"
        img.save(work_dir / name)
        sheet.characters.append(Card(image=Path(name), rarity=c.rarity, character=c.slug, badge=c.constellation))
        cons = f"C{c.constellation}" if c.constellation is not None else "C?"
        report.characters.append(f"{c.name} Lv{c.level or '?'} {cons}")
        if c.slug is None:
            report.unknown_avatars.append(c.avatar_id)

    for i, w in enumerate(weapons):
        side = get_quiet(w.owner_side_url, w.owner_side_name)
        img = draw_weapon_card(get_icon(w.icon_url, icon_stem(w.icon_url), w.name or f"vũ khí {w.weapon_id}"),
                               w.rarity, w.level, w.refinement, side)
        name = f"weapon_{i:02d}.png"
        img.save(work_dir / name)
        sheet.weapons.append(Card(image=Path(name), rarity=w.rarity))
        ref = f"R{w.refinement}" if w.refinement else "R–"
        report.weapons.append(f"{w.name} {w.rarity or '?'}★ {ref} Lv{w.level or '?'}")

    for i, r in enumerate(regions):       # % tăng dần, trên → dưới
        img = draw_exploration_card(r.name, r.permille, icons.get(r.icon_url), icons.get(r.background_url))
        name = f"explore_{i:02d}.png"
        img.save(work_dir / name)
        sheet.explorations.append(Piece(image=Path(name)))
        report.explorations.append(f"{r.name} {format_percent(r.permille)}")

    (work_dir / "sheet.json").write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
    return sheet, report


def build_sheet_from_hoyolab(uid: str | int, work_dir: Path, *, client: HoyolabClient | None = None,
                             cookie: str | None = None, icons: UrlIconCache | None = None,
                             enka_icons: IconCache | None = None, offline_icons: bool = False,
                             on_verification: Callable[[], bool] | None = None,
                             options: RenderOptions | None = None,
                             with_wish: bool = True) -> tuple[AccountSheet, HoyolabReport]:
    """UID → (sheet đã vẽ trong work_dir, báo cáo). `options`: tuỳ chọn render sẽ dùng (để chọn số
    vũ khí khớp số cột bố cục). `with_wish`: thêm ô đếm banner từ lịch sử roll (cần
    GACARD_HOYOLAB_COOKIE_TOKEN; thiếu/lỗi thì bỏ ô và ghi lý do vào báo cáo). Lỗi HoYoLAB → HoyolabError."""
    uid = validate_uid(uid)
    recognize_server(uid)
    if client is None:
        client = HoyolabClient(cookie if cookie is not None else load_cookie(),
                               on_verification=on_verification)
    index_data = client.index(uid)
    list_data = client.character_list(uid)
    root = data_dir()
    icons = icons or UrlIconCache(root / "icons" / "hoyolab", offline=offline_icons)
    ua = os.environ.get("GACARD_ENKA_USER_AGENT") or None
    if enka_icons is None:
        enka_icons = IconCache(root / "icons", **({"user_agent": ua} if ua else {}), offline=offline_icons)
    extras = fetch_enka_extras(uid, offline=offline_icons)   # Cấp Thế Giới: index HoYoLAB không có
    wish, wish_lines = _wish_counter(uid) if with_wish else (None, [])
    return build_sheet_from_data(index_data, list_data, work_dir, icons, enka_icons, extras, options,
                                 wish, wish_lines)


def _wish_counter(uid: str) -> tuple[BannerCounter | None, list[str]]:
    """(ô đếm banner hoặc None, dòng báo cáo). Không bao giờ ném lỗi: ô banner chỉ là phần thêm."""
    try:
        from gacard import wish as wish_mod

        got = wish_mod.fetch_wish_history(uid)
        if got.records is None:
            return None, [f"Bỏ qua ô banner: {got.reason}"]
        counter = wish_mod.banner_counter(got.records)
        lines = [f"Lịch sử roll ({got.source}, {len(got.records)} lượt):",
                 *("  " + t for t in wish_mod.summary_vi(got.records).splitlines())]
        if counter is None:
            lines.append("Bỏ qua ô banner: chưa có lượt roll banner nhân vật nào.")
        return counter, lines
    except Exception as exc:  # noqa: BLE001 — lịch sử roll không được làm hỏng chế độ HoYoLAB
        log.info("Không lấy được lịch sử roll: %s", type(exc).__name__)
        return None, [f"Bỏ qua ô banner: lỗi không rõ khi đọc lịch sử roll ({type(exc).__name__})."]


# ------------------------------------------------------------------ .env / CLI
def _env_file() -> Path:
    if os.environ.get("GACARD_ENV_FILE"):
        return Path(os.environ["GACARD_ENV_FILE"])
    cwd = Path(".env")
    return cwd if cwd.is_file() else PROJECT_ROOT / ".env"


def load_cookie() -> str:
    """GACARD_HOYOLAB_COOKIE từ biến môi trường (ưu tiên) hoặc .env. Thiếu → HoyolabError('no_cookie')."""
    from gacard.bot.config import parse_env_file   # đọc .env thuần, không kéo aiogram

    value = os.environ.get("GACARD_HOYOLAB_COOKIE")
    if not (value or "").strip():
        value = parse_env_file(_env_file()).get("GACARD_HOYOLAB_COOKIE", "")
    parse_cookie(value)
    return value.strip()


def _safe_part(text: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9\-]+", "", (text or "").replace("#", ""))


def default_out_path(uid: str, code: str | None) -> Path:
    from gacard.bot.config import DEFAULT_OUTPUT_DIR

    base = Path((os.environ.get("GACARD_OUTPUT_DIR") or "").strip() or DEFAULT_OUTPUT_DIR)
    parts = [p for p in (_safe_part(code), _safe_part(uid)) if p]
    return base / ("acc-" + "-".join(parts) + "-hoyolab.jpg")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m gacard.hoyolab",
                                description="Ảnh acc từ Sổ Tay Hành Trình HoYoLAB (cookie của chính chủ acc)")
    p.add_argument("uid")
    p.add_argument("--code", help='mã acc, vd 1116 hoặc "CN#1076"')
    g = p.add_mutually_exclusive_group()
    g.add_argument("--bg-file", help="ảnh nền")
    g.add_argument("--bg", help="mã nền hoặc slug nhân vật trong kho nền")
    p.add_argument("--watermark")
    p.add_argument("--frame-color", help="#RRGGBB")
    p.add_argument("--out", help="file JPG đầu ra (mặc định D:\\Claude\\genshin\\output\\acc-<mã>-<uid>-hoyolab.jpg)")
    p.add_argument("--work-dir", help="thư mục ghi các mảnh đã vẽ")
    p.add_argument("--no-wish", action="store_true", help="không lấy lịch sử roll / không vẽ ô đếm banner")
    p.add_argument("--no-open", action="store_true",
                   help="không mở trình duyệt khi HoYoLAB đòi xác minh (chạy không tương tác)")
    args = p.parse_args(argv)

    from gacard.backgrounds import BackgroundError, BackgroundLibrary
    from gacard.render import SheetError, render_sheet

    try:
        uid = validate_uid(args.uid)
        recognize_server(uid)
        cookie = load_cookie()
    except HoyolabError as exc:
        print(f"Lỗi: {exc.message_vi}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else default_out_path(uid, args.code)
    work = Path(args.work_dir) if args.work_dir else data_dir() / "hoyolab-work" / uid
    options = RenderOptions(code=args.code, uid=uid, frame_color=args.frame_color,
                            watermark=args.watermark, background=args.bg)
    try:
        sheet, report = build_sheet_from_hoyolab(
            uid, work, cookie=cookie, on_verification=None if args.no_open else interactive_verification,
            options=options, with_wish=not args.no_wish)
        if args.bg_file:
            data = render_sheet(sheet, work, options, background_file=Path(args.bg_file))
        else:
            data = render_sheet(sheet, work, options, BackgroundLibrary(data_dir() / "backgrounds"),
                                characters=report.account_characters)
    except HoyolabError as exc:
        print(f"Lỗi HoYoLAB ({exc.kind}): {redact(exc.message_vi, cookie)}", file=sys.stderr)
        return 1
    except (SheetError, BackgroundError) as exc:
        print(f"Lỗi: {exc.message_vi}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Lỗi: {redact(str(exc), cookie)}", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(report.text_vi())
    print(f"Đã lưu ảnh: {out} ({len(data) / 1024:.0f} KB); mảnh trong {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
