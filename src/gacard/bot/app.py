"""Gắn module thật (import trễ) và chạy bot bằng long polling."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable

from .config import Config, ConfigError, load_config
from .services import (
    FixtureCropper,
    RenderRequest,
    Services,
    find_default_fixture,
)
from .session import AlreadyRunning, InstanceLock

log = logging.getLogger("gacard.bot")

EXIT_CONFIG = 2
EXIT_RUNNING = 3
EXIT_MISSING_MODULE = 4


# ------------------------------------------------------------------ log


class RedactFilter(logging.Filter):
    """Không bao giờ để bí mật (token bot, cookie / cookie_token HoYoLAB) lọt vào log."""

    def __init__(self, secret: "str | Iterable[str]"):
        super().__init__()
        items = [secret] if isinstance(secret, str) else list(secret)
        self.secrets = sorted({x for x in items if x}, key=len, reverse=True)
        self.secret = self.secrets[0] if self.secrets else ""

    def _clean(self, text: str) -> str:
        for x in self.secrets:
            text = text.replace(x, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            text = record.getMessage()
            cleaned = self._clean(text)
            if cleaned != text:
                record.msg = cleaned
                record.args = None
            if record.exc_text:
                record.exc_text = self._clean(record.exc_text)
        return True


def secret_values(raw: "str | None") -> list[str]:
    """Chuỗi cookie → [cả chuỗi, từng giá trị dài ≥ 6] để lọc khỏi log. Không in ra đâu cả."""
    raw = (raw or "").strip()
    if not raw:
        return []
    out = [raw]
    for part in raw.split(";"):
        _, sep, value = part.strip().partition("=")
        if sep and len(value.strip()) >= 6:
            out.append(value.strip())
    return out


def hoyolab_secrets() -> list[str]:
    """Giá trị GACARD_HOYOLAB_COOKIE / _COOKIE_TOKEN (env hoặc .env) chỉ để RedactFilter che đi."""
    try:
        from gacard.hoyolab import _env_file
        from .config import parse_env_file

        env = parse_env_file(_env_file())
    except Exception:
        env = {}
    out: list[str] = []
    for key in ("GACARD_HOYOLAB_COOKIE", "GACARD_HOYOLAB_COOKIE_TOKEN"):
        out += secret_values(os.environ.get(key) or env.get(key))
    return out


def setup_logging(logs_dir: Path, secret: "str | Iterable[str]" = "", console: bool = True) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "gacard-bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler(sys.stderr))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactFilter(secret))
        root.addHandler(h)
    logging.getLogger("aiogram").setLevel(logging.INFO)


# ------------------------------------------------------------------ adapter cho module thật


class RealRenderer:
    def render(self, sheet: Any, base_dir: Path, request: RenderRequest, backgrounds: Any) -> bytes:
        from gacard.models import RenderOptions
        from gacard.render import render_sheet

        options = RenderOptions(
            code=request.code,
            uid=request.uid,
            background=request.background_id,
            watermark=request.watermark,
        )
        # background_id=None nghĩa là NỀN MẶC ĐỊNH: không đưa kho cho renderer, nếu không nó sẽ tự chọn nền.
        # render_sheet tự đánh dấu used_count của nền lấy từ kho, nên bot không gọi mark_used nữa.
        library = backgrounds if request.background_id else None
        return render_sheet(sheet, base_dir, options, backgrounds=library)


class RealCharacters:
    def __init__(self) -> None:
        from gacard import characters

        self._m = characters

    def resolve_character(self, name: str) -> str | None:
        return self._m.resolve_character(name)

    def load_hype(self) -> dict[str, int]:
        return self._m.load_hype()

    def display_name(self, slug: str) -> str:
        return self._m.display_name(slug)


class RealUidCard:
    """Chế độ chỉ UID: gacard.uidmode.build_sheet_from_uid với cache Enka/icon trong data dir của bot."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def build(self, uid: str, work_dir: Path) -> tuple[Any, Any, Any]:
        from gacard.enka import EnkaClient
        from gacard.uidmode import IconCache, build_sheet_from_uid

        ua = self.config.enka_user_agent
        client = EnkaClient(self.config.enka_cache_dir, ua)
        icons = IconCache(self.config.data_dir / "icons", ua)
        return build_sheet_from_uid(uid, work_dir, client=client, icons=icons)


class RealHoyolabCard:
    """/ghep: gacard.hoyolab.build_sheet_from_hoyolab với cookie CỦA CHỦ BOT trong .env.

    Không bao giờ nhận tài khoản/mật khẩu, không tự đăng nhập, không đụng captcha: HoYoLAB đòi xác minh thì
    ném HoyolabError(kind="verification") (on_verification=None) để bot báo admin. Ô banner (lịch sử roll)
    chỉ thử khi UID thuộc cookie: nếu có GACARD_HOYOLAB_OWNER_UIDS thì UID ngoài danh sách bỏ qua luôn
    (không gọi mạng); không có thì hoyolab tự bỏ qua kèm lý do khi HoYoLAB không cấp authkey."""

    def __init__(self, config: Config) -> None:
        self.owner_uids = config.hoyolab_owner_uids

    def build(self, uid: str, work_dir: Path, code: "str | None") -> tuple[Any, Any]:
        from gacard import hoyolab
        from gacard.models import RenderOptions

        with_wish = not self.owner_uids or uid in self.owner_uids
        sheet, report = hoyolab.build_sheet_from_hoyolab(
            uid, work_dir, options=RenderOptions(code=code, uid=uid), with_wish=with_wish, on_verification=None
        )
        if not with_wish:
            report.wish_lines.append("Bỏ qua ô banner: UID này không liên kết với tài khoản HoYoLAB của chủ bot.")
        return sheet, report


def build_services(config: Config) -> Services:
    from gacard.backgrounds import BackgroundLibrary

    config.data_dir.mkdir(parents=True, exist_ok=True)
    fixture = find_default_fixture(config.fixtures_dir, config.fixture) or (config.fixtures_dir / (config.fixture or "_none"))
    enka = None
    try:
        from gacard.enka import EnkaClient

        enka = EnkaClient(config.enka_cache_dir, config.enka_user_agent)
    except ImportError:
        log.warning("Chưa có module gacard.enka: bỏ qua tra Enka (dùng /cm).")
    return Services(
        cropper=FixtureCropper(fixture),
        renderer=RealRenderer(),
        backgrounds=BackgroundLibrary(config.backgrounds_dir),
        characters=RealCharacters(),
        enka=enka,
        uidcard=RealUidCard(config) if enka is not None else None,
        hoyocard=RealHoyolabCard(config),
    )


# ------------------------------------------------------------------ chạy


async def _housekeeping(controller: Any) -> None:
    ticks = 0
    while True:
        await asyncio.sleep(30)
        try:
            await controller.sweep()
            ticks += 1
            if ticks % 120 == 0:  # mỗi giờ
                controller.cleanup_outputs()
        except Exception:
            log.exception("Lỗi dọn dẹp định kỳ")


async def run(config: Config, services: Services | None = None) -> None:
    from aiogram import Bot, Dispatcher

    from .handlers import AiogramMessenger, BotController, build_router

    services = services or build_services(config)
    bot = Bot(token=config.token)
    controller = BotController(config, services, AiogramMessenger(bot))
    n = controller.sessions.purge_orphans()
    removed = controller.cleanup_outputs()
    log.info(
        "Khởi động bot gacard: %d chat được phép (.env + allowed.json), %d admin; output: %s; dọn %d phiên cũ, %d file kết quả cũ",
        len(controller.access.ids()), len(config.admin_chat_ids), config.output_dir, n, removed,
    )  # fmt: skip

    dp = Dispatcher()
    dp.include_router(build_router(controller))
    keeper = asyncio.create_task(_housekeeping(controller))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        keeper.cancel()
        controller.debouncer.cancel_all()
        await bot.session.close()


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    try:
        config = load_config()
    except ConfigError as e:
        print("LỖI CẤU HÌNH: " + e.message_vi, file=sys.stderr)
        return EXIT_CONFIG

    setup_logging(config.logs_dir, secret=[config.token, *hoyolab_secrets()])
    lock = InstanceLock(config.lock_file)
    try:
        lock.acquire()
    except AlreadyRunning:
        print(
            f"Bot gacard đang chạy ở một cửa sổ/tiến trình khác (khoá: {config.lock_file}). "
            "Tắt bản đó trước, vì hai bản cùng token sẽ tranh nhau cập nhật (lỗi 409).",
            file=sys.stderr,
        )
        return EXIT_RUNNING
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        log.info("Đã dừng bot (Ctrl+C).")
    except ImportError as e:
        log.exception("Thiếu module")
        print(f"Thiếu module cần để chạy bot: {e}. Kiểm tra các module gacard khác đã có chưa.", file=sys.stderr)
        return EXIT_MISSING_MODULE
    finally:
        lock.release()
    return 0
