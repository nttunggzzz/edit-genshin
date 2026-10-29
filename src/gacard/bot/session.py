"""Phiên theo chat (trong bộ nhớ + thư mục trên đĩa), debounce album, khoá một phiên bản chạy."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("gacard.bot.session")


@dataclass
class MediaItem:
    message_id: int
    path: Path
    width: int
    height: int
    is_document: bool

    @property
    def long_side(self) -> int:
        return max(self.width, self.height)


@dataclass
class RenderState:
    """Giữ kết quả lần render gần nhất để nút 'Đổi nền' không phải cắt lại ảnh."""

    sheet: Any
    work_dir: Path
    choices: list  # list[BgChoice]
    index: int = 0
    notes: list[str] = field(default_factory=list)
    account: list[str] = field(default_factory=list)  # mọi nhân vật biết được của acc (nút Đổi nền chọn trong đây)


@dataclass
class Session:
    chat_id: int
    dir: Path
    created_at: float
    last_activity: float
    items: list[MediaItem] = field(default_factory=list)
    inflight: int = 0
    notes: list[str] = field(default_factory=list)  # cảnh báo gom lại cho tin tóm tắt kế tiếp
    code: str | None = None
    uid: str | None = None
    bg_override: str | None = None  # slug nhân vật do /nen chọn
    manual_cm: dict[str, int] = field(default_factory=dict)
    enka_profile: Any = None
    enka_error: str | None = None
    enka_task: "asyncio.Task | None" = None
    render: RenderState | None = None
    rendered: bool = False
    busy: bool = False
    mode: str = "chup"  # "chup" = ảnh chụp (/xong), "uid" = Enka (/uid), "hoyolab" = Sổ Tay Hành Trình (/ghep)

    def image_paths(self) -> list[Path]:
        return [m.path for m in sorted(self.items, key=lambda m: m.message_id)]

    def slots_used(self) -> int:
        return len(self.items) + self.inflight

    @property
    def work_dir(self) -> Path:
        return self.dir / "work"

    @property
    def uid_work_dir(self) -> Path:
        return self.dir / "uid-work"

    @property
    def hoyolab_work_dir(self) -> Path:
        return self.dir / "hoyolab-work"


class SessionManager:
    """Quản lý phiên; đồng hồ và 'bây giờ' được tiêm vào để test không phải chờ."""

    def __init__(
        self,
        sessions_dir: Path,
        ttl: float = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.sessions_dir = Path(sessions_dir)
        self.ttl = ttl
        self.clock = clock
        self.now = now
        self._sessions: dict[int, Session] = {}

    def get(self, chat_id: int) -> Session | None:
        """Phiên còn hạn của chat, không tạo mới. Phiên quá hạn bị dọn và trả None."""
        s = self._sessions.get(chat_id)
        if s is not None and self._expired(s):
            self.drop(chat_id)
            return None
        return s

    def get_or_create(self, chat_id: int) -> Session:
        s = self.get(chat_id)
        if s is None:
            stamp = self.now().strftime("%Y%m%d-%H%M%S")
            path = self.sessions_dir / f"{chat_id}-{stamp}"
            n = 1
            while path.exists():  # hai phiên tạo trong cùng một giây
                n += 1
                path = self.sessions_dir / f"{chat_id}-{stamp}-{n}"
            path.mkdir(parents=True, exist_ok=True)
            t = self.clock()
            s = Session(chat_id=chat_id, dir=path, created_at=t, last_activity=t)
            self._sessions[chat_id] = s
        return s

    def touch(self, chat_id: int) -> None:
        s = self._sessions.get(chat_id)
        if s is not None:
            s.last_activity = self.clock()

    def drop(self, chat_id: int) -> bool:
        s = self._sessions.pop(chat_id, None)
        if s is None:
            return False
        if s.enka_task is not None and not s.enka_task.done():
            s.enka_task.cancel()
        shutil.rmtree(s.dir, ignore_errors=True)
        return True

    def _expired(self, s: Session) -> bool:
        return not s.busy and self.clock() - s.last_activity > self.ttl

    def expire_idle(self) -> list[int]:
        """Xoá mọi phiên quá hạn; trả danh sách chat_id đã bị xoá."""
        gone = [cid for cid, s in self._sessions.items() if self._expired(s)]
        for cid in gone:
            self.drop(cid)
        return gone

    def active_chats(self) -> list[int]:
        return list(self._sessions)

    def purge_orphans(self) -> int:
        """Lúc khởi động: thư mục phiên còn sót từ lần chạy trước (bộ nhớ đã mất) thì xoá."""
        n = 0
        if self.sessions_dir.is_dir():
            for d in self.sessions_dir.iterdir():
                if d.is_dir() and d not in {s.dir for s in self._sessions.values()}:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
        return n


class Debouncer:
    """Mỗi key một hẹn giờ: trigger lại thì huỷ lần trước và đợi thêm `delay` giây yên lặng."""

    def __init__(self, delay: float, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.delay = delay
        self._sleep = sleep
        self._tasks: dict[Any, asyncio.Task] = {}

    def trigger(self, key: Any, callback: Callable[[], Awaitable[None]]) -> None:
        old = self._tasks.get(key)
        if old is not None and not old.done():
            old.cancel()
        self._tasks[key] = asyncio.get_running_loop().create_task(self._run(key, callback))

    async def _run(self, key: Any, callback: Callable[[], Awaitable[None]]) -> None:
        try:
            await self._sleep(self.delay)
        except asyncio.CancelledError:
            return
        try:
            await callback()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Lỗi trong tác vụ debounce (key=%s)", key)
        finally:
            if self._tasks.get(key) is asyncio.current_task():
                self._tasks.pop(key, None)

    def cancel(self, key: Any) -> None:
        t = self._tasks.pop(key, None)
        if t is not None and not t.done():
            t.cancel()

    def cancel_all(self) -> None:
        for key in list(self._tasks):
            self.cancel(key)

    async def drain(self) -> None:
        """Đợi mọi hẹn giờ đang chờ chạy xong (dùng khi test / tắt bot)."""
        while True:
            pending = [t for t in self._tasks.values() if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    """Khoá file giữ suốt đời tiến trình (hệ điều hành tự nhả khi tiến trình chết)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise AlreadyRunning(str(self.path)) from None
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
