"""Danh sách chat được dùng bot: GACARD_ALLOWED_CHAT_IDS (.env) gộp với data/acc-card/allowed.json.

allowed.json do lệnh /moi, /bomoi của admin ghi:
    {"added": [chat_id...], "removed": [chat_id...], "usernames": {"ten": null | chat_id}}
- "added": chat ID được mời (kể cả chat ID nhận diện được từ lời mời theo username).
- "removed" cho phép /bomoi cả ID ghi trong .env mà không phải sửa .env. Admin luôn được dùng bot.
- "usernames": lời mời theo @username (chữ thường, không @). Bot không tra được @username → chat ID,
  nên giá trị là null cho tới khi người đó nhắn bot lần đầu; lúc đó chat ID được ghi vào "added"
  và gắn vào username (để /danhsach hiện, /bomoi @ten gỡ được cả chat ID).
File bị sửa tay khi bot đang chạy cũng được đọc lại (so mtime), nên kiểm tra quyền luôn theo dữ liệu mới nhất.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Iterable

log = logging.getLogger("gacard.bot.access")

# Quy tắc username Telegram: 5–32 ký tự chữ/số/gạch dưới, cho phép @ ở đầu.
USERNAME_RE = re.compile(r"^@?([A-Za-z0-9_]{5,32})$")


def normalize_username(raw: str | None) -> str | None:
    """'@Ten_Abc' → 'ten_abc'; sai quy tắc → None."""
    m = USERNAME_RE.match((raw or "").strip())
    return m.group(1).lower() if m else None


class AllowList:
    def __init__(self, path: Path, env_ids: Iterable[int] = (), admin_ids: Iterable[int] = ()):
        self.path = Path(path)
        self.env_ids = frozenset(env_ids)
        self.admin_ids = frozenset(admin_ids)
        self._added: set[int] = set()
        self._removed: set[int] = set()
        self._usernames: dict[str, int | None] = {}
        self._mtime: float | None = None
        self._reload(force=True)

    # ------------------------------------------------------------------ đọc / ghi file

    def _stat(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def _reload(self, force: bool = False) -> None:
        mtime = self._stat()
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        if mtime is None:
            self._added, self._removed, self._usernames = set(), set(), {}
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._added = {int(x) for x in data.get("added", [])}
            self._removed = {int(x) for x in data.get("removed", [])}
            self._usernames = self._parse_usernames(data.get("usernames", {}))
        except (OSError, ValueError, TypeError, AttributeError):
            log.exception("allowed.json hỏng, bỏ qua (chỉ dùng ID trong .env): %s", self.path)
            self._added, self._removed, self._usernames = set(), set(), {}

    @staticmethod
    def _parse_usernames(raw: object) -> dict[str, int | None]:
        """Nhận cả dạng {"ten": id|null} lẫn danh sách ["ten", ...] (sửa tay)."""
        items = raw.items() if isinstance(raw, dict) else ((x, None) for x in raw)  # type: ignore[union-attr]
        out: dict[str, int | None] = {}
        for name, cid in items:
            norm = normalize_username(str(name))
            if norm is None:
                log.warning("Bỏ qua username sai quy tắc trong allowed.json: %r", name)
                continue
            out[norm] = None if cid is None else int(cid)
        return out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        data = {
            "added": sorted(self._added),
            "removed": sorted(self._removed),
            "usernames": dict(sorted(self._usernames.items())),
        }
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self._stat()

    # ------------------------------------------------------------------ truy vấn

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self.admin_ids

    def ids(self) -> frozenset[int]:
        """Danh sách hiệu lực: (.env ∪ added) − removed, luôn gồm admin."""
        self._reload()
        return frozenset((self.env_ids | self._added) - self._removed) | self.admin_ids

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.ids()

    def usernames(self) -> dict[str, int | None]:
        """{username: chat_id đã nhận diện hoặc None (chưa nhắn bot)}."""
        self._reload()
        return dict(self._usernames)

    def username_of(self, chat_id: int) -> str | None:
        self._reload()
        return next((u for u, cid in sorted(self._usernames.items()) if cid == chat_id), None)

    def source(self, chat_id: int) -> str:
        if chat_id in self.admin_ids:
            return "admin"
        if chat_id in self.env_ids:
            return ".env"
        name = self.username_of(chat_id)
        return f"/moi @{name}" if name else "/moi"

    # ------------------------------------------------------------------ sửa

    def add(self, chat_id: int) -> bool:
        """True nếu trước đó chưa được phép."""
        self._reload()
        was = self.is_allowed(chat_id)
        self._removed.discard(chat_id)
        if chat_id not in self.env_ids:
            self._added.add(chat_id)
        self._save()
        return not was

    def remove(self, chat_id: int) -> bool:
        """True nếu trước đó đang được phép. Admin không bỏ được (kiểm tra ở ngoài).

        Username đã gắn với chat này cũng bị gỡ, nếu không người đó nhắn lại sẽ tự được nhận diện lại."""
        self._reload()
        was = self.is_allowed(chat_id)
        self._added.discard(chat_id)
        if chat_id in self.env_ids:
            self._removed.add(chat_id)
        for name in [u for u, cid in self._usernames.items() if cid == chat_id]:
            del self._usernames[name]
        self._save()
        return was

    def add_username(self, username: str) -> bool:
        """True nếu username chưa có trong danh sách mời. `username` đã chuẩn hoá (normalize_username)."""
        self._reload()
        if username in self._usernames:
            return False
        self._usernames[username] = None
        self._save()
        return True

    def remove_username(self, username: str) -> tuple[bool, int | None]:
        """Gỡ lời mời theo username. Trả (có trong danh sách không, chat_id đã gắn và vừa bị bỏ quyền)."""
        self._reload()
        if username not in self._usernames:
            return False, None
        cid = self._usernames.pop(username)
        if cid is not None:
            self.remove(cid)  # tự _save
        else:
            self._save()
        return True, cid

    def claim(self, chat_id: int, username: str | None) -> bool:
        """Người có username trong danh sách mời nhắn bot: ghi chat ID của họ vào "added".

        True chỉ ở lần đầu nhận diện (để báo admin đúng một lần)."""
        name = normalize_username(username)
        if name is None:
            return False
        self._reload()
        if name not in self._usernames or self._usernames[name] is not None:
            return False
        self._usernames[name] = chat_id
        self._removed.discard(chat_id)
        if chat_id not in self.env_ids:
            self._added.add(chat_id)
        self._save()
        return True
