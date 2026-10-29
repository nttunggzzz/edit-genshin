"""Tra cứu Enka.Network theo UID: AR, tên, cung mệnh (constellation) của nhân vật trong Tủ trưng bày.

Tài liệu API: https://github.com/EnkaNetwork/API-docs (api.md, docs/gi/api.md).
- GET https://enka.network/api/uid/{uid}/  -> playerInfo + avatarInfoList + ttl. Bắt buộc User-Agent mô tả riêng.
- Mã lỗi: 400 sai định dạng UID, 404 không tồn tại, 424 bảo trì/sau bản cập nhật, 429 rate limit, 500/503 lỗi máy chủ.
- "ttl" = số giây tới lần Enka lấy lại Tủ trưng bày; trong lúc đó Enka trả dữ liệu cache -> ta cache theo ttl.

Giới hạn: chỉ nhân vật đang đặt trong Tủ trưng bày mới có mặt; cung mệnh chỉ có khi chủ acc bật hiển thị chi tiết.

CLI:  python -m gacard.enka <uid>
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from pydantic import BaseModel

from . import characters

DEFAULT_BASE_URL = "https://enka.network"
DEFAULT_TTL = 60
UID_RE = re.compile(r"[0-9]{9,10}")

MESSAGES_VI = {
    "invalid_uid": "UID không hợp lệ. UID Genshin gồm 9 hoặc 10 chữ số.",
    "not_found": "Không tìm thấy người chơi với UID này (UID không tồn tại).",
    "maintenance": "Enka.Network đang bảo trì hoặc game vừa cập nhật nên chưa tra được. Thử lại sau ít phút.",
    "rate_limited": "Enka.Network đang giới hạn số lượt tra (quá nhanh). Đợi một lát rồi thử lại.",
    "server": "Máy chủ Enka.Network đang gặp lỗi. Thử lại sau ít phút.",
    "network": "Không kết nối được tới Enka.Network (mạng lỗi hoặc quá thời gian chờ).",
    "bad_response": "Enka.Network trả về dữ liệu không đọc được.",
}


class EnkaError(Exception):
    def __init__(self, kind: str, message_vi: str | None = None):
        self.kind = kind
        self.message_vi = message_vi or MESSAGES_VI.get(kind, "Lỗi khi tra Enka.Network.")
        super().__init__(f"{kind}: {self.message_vi}")


class EnkaAvatar(BaseModel):
    avatar_id: int
    character: str | None = None  # slug, tra từ bảng nhân vật
    level: int | None = None
    constellation: int | None = None  # len(talentIdList); None nếu Enka không trả chi tiết


class EnkaProfile(BaseModel):
    uid: str
    nickname: str | None = None
    adventure_rank: int | None = None
    world_level: int | None = None
    avatars: list[EnkaAvatar] = []
    fetched_at: datetime


def _to_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _detail_level(detail: dict) -> int | None:
    prop = (detail.get("propMap") or {}).get("4001") or {}
    return _to_int(prop.get("val", prop.get("ival")))


def validate_uid(uid: str | int) -> str:
    s = str(uid).strip()
    if not UID_RE.fullmatch(s):
        raise EnkaError("invalid_uid")
    return s


def parse_profile(raw: dict, uid: str, fetched_at: datetime | None = None) -> EnkaProfile:
    """Chuyển JSON Enka thành EnkaProfile (hàm thuần, không mạng).

    Gộp showAvatarInfoList (chỉ level; đúng thứ tự trưng bày) với avatarInfoList (chi tiết).
    Cung mệnh: len(talentIdList) khi có chi tiết (thiếu talentIdList = 0 cung); nếu không có chi tiết nhưng
    playerInfo.isShowAvatarTalent = true thì dùng talentLevel của mục trưng bày (thiếu = 0); còn lại None.
    """
    info = raw.get("playerInfo")
    if not isinstance(info, dict):
        raise EnkaError("bad_response")
    details: dict[int, dict] = {}
    for d in raw.get("avatarInfoList") or []:
        aid = _to_int(d.get("avatarId"))
        if aid is not None:
            details[aid] = d
    show_talent = bool(info.get("isShowAvatarTalent"))

    avatars: list[EnkaAvatar] = []
    seen: set[int] = set()

    def make(aid: int, show: dict | None, detail: dict | None) -> EnkaAvatar:
        level = _to_int(show.get("level")) if show else None
        if level is None and detail is not None:
            level = _detail_level(detail)
        cons: int | None = None
        if detail is not None:
            cons = len(detail.get("talentIdList") or [])
        elif show is not None and show_talent:
            cons = _to_int(show.get("talentLevel")) or 0
        char = characters.by_avatar_id(aid)
        return EnkaAvatar(avatar_id=aid, character=char.slug if char else None, level=level, constellation=cons)

    for s in info.get("showAvatarInfoList") or []:
        aid = _to_int(s.get("avatarId"))
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        avatars.append(make(aid, s, details.get(aid)))
    for aid, d in details.items():  # có chi tiết nhưng không nằm trong danh sách trưng bày (hiếm)
        if aid not in seen:
            seen.add(aid)
            avatars.append(make(aid, None, d))

    return EnkaProfile(
        uid=str(uid),
        nickname=info.get("nickname"),
        adventure_rank=_to_int(info.get("level")),
        world_level=_to_int(info.get("worldLevel")),
        avatars=avatars,
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )


class EnkaClient:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        timeout: float = 10.0,
        *,
        base_url: str = DEFAULT_BASE_URL,
        stale_max_age: float = 6 * 3600,
    ):
        if not user_agent or not user_agent.strip():
            raise ValueError("Enka yêu cầu User-Agent mô tả riêng (đặt GACARD_ENKA_USER_AGENT).")
        self.cache_dir = Path(cache_dir)
        self.user_agent = user_agent.strip()
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        # Khi Enka lỗi tạm thời (bảo trì/rate limit/mạng/5xx), dùng bản cache cũ không quá chừng này giây.
        self.stale_max_age = stale_max_age
        self._locks: dict[str, asyncio.Lock] = {}

    # --- cache trên đĩa: {"fetched_at": iso, "ttl": giây, "raw": <JSON Enka>}
    def _cache_path(self, uid: str) -> Path:
        return self.cache_dir / f"{uid}.json"

    def _read_cache(self, uid: str) -> tuple[dict, datetime, float] | None:
        try:
            blob = json.loads(self._cache_path(uid).read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(blob["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            ttl = float(blob.get("ttl", DEFAULT_TTL))
            raw = blob["raw"]
            if not isinstance(raw, dict):
                return None
            return raw, fetched, ttl
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write_cache(self, uid: str, raw: dict, fetched: datetime, ttl: float) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path(uid).with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"fetched_at": fetched.isoformat(), "ttl": ttl, "raw": raw}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._cache_path(uid))
        except OSError:
            pass  # cache hỏng không được làm hỏng luồng chính

    async def _request(self, uid: str) -> dict:
        url = f"{self.base_url}/api/uid/{uid}/"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.get(url, headers=headers) as resp:
                    status = resp.status
                    if status == 200:
                        try:
                            data = await resp.json(content_type=None)
                        except (ValueError, aiohttp.ContentTypeError) as exc:
                            raise EnkaError("bad_response") from exc
                        if not isinstance(data, dict):
                            raise EnkaError("bad_response")
                        return data
        except EnkaError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise EnkaError("network") from exc
        kinds = {400: "invalid_uid", 404: "not_found", 424: "maintenance", 429: "rate_limited"}
        raise EnkaError(kinds.get(status, "server"))

    async def fetch(self, uid: str) -> EnkaProfile:
        uid = validate_uid(uid)
        lock = self._locks.setdefault(uid, asyncio.Lock())
        async with lock:
            now = datetime.now(timezone.utc)
            cached = self._read_cache(uid)
            if cached:
                raw, fetched, ttl = cached
                if (now - fetched).total_seconds() < ttl:
                    return parse_profile(raw, uid, fetched)
            try:
                raw = await self._request(uid)
            except EnkaError as exc:
                transient = exc.kind in {"maintenance", "rate_limited", "network", "server"}
                if cached and transient and (now - cached[1]).total_seconds() <= self.stale_max_age:
                    return parse_profile(cached[0], uid, cached[1])
                raise
            fetched = datetime.now(timezone.utc)
            ttl = float(raw["ttl"]) if _to_int(raw.get("ttl")) is not None else float(DEFAULT_TTL)
            profile = parse_profile(raw, uid, fetched)  # parse trước khi ghi cache: dữ liệu hỏng không vào cache
            self._write_cache(uid, raw, fetched, ttl)
            return profile


def fetch_sync(client: EnkaClient, uid: str) -> EnkaProfile:
    """Tiện ích cho CLI/script (không dùng trong event loop đang chạy)."""
    return asyncio.run(client.fetch(uid))


# ----------------------------------------------------------------------- CLI
def format_summary_vi(profile: EnkaProfile) -> str:
    lines = [
        f"UID {profile.uid}: {profile.nickname or '(không rõ tên)'}",
        f"AR {profile.adventure_rank if profile.adventure_rank is not None else '?'}"
        f" | Thế giới cấp {profile.world_level if profile.world_level is not None else '?'}",
    ]
    if not profile.avatars:
        lines.append("Tủ trưng bày trống hoặc chủ acc đã đóng - không lấy được nhân vật nào.")
    else:
        lines.append(f"Nhân vật trong Tủ trưng bày ({len(profile.avatars)}):")
        for a in profile.avatars:
            name = characters.display_name(a.character) if a.character else f"(chưa biết id {a.avatar_id})"
            cons = f"C{a.constellation}" if a.constellation is not None else "không có cung mệnh"
            lvl = f"Lv{a.level}" if a.level is not None else "Lv?"
            lines.append(f"  - {name}: {lvl}, {cons}")
        missing = sum(1 for a in profile.avatars if a.constellation is None)
        if missing:
            lines.append(f"{missing} nhân vật chưa có cung mệnh (chủ acc chưa bật hiện chi tiết) - cần điền tay.")
    lines.append(f"Lấy lúc: {profile.fetched_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Cách dùng: python -m gacard.enka <uid>")
        return 2
    data_dir = Path(os.environ.get("GACARD_DATA_DIR") or Path(__file__).resolve().parents[2] / "data" / "acc-card")
    ua = os.environ.get("GACARD_ENKA_USER_AGENT") or "gacard-dev/0.1 (personal Telegram showcase bot)"
    client = EnkaClient(data_dir / "enka-cache", ua)
    try:
        profile = fetch_sync(client, args[0])
    except EnkaError as exc:
        print(f"Lỗi ({exc.kind}): {exc.message_vi}")
        return 1
    print(format_summary_vi(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
