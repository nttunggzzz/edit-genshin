"""Lịch sử roll (wish) Genshin của CHÍNH chủ acc qua cookie_token HoYoLAB, rồi tính pity cho ô đếm
banner "‹ N ›" trong ảnh acc. Theo .claude/skills/hoyolab-wish-history/SKILL.md.

Chuỗi: GACARD_HOYOLAB_COOKIE_TOKEN (.env) → genAuthKeyByCookieToken (authkey chỉ-đọc-lịch-sử-roll, sống
~1 ngày) → getGachaLog phân trang (gacha_type 301 gồm cả 400, 302, 200, 500) → gộp/khử trùng theo id vào
data/acc-card/wish/<uid>.json (git-ignored) → cache 30 phút.

An toàn: cookie_token và authkey chỉ nằm trong bộ nhớ; KHÔNG in/log/ghi file (file lưu chỉ có id, loại,
tên vật phẩm, độ hiếm, thời gian). Lỗi (thiếu cookie_token, UID không thuộc cookie, retcode, rate-limit,
captcha) → trả (None, lý do tiếng Việt), DỪNG, không thử lại. Chỉ ĐỌC lịch sử.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gacard import hoyolab
from gacard.hoyolab import HoyolabError, redact
from gacard.uidmode import data_dir

log = logging.getLogger(__name__)

AUTHKEY_URL = "https://api-account-os.hoyoverse.com/binding/api/genAuthKeyByCookieToken"
LOG_URL = "https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/getGachaLog"
GAME_BIZ = "hk4e_global"
GACHA_TYPES = (301, 302, 200, 500)        # 301 = nhân vật (gồm 400), 302 = vũ khí, 200 = thường, 500 = biên niên
CHARACTER_BANNER = 301
HARD_PITY = {301: 90, 302: 80, 200: 90, 500: 90}
BANNER_NAMES = {301: "Banner nhân vật", 302: "Banner vũ khí", 200: "Banner thường", 500: "Banner biên niên"}
PAGE_SIZE = 20
PAGE_DELAY = 0.5                          # giây giữa hai trang (tránh bị giới hạn)
CACHE_TTL = 30 * 60                       # giây
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEOUT = 15.0
# 5★ nhân vật vào được banner thường (thua 50/50 → ra các nhân vật này). Tên tiếng Anh như API (lang=en).
STANDARD_5 = frozenset({"Diluc", "Jean", "Mona", "Qiqi", "Keqing", "Tighnari", "Dehya", "Yumemizuki Mizuki"})
RECORD_KEYS = ("id", "gacha_type", "bucket", "name", "item_type", "rank_type", "time")

TOKEN_ENV = "GACARD_HOYOLAB_COOKIE_TOKEN"
REASONS = {
    "no_token": ("Chưa có GACARD_HOYOLAB_COOKIE_TOKEN trong .env nên không lấy được lịch sử roll. Cách lấy: "
                 "đăng nhập hoyolab.com → F12 → Application/Storage → Cookies → https://www.hoyolab.com → "
                 "chép chuỗi cookie có cookie_token_v2 (và account_id_v2) vào dòng "
                 "GACARD_HOYOLAB_COOKIE_TOKEN=... trong .env. Không gửi cookie cho ai."),
    "bad_token": ("GACARD_HOYOLAB_COOKIE_TOKEN không có cookie_token (cần cookie_token_v2 hoặc cookie_token) "
                  "nên không lấy được lịch sử roll."),
    "network": "Không kết nối được máy chủ lịch sử roll (mạng lỗi hoặc quá thời gian).",
    "bad_response": "Máy chủ lịch sử roll trả dữ liệu lạ, không đọc được.",
    "no_uid": "UID không hợp lệ hoặc là máy chủ không hỗ trợ lịch sử roll.",
}


@dataclass
class WishFetch:
    """Kết quả lấy lịch sử: records=None nghĩa là không có (xem `reason`, tiếng Việt)."""
    records: list[dict] | None = None
    reason: str | None = None
    source: str = ""                      # "cache 12 phút" | "HoYoLAB" | ""
    new_count: int = 0


@dataclass
class PityInfo:
    bucket: int
    total: int = 0                        # tổng lượt roll trong banner
    pity: int = 0                         # số lượt từ sau lần 5★ gần nhất
    last5_name: str | None = None
    last5_time: str | None = None
    prev5_name: str | None = None
    guaranteed: bool | None = None        # banner nhân vật: lần 5★ tới chắc chắn rate-up? (None = không biết)

    @property
    def hard_pity(self) -> int:
        return HARD_PITY.get(self.bucket, 90)


@dataclass
class BannerCounter:
    """Dữ liệu ô đếm banner: số N, pity, 5★ gần nhất ở banner nhân vật."""
    n: int
    pity: int
    total: int
    last5_name: str | None = None
    last5_time: str | None = None
    limited: bool | None = None           # 5★ gần nhất là nhân vật giới hạn? None = chưa có 5★


# ------------------------------------------------------------------ tính toán thuần (không mạng)
def counter_number(pity: int) -> int:
    """Quy tắc chủ dự án cho số trong ô: N = max(1, pity // 5), pity = pity banner nhân vật (301)."""
    return max(1, int(pity) // 5)


def is_limited_5(name: str | None) -> bool:
    return bool(name) and name not in STANDARD_5


def record_bucket(rec: dict) -> int:
    """Banner (301/302/200/500) của một dòng; 400 gộp vào 301."""
    try:
        b = int(rec.get("bucket") or rec.get("gacha_type"))
    except (TypeError, ValueError):
        return 0
    return CHARACTER_BANNER if b == 400 else b


def _id_key(rec: dict) -> int:
    try:
        return int(rec.get("id"))
    except (TypeError, ValueError):
        return 0


def compute_pity(records: list[dict]) -> dict[int, PityInfo]:
    """Pity riêng từng banner (không cộng dồn giữa các banner). Dòng cũ → mới theo id."""
    out = {b: PityInfo(b) for b in GACHA_TYPES}
    for rec in sorted(records, key=_id_key):
        b = record_bucket(rec)
        info = out.get(b)
        if info is None:
            continue
        info.total += 1
        info.pity += 1
        if str(rec.get("rank_type")) == "5":
            info.prev5_name = info.last5_name
            info.last5_name = rec.get("name")
            info.last5_time = rec.get("time")
            info.pity = 0
    char = out[CHARACTER_BANNER]
    if char.last5_name:
        char.guaranteed = not is_limited_5(char.last5_name)
    return out


def banner_counter(records: list[dict]) -> BannerCounter | None:
    """Ô đếm từ pity banner nhân vật (301). Chưa roll banner nhân vật lần nào → None (không có ô)."""
    info = compute_pity(records)[CHARACTER_BANNER]
    if info.total == 0:
        return None
    return BannerCounter(n=counter_number(info.pity), pity=info.pity, total=info.total,
                         last5_name=info.last5_name, last5_time=info.last5_time,
                         limited=is_limited_5(info.last5_name) if info.last5_name else None)


# ------------------------------------------------------------------ lưu file
def wish_dir() -> Path:
    return data_dir() / "wish"


def _wish_path(uid: str, directory: Path | None) -> Path:
    return (directory or wish_dir()) / f"{uid}.json"


def _clean(rec: dict) -> dict:
    """Chỉ giữ trường lịch sử, không bao giờ giữ thứ gì khác (uid, authkey, ...)."""
    return {k: rec.get(k) for k in RECORD_KEYS}


def load_stored(uid: str, directory: Path | None = None) -> tuple[list[dict], float | None]:
    """(records đã lưu, thời điểm lấy gần nhất theo epoch). Không có/hỏng → ([], None)."""
    p = _wish_path(uid, directory)
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
        records = [_clean(r) for r in blob.get("list", []) if isinstance(r, dict) and r.get("id")]
        fetched = blob.get("fetched_at")
        return records, float(fetched) if fetched is not None else p.stat().st_mtime
    except (OSError, ValueError, AttributeError, TypeError):
        return [], None


def save_stored(uid: str, records: list[dict], directory: Path | None = None, *, now: float | None = None) -> None:
    """Ghi nguyên tử, giữ bản .bak của file trước. Không chứa bí mật."""
    p = _wish_path(uid, directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = {"uid": uid, "fetched_at": time.time() if now is None else now,
            "list": sorted((_clean(r) for r in records), key=_id_key)}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=0), encoding="utf-8")
    if p.is_file():
        try:
            p.replace(p.with_suffix(".json.bak"))
        except OSError:
            pass
    tmp.replace(p)


def merge_records(old: list[dict], new: list[dict]) -> list[dict]:
    """Hợp nhất, khử trùng theo id (bản mới ghi đè), sắp cũ → mới."""
    by_id = {str(r["id"]): _clean(r) for r in old if r.get("id")}
    for r in new:
        if r.get("id"):
            by_id[str(r["id"])] = _clean(r)
    return sorted(by_id.values(), key=_id_key)


# ------------------------------------------------------------------ mạng
def load_cookie_token() -> str | None:
    """GACARD_HOYOLAB_COOKIE_TOKEN từ biến môi trường (ưu tiên) hoặc .env; thiếu → None. Không in giá trị."""
    from gacard.bot.config import parse_env_file

    value = (os.environ.get(TOKEN_ENV) or "").strip()
    if not value:
        value = (parse_env_file(hoyolab._env_file()).get(TOKEN_ENV) or "").strip()
    return value or None


def _has_cookie_token(cookie: str) -> bool:
    if "\r" in cookie or "\n" in cookie:
        return False
    jar = {}
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        jar[k.strip()] = v.strip()
    return bool(jar.get("cookie_token_v2") or jar.get("cookie_token"))


class _Stop(Exception):
    """Dừng ngay (không thử lại), `reason` là câu tiếng Việt cho người dùng."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _call(transport: Callable, method: str, url: str, headers: dict, body: bytes | None, secret: str) -> dict:
    try:
        status, raw = transport(method, url, headers, body, TIMEOUT)
    except HoyolabError as exc:
        raise _Stop(redact(exc.message_vi, secret)) from None
    except Exception as exc:  # noqa: BLE001 — không để lỗi lạ kéo header/URL (có authkey) ra ngoài
        raise _Stop(f"{REASONS['network']} ({type(exc).__name__})") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _Stop(REASONS["bad_response"])
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _Stop(f"{REASONS['network']} (HTTP {status})" if status != 200 else REASONS["bad_response"]) from None
    if not isinstance(doc, dict) or "retcode" not in doc:
        raise _Stop(REASONS["bad_response"])
    return doc


def _check_retcode(doc: dict, step: str, secret: str) -> dict:
    try:
        retcode = int(doc.get("retcode"))
    except (TypeError, ValueError):
        raise _Stop(REASONS["bad_response"]) from None
    if retcode != 0:
        msg = redact(str(doc.get("message") or "")[:120], secret)
        low = msg.lower()
        if any(w in low for w in ("geetest", "captcha", "verif")):
            raise _Stop("HoYoLAB yêu cầu xác minh (captcha) khi lấy lịch sử roll. Tự xác minh trên trình duyệt "
                        "của bạn rồi chạy lại.")
        if step == "authkey":
            raise _Stop(f"HoYoLAB không cấp authkey lịch sử roll (retcode {retcode}): cookie_token hết hạn/sai, "
                        f"hoặc UID không thuộc tài khoản của cookie này. Lấy lại cookie_token mới.")
        if retcode == -101:
            raise _Stop("Authkey lịch sử roll hết hạn (retcode -101). Chạy lại để xin authkey mới.")
        if retcode == -110:
            raise _Stop("Máy chủ lịch sử roll đang giới hạn lượt gọi (retcode -110). Đợi một lúc rồi chạy lại.")
        raise _Stop(f"Máy chủ lịch sử roll báo lỗi (retcode {retcode}): {msg}")
    data = doc.get("data")
    if not isinstance(data, dict):
        raise _Stop(REASONS["bad_response"])
    return data


def _fetch_remote(uid: str, server: str, cookie: str, transport: Callable, stored_ids: set[str],
                  sleep: Callable[[float], None]) -> list[dict]:
    """Chuỗi đầy đủ; ném _Stop ở lỗi đầu tiên. Trang mới → cũ; gặp id đã lưu thì dừng banner đó
    (mọi dòng cũ hơn đã có)."""
    body = json.dumps({"game_uid": uid, "region": server, "game_biz": GAME_BIZ,
                       "auth_appid": "webview_gacha"}, separators=(",", ":")).encode()
    headers = {"Cookie": cookie, "Content-Type": "application/json", "User-Agent": hoyolab.USER_AGENT,
               "Origin": hoyolab.ORIGIN, "Referer": hoyolab.ORIGIN + "/"}
    data = _check_retcode(_call(transport, "POST", AUTHKEY_URL, headers, body, cookie), "authkey", cookie)
    authkey = urllib.parse.unquote(str(data.get("authkey") or ""))
    if not authkey:
        raise _Stop(REASONS["bad_response"])
    rows: dict[str, dict] = {}
    try:
        for gt in GACHA_TYPES:
            end_id = "0"
            while True:
                q = urllib.parse.urlencode({"authkey_ver": 1, "authkey": authkey, "lang": "en",
                                            "game_biz": GAME_BIZ, "gacha_type": gt, "real_gacha_type": gt,
                                            "size": PAGE_SIZE, "end_id": end_id})
                doc = _call(transport, "GET", f"{LOG_URL}?{q}", {"User-Agent": hoyolab.USER_AGENT}, None, authkey)
                page = _check_retcode(doc, "log", authkey).get("list")
                if not isinstance(page, list):
                    raise _Stop(REASONS["bad_response"])
                if not page:
                    break
                hit = False
                for x in page:
                    if not isinstance(x, dict) or not x.get("id"):
                        raise _Stop(REASONS["bad_response"])
                    if str(x.get("uid")) != uid:
                        raise _Stop("Lịch sử roll trả về UID khác UID yêu cầu; dừng, không lưu gì.")
                    rows[str(x["id"])] = _clean({**x, "bucket": gt})
                    hit = hit or str(x["id"]) in stored_ids
                end_id = str(page[-1]["id"])
                if hit or len(page) < PAGE_SIZE:
                    break
                sleep(PAGE_DELAY)
    finally:
        authkey = ""          # xoá khỏi bộ nhớ tạm của tool
    return list(rows.values())


def fetch_wish_history(uid: str | int, server: str | None = None, *, cookie_token: str | None = None,
                       directory: Path | None = None, transport: Callable | None = None,
                       cache_ttl: int = CACHE_TTL, now: Callable[[], float] = time.time,
                       sleep: Callable[[float], None] = time.sleep) -> WishFetch:
    """Lịch sử roll của UID. Cache còn hạn (30 phút) → dùng file đã lưu, không gọi mạng. Lỗi → records=None
    + lý do tiếng Việt (không thử lại). Thành công → gộp vào data/acc-card/wish/<uid>.json."""
    try:
        uid = hoyolab.validate_uid(uid)
        server = server or hoyolab.recognize_server(uid)
    except HoyolabError as exc:
        return WishFetch(reason=exc.message_vi)
    stored, fetched_at = load_stored(uid, directory)
    if stored and fetched_at is not None and 0 <= now() - fetched_at <= cache_ttl:
        return WishFetch(stored, source=f"cache {max(0, round((now() - fetched_at) / 60))} phút")
    cookie = cookie_token if cookie_token is not None else load_cookie_token()
    if not cookie:
        return WishFetch(reason=REASONS["no_token"])
    if not _has_cookie_token(cookie):
        return WishFetch(reason=REASONS["bad_token"])
    try:
        fresh = _fetch_remote(uid, server, cookie, transport or hoyolab.urllib_transport,
                              {str(r["id"]) for r in stored}, sleep)
    except _Stop as exc:
        return WishFetch(reason=redact(exc.reason, cookie))
    finally:
        cookie = ""
    merged = merge_records(stored, fresh)
    try:
        save_stored(uid, merged, directory, now=now())
    except OSError as exc:
        log.info("Không ghi được lịch sử roll: %s", type(exc).__name__)
    old_ids = {str(r["id"]) for r in stored}
    return WishFetch(merged, source="HoYoLAB", new_count=sum(1 for r in fresh if str(r["id"]) not in old_ids))


def summary_vi(records: list[dict]) -> str:
    """Bảng pity ngắn (mỗi banner một dòng) cho báo cáo. Không chứa dữ liệu phiên."""
    lines = []
    for info in compute_pity(records).values():
        if not info.total:
            continue
        last = f"; 5★ gần nhất {info.last5_name} ({info.last5_time})" if info.last5_name else "; chưa có 5★"
        ins = ""
        if info.guaranteed is not None:
            ins = ", bảo hiểm" if info.guaranteed else ", không bảo hiểm"
        lines.append(f"{BANNER_NAMES[info.bucket]}: pity {info.pity}, còn {info.hard_pity - info.pity} tới "
                     f"hard pity {info.hard_pity}{ins}, {info.total} lượt{last}")
    return "\n".join(lines)

