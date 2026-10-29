"""Bộ điều khiển bot (BotController, không phụ thuộc aiogram) + lớp vỏ aiogram mỏng.

BotController nhận sự kiện đã chuẩn hoá (chat_id, lệnh, IncomingMedia, callback) và nói chuyện qua
Messenger/Services (Protocol) nên test được hoàn toàn bằng đồ giả.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from PIL import Image

from . import texts
from .access import AllowList, normalize_username
from .config import Config
from .services import (
    Buttons,
    BG_RANDOM_WHY,
    BadgeReport,
    BgChoice,
    Messenger,
    RenderRequest,
    Services,
    apply_constellations,
    bg_choice,
    build_bg_plan,
    characters_in_account,
    error_text_vi,
    format_enka_summary,
    hoyolab_summary_vi,
    looks_like_credentials,
    normalize_code,
    output_filename,
    parse_caption_options,
    parse_cm_args,
    parse_ghep_login_args,
    parse_uid_args,
    saved_copy_name,
)
from .session import Debouncer, MediaItem, RenderState, Session, SessionManager

log = logging.getLogger("gacard.bot")

COMMANDS = (
    "start", "help", "trogiup", "huongdan", "mau", "ma", "uid", "cm", "nen", "themnen", "dsnen", "xoanen",
    "uutien", "xong", "huy", "trangthai", "moi", "bomoi", "danhsach", "ghep",
)  # fmt: skip
CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CB_NEXT_BG = "bg:next"
CB_REDO = "redo"
CB_VERIFIED = "hv:"  # + mã yêu cầu xác minh HoYoLAB (chỉ admin bấm)
ENKA_WAIT_SECONDS = 20.0
VERIFY_TTL = 30 * 60  # yêu cầu xác minh bỏ sau 30 phút
BG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-[0-9a-f]{8}$")


@dataclass
class IncomingMedia:
    message_id: int
    file_id: str
    is_document: bool = False
    file_size: int | None = None
    file_name: str | None = None
    mime_type: str | None = None
    media_group_id: str | None = None
    caption: str | None = None

    def extension(self) -> str:
        if self.file_name:
            suffix = Path(self.file_name).suffix.lower()
            if suffix in IMAGE_EXTS:
                return suffix
        return {"image/png": ".png", "image/webp": ".webp"}.get(self.mime_type or "", ".jpg")

    def is_image(self) -> bool:
        if not self.is_document:
            return True
        if self.mime_type in IMAGE_MIMES:
            return True
        return bool(self.file_name) and Path(self.file_name).suffix.lower() in IMAGE_EXTS


@dataclass
class _PendingBg:
    slug: str
    deadline: float


@dataclass
class _PendingVerify:
    """Một lần /ghep đang chờ admin tự xác minh HoYoLAB trên trình duyệt của họ (bot không đụng captcha)."""

    chat_id: int
    uid: str
    code: str | None
    bg_override: str | None
    notes: list[str]
    deadline: float


class BotController:
    def __init__(
        self,
        config: Config,
        services: Services,
        messenger: Messenger,
        sessions: SessionManager | None = None,
        debouncer: Debouncer | None = None,
        clock: Callable[[], float] = time.monotonic,
        access: AllowList | None = None,
    ):
        self.config = config
        self.access = access or AllowList(config.allowed_file, config.allowed_chat_ids, config.admin_chat_ids)
        self.svc = services
        self.msg = messenger
        self.clock = clock
        self.sessions = sessions or SessionManager(config.sessions_dir, ttl=config.session_ttl, clock=clock, now=services.now)
        self.debouncer = debouncer or Debouncer(config.debounce)
        self._pending_bg: dict[int, _PendingBg] = {}
        self._pending_verify: dict[str, _PendingVerify] = {}
        self._verify_seq = 0
        self._refused: set[int] = set()

    # ------------------------------------------------------------------ tiện ích

    async def _say(self, chat_id: int, text: str, buttons: Buttons | None = None) -> None:
        try:
            await self.msg.send_text(chat_id, text, buttons)
        except Exception:
            log.exception("Không gửi được tin nhắn tới chat %s", chat_id)

    def _name(self, slug: str) -> str:
        return self.svc.characters.display_name(slug)

    def is_allowed(self, chat_id: int) -> bool:
        """Đọc danh sách gộp (.env + allowed.json) mỗi lần, nên /moi, /bomoi có hiệu lực ngay."""
        return self.access.is_allowed(chat_id)

    async def guard(self, chat_id: int, username: str | None = None) -> bool:
        """Chat lạ: một câu từ chối ngắn (mỗi chat một lần), không xử lý gì thêm.

        `username` (chỉ truyền khi là chat riêng với chính người đó): nếu có trong lời mời /moi @ten mà
        chưa nhận diện, ghi chat ID vào allowed.json rồi báo admin (một lần) và cho qua."""
        if username and self.access.claim(chat_id, username):
            await self._announce_claim(chat_id, normalize_username(username) or username)
        if self.is_allowed(chat_id):
            return True
        if chat_id not in self._refused:
            self._refused.add(chat_id)
            log.warning("Từ chối chat không có trong danh sách cho phép: %s", chat_id)
            await self._say(chat_id, texts.REFUSAL.format(chat_id=chat_id))
        return False

    async def _announce_claim(self, chat_id: int, name: str) -> None:
        self._refused.discard(chat_id)
        log.info("Nhận diện @%s = chat %s (từ lời mời theo username)", name, chat_id)
        for admin in sorted(self.access.admin_ids):
            if admin != chat_id:
                await self._say(admin, texts.USERNAME_RECOGNIZED.format(name=name, id=chat_id))

    # ------------------------------------------------------------------ cổng vào

    async def handle_command(
        self, chat_id: int, name: str, args: str | None, username: str | None = None, message_id: int | None = None
    ) -> None:
        name = name.lstrip("/").split("@")[0].lower()
        try:
            if not await self.guard(chat_id, username):
                return
            # Ngoại lệ DUY NHẤT của bộ lọc tài khoản/mật khẩu: /ghep <tk>-<mk>.
            # Tin vẫn bị xoá ngay (nếu bot xoá được) trước khi xử lý, và mật khẩu
            # chỉ sống trong lần đăng nhập này (xem _cmd_ghep / _build_login).
            is_login = name == "ghep" and parse_ghep_login_args(args or "") is not None
            if is_login:
                if message_id is not None:
                    try:
                        await self.msg.delete_message(chat_id, message_id)
                    except Exception:
                        log.info("Không xoá được tin /ghep đăng nhập (chat %s)", chat_id)
            elif await self._refuse_credentials(chat_id, args, message_id):
                return
            self.sessions.touch(chat_id)
            await self._command(chat_id, name, (args or "").strip())
        except Exception:
            log.exception("Lỗi khi xử lý lệnh /%s (chat %s)", name, chat_id)
            await self._say(chat_id, texts.GENERIC_ERROR)

    async def handle_media(self, chat_id: int, media: IncomingMedia, username: str | None = None) -> None:
        try:
            if not await self.guard(chat_id, username):
                return
            if await self._refuse_credentials(chat_id, media.caption, media.message_id):
                return
            await self._media(chat_id, media)
        except Exception:
            log.exception("Lỗi khi nhận ảnh (chat %s)", chat_id)
            await self._say(chat_id, texts.GENERIC_ERROR)

    async def handle_text(
        self, chat_id: int, username: str | None = None, text: str | None = None, message_id: int | None = None
    ) -> None:
        if await self.guard(chat_id, username):
            if await self._refuse_credentials(chat_id, text, message_id):
                return
            await self._say(chat_id, texts.TEXT_HINT)

    def _safe_dash_token(self, token: str) -> bool:
        """Cụm có '-' mà bot dùng thật: slug nhân vật (hu-tao) hoặc id nền (hu-tao-1a2b3c4d)."""
        low = token.lower()
        if BG_ID_RE.match(low):
            return True
        try:
            return self.svc.characters.resolve_character(low) == low
        except Exception:
            return False

    async def _refuse_credentials(self, chat_id: int, text: str | None, message_id: int | None) -> bool:
        """Tin giống tài khoản/mật khẩu: thử xoá tin (lỗi thì bỏ qua), nhắc dùng /ghep <UID>.

        Không lặp lại, không log, không lưu nội dung tin."""
        if not looks_like_credentials(text, self._safe_dash_token):
            return False
        if message_id is not None:
            try:
                await self.msg.delete_message(chat_id, message_id)
            except Exception:
                log.info("Không xoá được tin nghi là tài khoản/mật khẩu (chat %s)", chat_id)
        log.warning("Chặn một tin nghi là tài khoản/mật khẩu (chat %s); nội dung không được ghi lại", chat_id)
        await self._say(chat_id, texts.CREDENTIALS_REFUSED)
        return True

    async def handle_callback(self, chat_id: int, data: str, username: str | None = None) -> str | None:
        """Trả về chữ hiện trên toast của nút (hoặc None)."""
        try:
            if not await self.guard(chat_id, username):
                return None
            return await self._callback(chat_id, data)
        except Exception:
            log.exception("Lỗi khi xử lý nút %s (chat %s)", data, chat_id)
            await self._say(chat_id, texts.GENERIC_ERROR)
            return None

    async def sweep(self) -> list[int]:
        """Gọi định kỳ: xoá phiên quá hạn, báo cho người dùng."""
        gone = self.sessions.expire_idle()
        for cid in gone:
            self.debouncer.cancel(cid)
            await self._say(cid, texts.SESSION_EXPIRED)
        now = self.clock()
        for cid in [c for c, p in self._pending_bg.items() if p.deadline < now]:
            self._pending_bg.pop(cid, None)
        for key in [k for k, v in self._pending_verify.items() if v.deadline < now]:
            self._pending_verify.pop(key, None)
        return gone

    def cleanup_outputs(self) -> int:
        """Xoá file kết quả cũ hơn output_keep_days."""
        out = self.config.out_dir
        if not out.is_dir():
            return 0
        limit = time.time() - self.config.output_keep_days * 86400
        n = 0
        for f in out.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < limit:
                    f.unlink()
                    n += 1
            except OSError:
                log.warning("Không xoá được %s", f)
        return n

    # ------------------------------------------------------------------ lệnh

    async def _command(self, chat_id: int, name: str, args: str) -> None:
        table: dict[str, Callable[[int, str], Awaitable[None]]] = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "trogiup": self._cmd_help,
            "huongdan": self._cmd_huongdan,
            "mau": self._cmd_mau,
            "ma": self._cmd_ma,
            "uid": self._cmd_uid,
            "ghep": self._cmd_ghep,
            "cm": self._cmd_cm,
            "nen": self._cmd_nen,
            "themnen": self._cmd_themnen,
            "dsnen": self._cmd_dsnen,
            "xoanen": self._cmd_xoanen,
            "uutien": self._cmd_uutien,
            "xong": self._cmd_xong,
            "huy": self._cmd_huy,
            "trangthai": self._cmd_trangthai,
            "moi": self._cmd_moi,
            "bomoi": self._cmd_bomoi,
            "danhsach": self._cmd_danhsach,
        }
        fn = table.get(name)
        if fn is None:
            await self._say(chat_id, texts.UNKNOWN_COMMAND)
            return
        await fn(chat_id, args)

    async def _cmd_start(self, chat_id: int, args: str) -> None:
        await self._say(chat_id, texts.START)

    async def _cmd_help(self, chat_id: int, args: str) -> None:
        await self._say(chat_id, texts.help_text(self.access.is_admin(chat_id)))

    async def _cmd_huongdan(self, chat_id: int, args: str) -> None:
        await self._say(chat_id, texts.HUONGDAN)

    async def _cmd_mau(self, chat_id: int, args: str) -> None:
        await self._say(chat_id, texts.MAU)

    async def _cmd_ma(self, chat_id: int, args: str) -> None:
        code = normalize_code(args)
        if not code:
            await self._say(chat_id, texts.BAD_CODE)
            return
        self.sessions.get_or_create(chat_id).code = code
        await self._say(chat_id, texts.CODE_SET.format(code=code))

    async def _cmd_uid(self, chat_id: int, args: str) -> None:
        """/uid <uid> [ma <so>] [nen <ten>]: tra Enka, tự vẽ mảnh rồi ghép ảnh ngay (chế độ chỉ UID).

        Chưa gắn bộ vẽ UID (svc.uidcard None) thì như cũ: chỉ đặt UID + tóm tắt Enka cho phiên ảnh chụp."""
        ua = parse_uid_args(args)
        if not ua.uid:
            await self._say(chat_id, texts.BAD_UID)
            return
        s = self.sessions.get_or_create(chat_id)
        if self.svc.uidcard is None:
            await self._say(chat_id, texts.UID_SET.format(uid=ua.uid))
            await self._start_enka(chat_id, s, ua.uid, announce=True)
            return
        if s.busy:
            await self._say(chat_id, texts.BUSY)
            return
        notes = list(ua.errors)
        slug: str | None = None
        if ua.nen:
            slug = self.svc.characters.resolve_character(ua.nen)
            if slug is None:
                notes.append(texts.UNKNOWN_CHARACTER.format(name=ua.nen))
        if s.enka_task is not None and not s.enka_task.done():
            s.enka_task.cancel()  # bản vẽ UID tự tra Enka
        s.enka_task = None
        s.uid, s.code, s.bg_override = ua.uid, ua.code, slug
        s.enka_profile, s.enka_error = None, None
        s.mode, s.render = "uid", None
        await self._run_render(chat_id, s, rebuild=True, extra_notes=notes)

    async def _cmd_ghep(self, chat_id: int, args: str) -> None:
        """/ghep <uid> [ma <so>] [nen <ten>]: Sổ Tay Hành Trình HoYoLAB (cookie của chủ bot trong .env) →
        đủ nhân vật/vũ khí, thẻ khám phá, ô hồ sơ, ô banner (nếu UID thuộc cookie) → ghép ảnh.
        /ghep <tk>-<mk> [ma <so>] [nen <ten>]: nhánh đăng nhập riêng — bot tự đăng nhập
        HoYoLAB bằng tài khoản của người gửi, suy UID từ tài khoản đó, rồi vẽ bằng
        đúng pipeline HoYoLAB cũ (không dùng cookie chủ bot)."""
        if not args:
            await self._say(chat_id, texts.NEED_GHEP_ARGS)
            return
        login = parse_ghep_login_args(args)
        if login is not None:
            if self.svc.login_card is None:
                await self._say(chat_id, texts.GHEP_LOGIN_OFF)
                return
            slug: str | None = None
            if login.nen:
                slug = self.svc.characters.resolve_character(login.nen)
                if slug is None:
                    login.errors.append(texts.UNKNOWN_CHARACTER.format(name=login.nen))
            await self._start_ghep_login(chat_id, login, slug)
            return
        ua = parse_uid_args(args)
        if not ua.uid:
            first = args.split()[0] if args.split() else ""
            if "@" in first or any(c in first for c in ("-", ":", "|")):
                await self._say(chat_id, texts.GHEP_LOGIN_BAD)
            else:
                await self._say(chat_id, texts.BAD_UID)
            return
        if self.svc.hoyocard is None:
            await self._say(chat_id, texts.GHEP_OFF)
            return
        notes = list(ua.errors)
        slug: str | None = None
        if ua.nen:
            slug = self.svc.characters.resolve_character(ua.nen)
            if slug is None:
                notes.append(texts.UNKNOWN_CHARACTER.format(name=ua.nen))
        await self._start_ghep(chat_id, ua.uid, ua.code, slug, notes, retry=False)

    async def _start_ghep(
        self, chat_id: int, uid: str, code: str | None, slug: str | None, notes: list[str], retry: bool
    ) -> None:
        s = self.sessions.get_or_create(chat_id)
        if s.busy:
            await self._say(chat_id, texts.BUSY)
            return
        if s.enka_task is not None and not s.enka_task.done():
            s.enka_task.cancel()
        s.enka_task = None
        s.uid, s.code, s.bg_override = uid, code, slug
        s.enka_profile, s.enka_error = None, None
        s.mode, s.render = "hoyolab", None
        await self._run_render(chat_id, s, rebuild=True, extra_notes=notes, verify_retry=retry)

    async def _start_ghep_login(self, chat_id: int, login: Any, slug: str | None) -> None:
        s = self.sessions.get_or_create(chat_id)
        if s.busy:
            await self._say(chat_id, texts.BUSY)
            return
        if s.enka_task is not None and not s.enka_task.done():
            s.enka_task.cancel()
        s.enka_task = None
        s.uid, s.code, s.bg_override = None, login.code, slug
        s.enka_profile, s.enka_error = None, None
        s.mode, s.render = "login", None
        await self._say(chat_id, texts.GHEP_LOGIN_RENDERING)
        try:
            await self._run_login(chat_id, s, login)
        finally:
            # Hủy mật khẩu ngay sau lần dùng: không giữ trong session, không log.
            login.password = ""
            login.username = ""

    async def _run_login(self, chat_id: int, s: Any, login: Any) -> None:
        if s.busy:
            await self._say(chat_id, texts.BUSY)
            return
        s.busy = True
        try:
            if not await self._build_login(chat_id, s, login):
                return
            await self._render_current(chat_id, s, first=True)
        finally:
            s.busy = False
            self.sessions.touch(chat_id)

    async def _build_login(self, chat_id: int, s: Any, login: Any) -> bool:
        """Đăng nhập -> UID -> sheet bằng pipeline HoYoLAB cũ (qua login_card)."""
        assert self.svc.login_card is not None
        loop = asyncio.get_running_loop()

        def _on_captcha(link: str) -> None:
            # Gọi từ thread Playwright: chỉ đặt lịch gửi tin, không chờ ở đây.
            text = texts.GHEP_LOGIN_CAPTCHA_WEB.format(link=link) if link else texts.GHEP_LOGIN_CAPTCHA_LOCAL
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self._say(chat_id, text)))

        try:
            sheet, report, uid = await asyncio.to_thread(
                self.svc.login_card.build, login.username, login.password,
                s.login_work_dir, s.code, _on_captcha,
            )
        except Exception as e:
            uids = getattr(e, "uids", None)
            if uids:
                await self._say(chat_id, texts.GHEP_LOGIN_MULTI.format(uids=", ".join(uids)))
                return False
            why = error_text_vi(e) or getattr(e, "message_vi", None)
            if why is None:
                log.exception("Đăng nhập rồi ghép lỗi (chat %s)", chat_id)
                why = texts.UNKNOWN_WHY
            await self._say(chat_id, texts.GHEP_LOGIN_FAIL.format(why=why))
            return False
        s.uid = uid
        chars = list(getattr(report, "account_characters", None) or [])
        for c in characters_in_account(None, sheet):
            if c not in chars:
                chars.append(c)
        plan = build_bg_plan(self.svc.backgrounds, self.svc.characters, chars, s.bg_override, self.svc.rng)
        notes = [hoyolab_summary_vi(report, uid), *list(login.errors), *plan.notes]
        s.render = RenderState(
            sheet=sheet, work_dir=s.login_work_dir, choices=plan.choices, index=0, notes=notes, account=chars
        )
        return True

    async def _cmd_cm(self, chat_id: int, args: str) -> None:
        if not args:
            await self._say(chat_id, texts.NEED_CM_ARGS)
            return
        pairs, errors = parse_cm_args(args, self.svc.characters.resolve_character)
        if pairs:
            self.sessions.get_or_create(chat_id).manual_cm.update(pairs)
            items = ", ".join(f"{self._name(s)} C{n}" for s, n in pairs.items())
            await self._say(chat_id, texts.CM_SET.format(items=items))
        if errors:
            await self._say(chat_id, texts.CM_ERRORS.format(errors="\n- ".join(errors)))

    async def _cmd_nen(self, chat_id: int, args: str) -> None:
        if not args:
            await self._say(chat_id, texts.NEED_NEN_ARGS)
            return
        slug = self.svc.characters.resolve_character(args)
        if slug is None:
            await self._say(chat_id, texts.UNKNOWN_CHARACTER.format(name=args))
            return
        self.sessions.get_or_create(chat_id).bg_override = slug
        text = texts.NEN_SET.format(name=self._name(slug))
        if not self.svc.backgrounds.list(slug):
            text += f"\nLưu ý: kho chưa có nền nào của {self._name(slug)}, gửi nền bằng /themnen {slug}."
        await self._say(chat_id, text)

    async def _cmd_themnen(self, chat_id: int, args: str) -> None:
        if not args:
            await self._say(chat_id, texts.NEED_THEMNEN_ARGS)
            return
        slug = self.svc.characters.resolve_character(args)
        if slug is None:
            await self._say(chat_id, texts.UNKNOWN_CHARACTER.format(name=args))
            return
        self._pending_bg[chat_id] = _PendingBg(slug, self.clock() + self.config.session_ttl)
        await self._say(chat_id, texts.THEMNEN_WAIT.format(name=self._name(slug)))

    async def _cmd_dsnen(self, chat_id: int, args: str) -> None:
        bgs = list(self.svc.backgrounds.list())
        if not bgs:
            await self._say(chat_id, texts.BG_EMPTY)
            return
        groups: dict[str, list[str]] = {}
        for b in bgs:
            groups.setdefault(b.character, []).append(str(b.id))
        lines = [texts.BG_LIST_HEAD.format(total=len(bgs))]
        for slug in sorted(groups, key=lambda s: self._name(s).lower()):
            ids = groups[slug]
            lines.append(f"- {self._name(slug)} ({len(ids)}): " + ", ".join(ids))
        await self._say(chat_id, "\n".join(lines))

    async def _cmd_xoanen(self, chat_id: int, args: str) -> None:
        bg_id = args.split()[0] if args else ""
        if not bg_id:
            await self._say(chat_id, texts.NEED_XOANEN_ARGS)
            return
        try:
            bg = self.svc.backgrounds.get(bg_id)
            if bg is None:
                await self._say(chat_id, texts.BG_NOT_FOUND.format(id=bg_id))
                return
            self.svc.backgrounds.remove(bg_id)
        except Exception as e:
            why = error_text_vi(e)
            if why is None:
                raise
            await self._say(chat_id, texts.BG_REJECTED.format(why=why))
            return
        await self._say(chat_id, texts.BG_REMOVED.format(id=bg_id, name=self._name(bg.character)))

    async def _cmd_uutien(self, chat_id: int, args: str) -> None:
        hype = self.svc.characters.load_hype()
        if not hype:
            await self._say(chat_id, texts.HYPE_EMPTY)
            return
        counts: dict[str, int] = {}
        for b in self.svc.backgrounds.list():
            counts[b.character] = counts.get(b.character, 0) + 1
        top = sorted(hype.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
        lines = [texts.HYPE_HEAD]
        for i, (slug, score) in enumerate(top, 1):
            have = f" — {counts[slug]} nền" if counts.get(slug) else ""
            lines.append(f"{i}. {self._name(slug)}: {score}{have}")
        lines.append(texts.HYPE_FOOT)
        await self._say(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------ admin: mời / bỏ mời

    async def _admin_target(self, chat_id: int, args: str, usage: str) -> int | str | None:
        """Kiểm tra admin + đọc đối tượng: chat ID (int) hoặc username đã chuẩn hoá (str); sai → None."""
        if not self.access.is_admin(chat_id):
            log.warning("Chat %s (không phải admin) gọi lệnh admin", chat_id)
            await self._say(chat_id, texts.ADMIN_ONLY)
            return None
        raw = args.split()[0] if args else ""
        if not raw:
            await self._say(chat_id, usage)
            return None
        if CHAT_ID_RE.match(raw):
            return int(raw)
        name = normalize_username(raw)
        if name is None:
            await self._say(chat_id, texts.BAD_USERNAME.format(raw=raw))
            return None
        return name

    def _forget_chat(self, target: int) -> None:
        self.debouncer.cancel(target)
        self._pending_bg.pop(target, None)
        self.sessions.drop(target)

    async def _cmd_moi(self, chat_id: int, args: str) -> None:
        target = await self._admin_target(chat_id, args, texts.NEED_MOI_ARGS)
        if target is None:
            return
        if isinstance(target, str):
            added = self.access.add_username(target)
            log.info("Admin %s mời @%s (mới: %s)", chat_id, target, added)
            await self._say(chat_id, (texts.MOI_USER_DONE if added else texts.MOI_USER_ALREADY).format(name=target))
            return
        added = self.access.add(target)
        self._refused.discard(target)
        log.info("Admin %s mời chat %s (mới: %s)", chat_id, target, added)
        await self._say(chat_id, (texts.MOI_DONE if added else texts.MOI_ALREADY).format(id=target))

    async def _cmd_bomoi(self, chat_id: int, args: str) -> None:
        target = await self._admin_target(chat_id, args, texts.NEED_BOMOI_ARGS)
        if target is None:
            return
        if isinstance(target, str):
            found, linked = self.access.remove_username(target)
            if linked is not None and not self.access.is_admin(linked):
                self._forget_chat(linked)
            log.info("Admin %s bỏ mời @%s (có: %s, chat gắn: %s)", chat_id, target, found, linked)
            if not found:
                text = texts.BOMOI_USER_NOT_FOUND.format(name=target)
            elif linked is not None:
                text = texts.BOMOI_USER_DONE_ID.format(name=target, id=linked)
            else:
                text = texts.BOMOI_USER_DONE.format(name=target)
            await self._say(chat_id, text)
            return
        if self.access.is_admin(target):
            await self._say(chat_id, texts.BOMOI_ADMIN.format(id=target))
            return
        removed = self.access.remove(target)
        if removed:
            self._forget_chat(target)
        log.info("Admin %s bỏ mời chat %s (đang có quyền: %s)", chat_id, target, removed)
        await self._say(chat_id, (texts.BOMOI_DONE if removed else texts.BOMOI_NOT_FOUND).format(id=target))

    async def _cmd_danhsach(self, chat_id: int, args: str) -> None:
        if not self.access.is_admin(chat_id):
            await self._say(chat_id, texts.ADMIN_ONLY)
            return
        ids = sorted(self.access.ids())
        lines = [texts.DANHSACH_HEAD.format(n=len(ids))]
        lines += [f"- {i} ({self.access.source(i)})" for i in ids]
        users = self.access.usernames()
        if users:
            lines += ["", texts.DANHSACH_USERS_HEAD.format(n=len(users))]
            for name, cid in sorted(users.items()):
                tpl = texts.DANHSACH_USER_UNSEEN if cid is None else texts.DANHSACH_USER_SEEN
                lines.append(tpl.format(name=name, id=cid))
        await self._say(chat_id, "\n".join(lines))

    async def _cmd_huy(self, chat_id: int, args: str) -> None:
        self.debouncer.cancel(chat_id)
        self._pending_bg.pop(chat_id, None)
        self.sessions.drop(chat_id)
        await self._say(chat_id, texts.SESSION_CANCELLED)

    async def _cmd_trangthai(self, chat_id: int, args: str) -> None:
        s = self.sessions.get(chat_id)
        pend = self._pending_bg.get(chat_id)
        if s is None and pend is None:
            await self._say(chat_id, texts.NO_SESSION)
            return
        lines: list[str] = []
        if s is not None:
            lines.append(f"Phiên hiện tại: {len(s.items)} ảnh" + (f" (+{s.inflight} đang tải)" if s.inflight else ""))
            for i, m in enumerate(sorted(s.items, key=lambda m: m.message_id), 1):
                lines.append(f"  {i}. {m.width}x{m.height}" + (" (File)" if m.is_document else " (Ảnh)"))
            lines.append(f"Mã acc: {s.code or '(chưa đặt, /ma <so>)'}")
            lines.append(f"UID: {s.uid or '(chưa đặt, /uid <uid>)'}" + self._enka_state(s))
            if s.manual_cm:
                lines.append("Cung mệnh tay: " + ", ".join(f"{self._name(k)} C{v}" for k, v in s.manual_cm.items()))
            if s.bg_override:
                lines.append(f"Nền chọn tay: {self._name(s.bg_override)}")
        if pend is not None:
            lines.append(f"Đang chờ ảnh nền cho {self._name(pend.slug)} (gửi ảnh tiếp theo, /huy để bỏ).")
        await self._say(chat_id, "\n".join(lines))

    @staticmethod
    def _enka_state(s: Session) -> str:
        if s.enka_profile is not None:
            return " — Enka OK"
        if s.enka_error:
            return " — Enka lỗi"
        if s.enka_task is not None and not s.enka_task.done():
            return " — đang tra Enka"
        return ""

    # ------------------------------------------------------------------ nhận ảnh

    async def _media(self, chat_id: int, m: IncomingMedia) -> None:
        opts = parse_caption_options(m.caption)

        # 1) ảnh nền: đang chờ sau /themnen, hoặc caption chỉ có "nen <ten>"
        bg_slug: str | None = None
        pend = self._pending_bg.get(chat_id)
        if pend is not None and pend.deadline < self.clock():
            self._pending_bg.pop(chat_id, None)
            pend = None
        if pend is not None:
            bg_slug = pend.slug
        elif opts.only_nen and not opts.errors:
            bg_slug = self.svc.characters.resolve_character(opts.nen or "")
            if bg_slug is None:
                await self._say(chat_id, texts.UNKNOWN_CHARACTER.format(name=opts.nen))
                return
        if bg_slug is not None:
            await self._store_background(chat_id, m, bg_slug)
            return

        # 2) ảnh của phiên
        if not m.is_image():
            await self._say(chat_id, texts.NOT_IMAGE)
            return
        if m.file_size and m.file_size > self.config.max_bytes:
            await self._say(chat_id, texts.TOO_BIG.format(mb=m.file_size / 1024 / 1024))
            return

        s = self.sessions.get(chat_id)
        if s is not None and (s.rendered or (s.mode in ("uid", "hoyolab", "login") and not s.items)):
            self.sessions.drop(chat_id)  # đã ra ảnh xong (ảnh chụp, /uid hoặc /ghep); ảnh mới = acc mới
            s = None
        s = self.sessions.get_or_create(chat_id)
        self.sessions.touch(chat_id)

        for err in opts.errors:
            self._note(s, err)
        if opts.code:
            s.code = opts.code
        if opts.nen:
            slug = self.svc.characters.resolve_character(opts.nen)
            if slug:
                s.bg_override = slug
            else:
                self._note(s, texts.UNKNOWN_CHARACTER.format(name=opts.nen))
        if opts.uid:
            await self._start_enka(chat_id, s, opts.uid, announce=False)

        if s.slots_used() >= self.config.max_images:
            self._note(s, texts.TOO_MANY.format(n=self.config.max_images))
        else:
            s.inflight += 1
            try:
                index = s.slots_used()
                dest = s.dir / f"{index:02d}-{m.message_id}{m.extension()}"
                await self.msg.download(m.file_id, dest)
                try:
                    with Image.open(dest) as im:
                        width, height = im.size
                except Exception:
                    dest.unlink(missing_ok=True)
                    self._note(s, texts.UNREADABLE)
                else:
                    s.items.append(MediaItem(m.message_id, dest, width, height, m.is_document))
            finally:
                s.inflight -= 1

        # gom cả album (và ảnh gửi lẻ liên tiếp) thành MỘT tin tóm tắt
        self.debouncer.trigger(chat_id, lambda: self._send_summary(chat_id))

    @staticmethod
    def _note(s: Session, text: str) -> None:
        if text not in s.notes:
            s.notes.append(text)

    async def _send_summary(self, chat_id: int) -> None:
        s = self.sessions.get(chat_id)
        if s is None:
            return
        items = sorted(s.items, key=lambda m: m.message_id)
        lines = [f"Đã nhận {len(items)} ảnh."]
        small = [
            texts.SMALL_IMAGE.format(i=i, w=m.width, h=m.height, min=self.config.min_long_side)
            for i, m in enumerate(items, 1)
            if m.long_side < self.config.min_long_side
        ]
        for w in small:
            lines.append("Cảnh báo: " + w)
        if 0 < len(items) < 4:
            lines.append("Thường cần 4–7 ảnh (hồ sơ, nhân vật, vũ khí...). Gửi thêm nếu còn thiếu.")
        opts = []
        if s.code:
            opts.append(f"mã {s.code}")
        if s.uid:
            opts.append(f"UID {s.uid}")
        if s.bg_override:
            opts.append(f"nền {self._name(s.bg_override)}")
        lines.append("Tuỳ chọn: " + (", ".join(opts) if opts else "chưa đặt (/ma, /uid, /nen)"))
        lines.extend(s.notes)
        s.notes.clear()
        lines.append("Gõ /xong khi đủ ảnh, /huy để làm lại.")
        await self._say(chat_id, "\n".join(lines))

    async def _store_background(self, chat_id: int, m: IncomingMedia, slug: str) -> None:
        name = self._name(slug)
        if not m.is_image():
            await self._say(chat_id, texts.NOT_IMAGE)
            return
        if m.file_size and m.file_size > self.config.max_bytes:
            await self._say(chat_id, texts.TOO_BIG.format(mb=m.file_size / 1024 / 1024))
            return
        tmp = self.config.tmp_dir / f"bg-{chat_id}-{m.message_id}{m.extension()}"
        try:
            await self.msg.download(m.file_id, tmp)
            data = tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)
        try:
            bg = await asyncio.to_thread(self.svc.backgrounds.add, data, slug, f"telegram:{chat_id}")
        except Exception as e:
            why = error_text_vi(e)
            if why is None:
                raise
            text = texts.BG_REJECTED.format(why=why)
            if not m.is_document:
                text += "\n" + texts.BG_PHOTO_HINT
            await self._say(chat_id, text)
            return
        self._pending_bg.pop(chat_id, None)
        count = len(self.svc.backgrounds.list(slug))
        await self._say(chat_id, texts.BG_ADDED.format(name=name, id=bg.id, count=count))

    # ------------------------------------------------------------------ Enka

    async def _start_enka(self, chat_id: int, s: Session, uid: str, announce: bool) -> None:
        s.uid = uid
        s.enka_profile = None
        s.enka_error = None
        if s.enka_task is not None and not s.enka_task.done():
            s.enka_task.cancel()
        s.enka_task = None
        if self.svc.enka is None:
            if announce:
                await self._say(chat_id, texts.ENKA_OFF)
            else:
                self._note(s, texts.ENKA_OFF)
            return
        if announce:
            await self._say(chat_id, texts.ENKA_FETCHING.format(uid=uid))
        s.enka_task = asyncio.get_running_loop().create_task(self._fetch_enka(chat_id, s, uid))

    async def _fetch_enka(self, chat_id: int, s: Session, uid: str) -> None:
        assert self.svc.enka is not None
        try:
            profile = await self.svc.enka.fetch(uid)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            why = error_text_vi(e)
            if why is None:
                log.exception("Enka lỗi bất ngờ (uid=%s)", uid)
                why = texts.UNKNOWN_WHY
            if s.uid == uid:
                s.enka_error = why
                await self._say(chat_id, texts.ENKA_FAIL.format(uid=uid, why=why))
            return
        if s.uid != uid or self.sessions.get(chat_id) is not s:
            return  # đã đổi UID / huỷ phiên trong lúc chờ
        s.enka_profile = profile
        await self._say(chat_id, format_enka_summary(profile, self.svc.characters))

    async def _wait_enka(self, chat_id: int, s: Session) -> None:
        task = s.enka_task
        if task is None or task.done():
            return
        done, _ = await asyncio.wait({task}, timeout=ENKA_WAIT_SECONDS)
        if not done:
            task.cancel()
            await self._say(chat_id, texts.ENKA_WAIT_TIMEOUT)

    # ------------------------------------------------------------------ render

    async def _cmd_xong(self, chat_id: int, args: str) -> None:
        s = self.sessions.get(chat_id)
        if s is None:
            await self._say(chat_id, texts.NO_SESSION)
            return
        if not s.items and getattr(self.svc.cropper, "needs_images", True):
            await self._say(chat_id, texts.NEED_IMAGES)
            return
        s.mode = "chup"
        await self._run_render(chat_id, s, rebuild=True)

    async def _run_render(
        self,
        chat_id: int,
        s: Session,
        rebuild: bool,
        advance: bool = False,
        extra_notes: list[str] | None = None,
        verify_retry: bool = False,
    ) -> None:
        if s.busy:
            await self._say(chat_id, texts.BUSY)
            return
        s.busy = True
        try:
            if rebuild and s.mode == "hoyolab":
                await self._say(chat_id, texts.GHEP_RENDERING)
                if not await self._build_hoyolab(chat_id, s, extra_notes or [], verify_retry):
                    return
            elif rebuild and s.mode == "uid":
                await self._say(chat_id, texts.UID_RENDERING.format(uid=s.uid))
                if not await self._build_uid(chat_id, s, extra_notes or []):
                    return
            elif rebuild:
                await self._wait_enka(chat_id, s)
                await self._say(chat_id, texts.RENDERING)
                if not await self._build(chat_id, s):
                    return
            elif advance and s.render is not None:
                if not self._advance_background(s.render):
                    return
            await self._render_current(chat_id, s, first=rebuild)
        finally:
            s.busy = False
            self.sessions.touch(chat_id)

    async def _build(self, chat_id: int, s: Session) -> bool:
        """Cắt ảnh -> sheet, gắn cung mệnh, lập danh sách nền ứng viên."""
        t0 = time.perf_counter()
        try:
            sheet = await asyncio.to_thread(self.svc.cropper.build_sheet, s.image_paths(), s.work_dir)
        except Exception as e:
            why = error_text_vi(e)
            if why is None:
                log.exception("Cropper lỗi (chat %s)", chat_id)
                why = texts.UNKNOWN_WHY
            await self._say(chat_id, texts.CROP_FAIL.format(why=why))
            return False

        report = apply_constellations(sheet, s.enka_profile, s.manual_cm)
        chars = characters_in_account(s.enka_profile, sheet)
        plan = build_bg_plan(self.svc.backgrounds, self.svc.characters, chars, s.bg_override, self.svc.rng)
        notes = list(plan.notes) + self._badge_notes(report, sheet)
        s.render = RenderState(sheet=sheet, work_dir=s.work_dir, choices=plan.choices, index=0, notes=notes, account=chars)
        log.info(
            "phiên %s: %d ảnh, cắt xong %.2fs, nhân vật trong acc=%d, nền ứng viên=%d",
            chat_id, len(s.items), time.perf_counter() - t0, len(chars), len(plan.choices),
        )  # fmt: skip
        return True

    async def _build_uid(self, chat_id: int, s: Session, extra_notes: list[str]) -> bool:
        """Chế độ chỉ UID: Enka → mảnh tự vẽ (uidmode) → sheet; chọn nền như chế độ ảnh chụp."""
        assert self.svc.uidcard is not None and s.uid
        t0 = time.perf_counter()
        try:
            sheet, profile, report = await asyncio.to_thread(self.svc.uidcard.build, s.uid, s.uid_work_dir)
        except Exception as e:
            why = error_text_vi(e)
            if why is None:
                log.exception("Vẽ theo UID lỗi (chat %s, uid %s)", chat_id, s.uid)
                why = texts.UNKNOWN_WHY
            s.enka_error = why
            await self._say(chat_id, texts.UID_FAIL.format(uid=s.uid, why=why))
            return False
        s.enka_profile = profile
        badges = apply_constellations(sheet, profile, s.manual_cm)  # /cm vẫn thắng Enka
        chars = characters_in_account(profile, sheet)
        plan = build_bg_plan(self.svc.backgrounds, self.svc.characters, chars, s.bg_override, self.svc.rng)
        head = format_enka_summary(profile, self.svc.characters).split("\n")[0]
        notes = [head + "\n" + report.text_vi(), *extra_notes, *plan.notes]
        if badges.from_manual:
            notes.append(texts.BADGE_SUMMARY.format(enka=len(badges.from_enka), manual=len(badges.from_manual)))
        if badges.missing:
            notes.append(texts.UID_CM_HINT)
        s.render = RenderState(
            sheet=sheet, work_dir=s.uid_work_dir, choices=plan.choices, index=0, notes=notes, account=chars
        )
        log.info("chat %s: vẽ theo UID xong %.2fs, nhân vật=%d, nền ứng viên=%d",
                 chat_id, time.perf_counter() - t0, len(chars), len(plan.choices))  # fmt: skip
        return True

    async def _build_hoyolab(self, chat_id: int, s: Session, extra_notes: list[str], verify_retry: bool) -> bool:
        """Chế độ HoYoLAB: Sổ Tay Hành Trình → mảnh tự vẽ → sheet. Nền: ngẫu nhiên trong MỌI nhân vật acc có."""
        assert self.svc.hoyocard is not None and s.uid
        t0 = time.perf_counter()
        try:
            sheet, report = await asyncio.to_thread(self.svc.hoyocard.build, s.uid, s.hoyolab_work_dir, s.code)
        except Exception as e:
            if getattr(e, "kind", None) == "verification":
                await self._on_verification(chat_id, s, extra_notes, verify_retry)
                return False
            why = error_text_vi(e)
            if why is None:
                log.exception("Chế độ HoYoLAB lỗi (chat %s, uid %s)", chat_id, s.uid)
                why = texts.UNKNOWN_WHY
            await self._say(chat_id, texts.GHEP_FAIL.format(uid=s.uid, why=why))
            return False
        chars = list(getattr(report, "account_characters", None) or [])
        for c in characters_in_account(None, sheet):
            if c not in chars:
                chars.append(c)
        plan = build_bg_plan(self.svc.backgrounds, self.svc.characters, chars, s.bg_override, self.svc.rng)
        notes = [hoyolab_summary_vi(report, s.uid), *extra_notes, *plan.notes]
        s.render = RenderState(
            sheet=sheet, work_dir=s.hoyolab_work_dir, choices=plan.choices, index=0, notes=notes, account=chars
        )
        log.info("chat %s: HoYoLAB xong %.2fs, nhân vật acc=%d, đã vẽ=%d",
                 chat_id, time.perf_counter() - t0, len(chars), len(sheet.characters))  # fmt: skip
        return True

    async def _on_verification(self, chat_id: int, s: Session, notes: list[str], was_retry: bool) -> None:
        """HoYoLAB đòi captcha: CHỈ báo admin (link Sổ Tay Hành Trình + nút "Đã xác minh"); admin tự kéo
        captcha trên trình duyệt của họ. Bot không tải, không chụp, không chuyển captcha. Thử lại đúng 1 lần."""
        assert s.uid
        if was_retry:
            await self._say(chat_id, texts.GHEP_VERIFY_STILL.format(uid=s.uid))
            for admin in sorted(self.access.admin_ids):
                await self._say(admin, texts.VERIFY_ADMIN_STILL.format(uid=s.uid))
            return
        admins = sorted(self.access.admin_ids)
        if not admins:
            await self._say(chat_id, texts.GHEP_VERIFY_NO_ADMIN)
            return
        from gacard.hoyolab import BATTLE_CHRONICLE_URL

        self._verify_seq += 1
        key = str(self._verify_seq)
        self._pending_verify[key] = _PendingVerify(
            chat_id, s.uid, s.code, s.bg_override, list(notes), self.clock() + VERIFY_TTL
        )
        log.info("HoYoLAB đòi xác minh (chat %s, uid %s): đã báo admin", chat_id, s.uid)
        text = "\n".join([texts.VERIFY_ADMIN, BATTLE_CHRONICLE_URL, texts.VERIFY_ADMIN_FOR.format(uid=s.uid, chat_id=chat_id)])
        for admin in admins:
            await self._say(admin, text, buttons=[[(texts.BTN_VERIFIED, CB_VERIFIED + key)]])
        await self._say(chat_id, texts.GHEP_WAIT_VERIFY)

    async def _verified(self, chat_id: int, key: str) -> str | None:
        """Admin bấm "Đã xác minh": chạy lại đúng job /ghep đó MỘT lần."""
        if not self.access.is_admin(chat_id):
            return texts.CALLBACK_UNKNOWN
        job = self._pending_verify.pop(key, None)
        if job is None:
            return texts.VERIFY_GONE
        await self._start_ghep(job.chat_id, job.uid, job.code, job.bg_override, job.notes, retry=True)
        return texts.VERIFY_RETRYING

    def _advance_background(self, st: RenderState) -> bool:
        """Nút "Đổi nền": nền ngẫu nhiên khác trong các nền của nhân vật acc có (tránh lặp nền đang dùng)."""
        current = st.choices[st.index].background
        exclude = [current.id] if current is not None else []
        bg = self.svc.backgrounds.pick(list(st.account), self.svc.rng, exclude)
        if bg is None:
            return False
        st.choices.append(bg_choice(self.svc.characters, bg, BG_RANDOM_WHY))
        st.index = len(st.choices) - 1
        return True

    def _can_change_bg(self, st: RenderState) -> bool:
        current = st.choices[st.index].background
        ids = {b.id for b in self.svc.backgrounds.list() if b.character in set(st.account)}
        return bool(ids - ({current.id} if current is not None else set()))

    def _badge_notes(self, report: BadgeReport, sheet: Any) -> list[str]:
        notes: list[str] = []
        if report.from_enka or report.from_manual:
            notes.append(texts.BADGE_SUMMARY.format(enka=len(report.from_enka), manual=len(report.from_manual)))
        if not any(c.character for c in sheet.characters):
            notes.append(texts.NO_CARD_CHARS)
        if report.missing:
            names = ", ".join(self._name(s) for s in report.missing)
            notes.append(texts.MISSING_BADGES.format(names=names, example=report.missing[0]))
        if report.unmatched_manual:
            names = ", ".join(self._name(s) for s in report.unmatched_manual)
            notes.append(texts.UNMATCHED_BADGES.format(names=names))
        return notes

    async def _render_current(self, chat_id: int, s: Session, first: bool) -> None:
        st = s.render
        assert st is not None
        choice: BgChoice = st.choices[st.index]
        bg = choice.background
        request = RenderRequest(code=s.code, uid=s.uid, background_id=bg.id if bg else None)
        t0 = time.perf_counter()
        try:
            data = await asyncio.to_thread(self.svc.renderer.render, st.sheet, st.work_dir, request, self.svc.backgrounds)
        except Exception as e:
            why = error_text_vi(e)
            if why is None:
                log.exception("Render lỗi (chat %s)", chat_id)
                why = texts.UNKNOWN_WHY
            await self._say(chat_id, texts.RENDER_FAIL.format(why=why))
            return
        elapsed = time.perf_counter() - t0
        filename = output_filename(s.code, self.svc.now())
        try:
            self.config.out_dir.mkdir(parents=True, exist_ok=True)
            (self.config.out_dir / filename).write_bytes(data)
        except OSError:
            log.exception("Không lưu được bản sao kết quả")
        copy_name = saved_copy_name(s.mode, s.code, s.uid, self.svc.now())
        saved = ""
        try:  # bản sao cho chủ dự án (không tự xoá), tên có cả mã + UID
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            (self.config.output_dir / copy_name).write_bytes(data)
            saved = "\n" + texts.SAVED_COPY.format(name=copy_name)
        except OSError:
            log.exception("Không lưu được bản sao vào %s", self.config.output_dir)
        log.info("phiên %s: render %.2fs, %d KB, nền=%s", chat_id, elapsed, len(data) // 1024, bg.id if bg else "mặc định")

        await self.msg.send_photo(chat_id, data, caption=choice.reason)
        await self.msg.send_document(
            chat_id,
            data,
            filename,
            caption=texts.RENDER_DONE_DOC + saved,
            buttons=[[(texts.BTN_CHANGE_BG, CB_NEXT_BG), (texts.BTN_REDO, CB_REDO)]],
        )
        if first and st.notes:
            await self._say(chat_id, "\n".join(st.notes))
        if s.mode == "chup":
            s.rendered = True  # /uid, /ghep không khoá phiên: vẫn gửi tiếp ảnh chụp cho cùng acc được

    async def _callback(self, chat_id: int, data: str) -> str | None:
        if data.startswith(CB_VERIFIED):
            return await self._verified(chat_id, data[len(CB_VERIFIED):])
        s = self.sessions.get(chat_id)
        if data not in (CB_NEXT_BG, CB_REDO):
            return texts.CALLBACK_UNKNOWN
        if s is None or s.render is None:
            return texts.CALLBACK_EXPIRED
        self.sessions.touch(chat_id)
        if s.busy:
            return texts.BUSY
        if data == CB_NEXT_BG:
            if not self._can_change_bg(s.render):
                return texts.BG_CYCLE_ONLY_ONE
            await self._run_render(chat_id, s, rebuild=False, advance=True)
        else:
            await self._run_render(chat_id, s, rebuild=True)
        return None


# ======================================================================================
# Lớp vỏ aiogram (mỏng): đổi Update của Telegram thành lời gọi BotController
# ======================================================================================

from aiogram import BaseMiddleware, Bot, F, Router  # noqa: E402
from aiogram.filters import Command, CommandObject  # noqa: E402
from aiogram.types import (  # noqa: E402
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

TG_TEXT_LIMIT = 4000


def _markup(buttons: Buttons | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in buttons]
    )


def _chunks(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


class AiogramMessenger:
    """Messenger thật. Không văn bản nào đi qua parse_mode, nên không cần escape."""

    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_text(self, chat_id: int, text: str, buttons: Buttons | None = None) -> None:
        parts = _chunks(text)
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            await self.bot.send_message(chat_id, part, reply_markup=_markup(buttons) if last else None)

    async def send_photo(self, chat_id: int, data: bytes, caption: str | None = None) -> None:
        await self.bot.send_photo(chat_id, BufferedInputFile(data, filename="preview.jpg"), caption=caption)

    async def send_document(
        self, chat_id: int, data: bytes, filename: str, caption: str | None = None, buttons: Buttons | None = None
    ) -> None:
        await self.bot.send_document(
            chat_id, BufferedInputFile(data, filename=filename), caption=caption, reply_markup=_markup(buttons)
        )

    async def download(self, file_id: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await self.bot.download(file_id, destination=dest)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self.bot.delete_message(chat_id, message_id)


def media_from_message(message: Message) -> IncomingMedia | None:
    if message.photo:
        p = message.photo[-1]
        return IncomingMedia(
            message_id=message.message_id,
            file_id=p.file_id,
            is_document=False,
            file_size=p.file_size,
            media_group_id=message.media_group_id,
            caption=message.caption,
        )
    d = message.document
    if d is not None:
        return IncomingMedia(
            message_id=message.message_id,
            file_id=d.file_id,
            is_document=True,
            file_size=d.file_size,
            file_name=d.file_name,
            mime_type=d.mime_type,
            media_group_id=message.media_group_id,
            caption=message.caption,
        )
    return None


def private_username(chat_id: int, user: Any) -> str | None:
    """Username của người gửi, chỉ khi đang ở chat riêng với chính họ (chat.id == user.id).

    Nhờ vậy lời mời /moi @ten không vô tình cấp quyền cho cả nhóm mà người đó nhắn từ trong đó."""
    if user is None or getattr(user, "id", None) != chat_id:
        return None
    return getattr(user, "username", None) or None


class WhitelistMiddleware(BaseMiddleware):
    """Chat không có trong danh sách: một câu từ chối, handler không chạy."""

    def __init__(self, controller: BotController):
        self.controller = controller

    async def __call__(self, handler, event, data):  # type: ignore[no-untyped-def]
        if isinstance(event, CallbackQuery):
            chat_id = event.message.chat.id if event.message is not None else event.from_user.id
        else:
            chat_id = event.chat.id
        if not await self.controller.guard(chat_id, private_username(chat_id, event.from_user)):
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None
        return await handler(event, data)


def build_router(controller: BotController) -> Router:
    router = Router(name="gacard")
    mw = WhitelistMiddleware(controller)
    router.message.outer_middleware(mw)
    router.callback_query.outer_middleware(mw)

    @router.message(F.photo | F.document)
    async def on_media(message: Message) -> None:
        media = media_from_message(message)
        if media is not None:
            await controller.handle_media(message.chat.id, media)

    @router.message(Command(*COMMANDS))
    async def on_command(message: Message, command: CommandObject) -> None:
        await controller.handle_command(message.chat.id, command.command, command.args, message_id=message.message_id)

    @router.message(F.text.startswith("/"))
    async def on_unknown_command(message: Message) -> None:
        await controller.handle_command(message.chat.id, "?", message.text, message_id=message.message_id)

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        await controller.handle_text(message.chat.id, text=message.text, message_id=message.message_id)

    @router.callback_query()
    async def on_callback(cq: CallbackQuery) -> None:
        chat_id = cq.message.chat.id if cq.message is not None else cq.from_user.id
        toast = await controller.handle_callback(chat_id, cq.data or "")
        await cq.answer(toast)

    return router
