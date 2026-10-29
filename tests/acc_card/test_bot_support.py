"""Đồ giả dùng chung cho các test bot (không gọi Telegram, Enka hay renderer thật)."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image

from gacard.bot.config import Config
from gacard.bot.handlers import BotController, IncomingMedia
from gacard.bot.services import RenderRequest, Services
from gacard.bot.session import Debouncer, SessionManager

NAMES = {
    "columbina": "Columbina",
    "zibai": "Zibai",
    "hutao": "Hu Tao",
    "furina": "Furina",
    "nahida": "Nahida",
}
ALIASES = {"columbina": "columbina", "zibai": "zibai", "hu tao": "hutao", "hutao": "hutao", "furina": "furina", "nahida": "nahida"}


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeMessenger:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.sizes: dict[str, tuple[int, int]] = {}
        self.downloads: list[str] = []
        self.deleted: list[tuple[int, int]] = []
        self.delete_fails = False

    def size_of(self, file_id: str, w: int, h: int) -> str:
        self.sizes[file_id] = (w, h)
        return file_id

    async def send_text(self, chat_id, text, buttons=None):
        self.sent.append({"kind": "text", "chat": chat_id, "text": text, "buttons": buttons})

    async def send_photo(self, chat_id, data, caption=None):
        self.sent.append({"kind": "photo", "chat": chat_id, "data": data, "caption": caption})

    async def send_document(self, chat_id, data, filename, caption=None, buttons=None):
        self.sent.append(
            {"kind": "doc", "chat": chat_id, "data": data, "filename": filename, "caption": caption, "buttons": buttons}
        )

    async def download(self, file_id, dest: Path):
        self.downloads.append(file_id)
        w, h = self.sizes.get(file_id, (1920, 1080))
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (w, h), (10, 20, 30)).save(dest, format=dest.suffix.lstrip(".").upper().replace("JPG", "JPEG") or "PNG")

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        if self.delete_fails:
            raise RuntimeError("bot không có quyền xoá tin")

    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent if m["kind"] == "text"]

    def texts_to(self, chat_id: int) -> list[str]:
        return [m["text"] for m in self.sent if m["kind"] == "text" and m["chat"] == chat_id]

    def kinds(self) -> list[str]:
        return [m["kind"] for m in self.sent]

    def last_text(self) -> str:
        return self.texts()[-1]


class FakeBgError(Exception):
    def __init__(self, message_vi: str):
        super().__init__(message_vi)
        self.message_vi = message_vi


@dataclass
class FakeBg:
    id: str
    character: str
    used_count: int = 0
    last_used_at: str | None = None


class FakeStore:
    """Giống BackgroundLibrary: nhận ảnh >= 1920x1080, pick = random.choice trong các nền có nhân vật thuộc acc."""

    def __init__(self) -> None:
        self.bgs: list[FakeBg] = []
        self.next_id = 1
        self.used: list[str] = []
        self.pick_calls: list[tuple] = []

    def seed(self, character: str, used_count: int = 0) -> FakeBg:
        bg = FakeBg(f"bg{self.next_id}", character, used_count)
        self.next_id += 1
        self.bgs.append(bg)
        return bg

    def add(self, image_bytes, character, source=None):
        import io

        w, h = Image.open(io.BytesIO(image_bytes)).size
        if w < 1920 or h < 1080:
            raise FakeBgError(f"Nền quá nhỏ ({w}x{h}), cần tối thiểu 1920x1080.")
        return self.seed(character)

    def list(self, character=None):
        return [b for b in self.bgs if character is None or b.character == character]

    def get(self, background_id):
        return next((b for b in self.bgs if b.id == background_id), None)

    def remove(self, background_id):
        self.bgs = [b for b in self.bgs if b.id != background_id]

    def pick(self, characters_in_account, rng=None, exclude=()):
        self.pick_calls.append((list(characters_in_account), list(exclude)))
        cands = [b for b in self.bgs if b.character in set(characters_in_account) and b.id not in set(exclude)]
        if not cands:
            return None
        return (rng or random).choice(cands)

    def mark_used(self, background_id):
        self.used.append(background_id)
        bg = self.get(background_id)
        if bg:
            bg.used_count += 1


class FakeChars:
    def __init__(self, hype: dict[str, int] | None = None):
        self.hype = hype if hype is not None else {"zibai": 95, "columbina": 90, "furina": 70, "hutao": 60, "nahida": 50}

    def resolve_character(self, name):
        return ALIASES.get(name.strip().lower())

    def load_hype(self):
        return dict(self.hype)

    def display_name(self, slug):
        return NAMES.get(slug, slug)


@dataclass
class FakeCard:
    character: str | None = None
    badge: int | None = None


@dataclass
class FakeSheet:
    characters: list[FakeCard] = field(default_factory=list)
    weapons: list = field(default_factory=list)


class FakeCropper:
    needs_images = True

    def __init__(self, characters: list[str | None] | None = None, badges: dict[int, int] | None = None):
        self.characters = characters if characters is not None else ["columbina", "zibai", "hutao", None]
        self.preset_badges = badges or {}
        self.calls: list[tuple[list[Path], Path]] = []
        self.fail: Exception | None = None

    def build_sheet(self, images, work_dir):
        self.calls.append((list(images), work_dir))
        if self.fail:
            raise self.fail
        work_dir.mkdir(parents=True, exist_ok=True)
        return FakeSheet([FakeCard(c, self.preset_badges.get(i)) for i, c in enumerate(self.characters)])


class FakeRenderer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail: Exception | None = None

    def render(self, sheet, base_dir, request: RenderRequest, backgrounds):
        self.calls.append(
            {
                "request": request,
                "badges": {c.character: c.badge for c in sheet.characters if c.character},
                "base_dir": base_dir,
                "thread": __import__("threading").current_thread().name,
            }
        )
        if self.fail:
            raise self.fail
        if request.background_id:  # renderer thật (render_sheet) tự đánh dấu nền đã dùng
            backgrounds.mark_used(request.background_id)
        return b"\xff\xd8JPEGBYTES\xff\xd9"


def make_profile(uid: str = "850363095", avatars: list[tuple[str | None, int | None]] | None = None):
    avatars = avatars if avatars is not None else [("columbina", 1), ("zibai", None), ("furina", 3)]
    return SimpleNamespace(
        uid=uid,
        nickname="Lữ Khách",
        adventure_rank=60,
        world_level=8,
        avatars=[SimpleNamespace(avatar_id=i, character=c, level=90, constellation=k) for i, (c, k) in enumerate(avatars)],
        fetched_at=datetime(2026, 10, 21),
    )


class FakeEnka:
    def __init__(self, profile=None, error: Exception | None = None, delay: float = 0.0):
        self.profile = profile if profile is not None else make_profile()
        self.error = error
        self.delay = delay
        self.calls: list[str] = []

    async def fetch(self, uid):
        self.calls.append(uid)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.profile


class FakeEnkaError(Exception):
    def __init__(self, message_vi: str):
        super().__init__(message_vi)
        self.message_vi = message_vi


def make_config(tmp_path: Path, **overrides) -> Config:
    base = dict(
        token="123456:SECRET-TOKEN",
        allowed_chat_ids=frozenset({1, 2}),
        data_dir=tmp_path / "data",
        debounce=0.05,
        admin_chat_ids=frozenset({1}),
        output_dir=tmp_path / "output",
    )
    base.update(overrides)
    return Config(**base)


@dataclass
class Rig:
    ctl: BotController
    msg: FakeMessenger
    store: FakeStore
    chars: FakeChars
    cropper: FakeCropper
    renderer: FakeRenderer
    enka: FakeEnka | None
    clock: FakeClock
    config: Config

    @property
    def sessions(self) -> SessionManager:
        return self.ctl.sessions

    async def settle(self) -> None:
        """Đợi debounce + tác vụ Enka nền chạy xong."""
        await self.ctl.debouncer.drain()
        for cid in self.sessions.active_chats():
            s = self.sessions.get(cid)
            if s is not None and s.enka_task is not None:
                await asyncio.gather(s.enka_task, return_exceptions=True)


def make_rig(tmp_path: Path, enka: Any = "default", seed: int = 0, hoyocard: Any = None, **cfg) -> Rig:
    config = make_config(tmp_path, **cfg)
    clock = FakeClock()
    msg, store, chars = FakeMessenger(), FakeStore(), FakeChars()
    cropper, renderer = FakeCropper(), FakeRenderer()
    enka_obj = FakeEnka() if enka == "default" else enka
    svc = Services(
        cropper=cropper,
        renderer=renderer,
        backgrounds=store,
        characters=chars,
        enka=enka_obj,
        now=lambda: datetime(2026, 10, 21, 14, 5, 33),
        hoyocard=hoyocard,
        rng=random.Random(seed),
    )
    sessions = SessionManager(config.sessions_dir, ttl=config.session_ttl, clock=clock, now=svc.now)
    ctl = BotController(config, svc, msg, sessions=sessions, debouncer=Debouncer(config.debounce), clock=clock)
    return Rig(ctl, msg, store, chars, cropper, renderer, enka_obj, clock, config)


def photo(message_id: int, msg: FakeMessenger, w: int = 1920, h: int = 1080, caption: str | None = None, group: str | None = None):
    return IncomingMedia(
        message_id=message_id,
        file_id=msg.size_of(f"f{message_id}", w, h),
        is_document=False,
        file_size=1000,
        media_group_id=group,
        caption=caption,
    )


def doc(message_id: int, msg: FakeMessenger, w: int = 2560, h: int = 1440, name: str = "x.png", mime: str = "image/png",
        caption: str | None = None, size: int = 1000):
    return IncomingMedia(
        message_id=message_id,
        file_id=msg.size_of(f"f{message_id}", w, h),
        is_document=True,
        file_size=size,
        file_name=name,
        mime_type=mime,
        caption=caption,
    )
