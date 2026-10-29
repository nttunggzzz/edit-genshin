"""Chạy Update giả qua Dispatcher thật của aiogram với session giả (không gọi Telegram)."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, GetFile, SendDocument, SendMessage, SendPhoto
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    Message,
    MessageEntity,
    PhotoSize,
    Update,
    User,
)
from PIL import Image

from gacard.bot import texts
from gacard.bot.handlers import AiogramMessenger, BotController, _chunks, build_router

from .test_bot_support import make_rig


class FakeTelegramSession(BaseSession):
    """Ghi lại mọi method mà bot gọi, trả kết quả tối thiểu."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.png = io.BytesIO()
        Image.new("RGB", (1920, 1080), (1, 2, 3)).save(self.png, format="PNG")

    async def close(self) -> None:  # pragma: no cover
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        now = datetime.now()
        if isinstance(method, (SendMessage, SendPhoto, SendDocument)):
            return Message(message_id=900 + len(self.calls), date=now, chat=Chat(id=method.chat_id, type="private"))
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="photos/x.png")
        if isinstance(method, AnswerCallbackQuery):
            return True
        raise AssertionError(f"method không mong đợi: {method}")

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        data = self.png.getvalue()
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


def _msg(chat_id, mid=1, **kw) -> Message:
    return Message(
        message_id=mid,
        date=datetime.now(),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=chat_id, is_bot=False, first_name="t"),
        **kw,
    )


def _cmd(chat_id, text, mid=1) -> Update:
    name = text.split()[0]
    return Update(update_id=mid, message=_msg(chat_id, mid, text=text, entities=[MessageEntity(type="bot_command", offset=0, length=len(name))]))


def build(tmp_path):
    rig = make_rig(tmp_path)
    tg = FakeTelegramSession()
    bot = Bot(token="123456:ABC-DEF", session=tg)
    # Dùng messenger thật của aiogram (qua session giả) thay cho FakeMessenger
    ctl = BotController(rig.config, rig.ctl.svc, AiogramMessenger(bot), sessions=rig.sessions, debouncer=rig.ctl.debouncer, clock=rig.clock)
    dp = Dispatcher()
    dp.include_router(build_router(ctl))
    return rig, ctl, dp, bot, tg


def _sent_texts(tg):
    return [c.text for c in tg.calls if isinstance(c, SendMessage)]


def test_command_routing_and_args(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        await dp.feed_update(bot, _cmd(1, "/start"))
        await dp.feed_update(bot, _cmd(1, "/cm columbina 2 zibai 0", 2))
        await dp.feed_update(bot, _cmd(1, "/ma 1043", 3))
        await dp.feed_update(bot, _cmd(1, "/khongco", 4))
        await dp.feed_update(bot, Update(update_id=5, message=_msg(1, 5, text="xin chào")))
        return rig, tg

    rig, tg = asyncio.run(go())
    t = _sent_texts(tg)
    assert t[0] == texts.START
    assert "Columbina C2" in t[1] and "Zibai C0" in t[1]
    assert t[2] == texts.CODE_SET.format(code="1043")
    assert t[3] == texts.UNKNOWN_COMMAND and t[4] == texts.TEXT_HINT
    s = rig.sessions.get(1)
    assert s.manual_cm == {"columbina": 2, "zibai": 0} and s.code == "1043"


def test_whitelist_middleware_blocks_stranger_everywhere(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        photo = PhotoSize(file_id="f1", file_unique_id="u1", width=1920, height=1080, file_size=10)
        await dp.feed_update(bot, _cmd(777, "/start"))
        await dp.feed_update(bot, _cmd(777, "/xong", 2))
        await dp.feed_update(bot, Update(update_id=3, message=_msg(777, 3, photo=[photo])))
        cq = CallbackQuery(id="c", from_user=User(id=777, is_bot=False, first_name="t"), chat_instance="x",
                           data="bg:next", message=_msg(777, 4, text="x"))
        await dp.feed_update(bot, Update(update_id=4, callback_query=cq))
        return rig, tg

    rig, tg = asyncio.run(go())
    assert len(_sent_texts(tg)) == 1 and "777" in _sent_texts(tg)[0]
    assert not any(isinstance(c, GetFile) for c in tg.calls)  # không tải gì
    assert rig.sessions.active_chats() == [] and rig.cropper.calls == []


def test_photo_and_document_flow_then_xong_and_buttons(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        photo = PhotoSize(file_id="p1", file_unique_id="u1", width=320, height=180, file_size=10)
        big = PhotoSize(file_id="p2", file_unique_id="u2", width=1280, height=720, file_size=10)
        await dp.feed_update(bot, Update(update_id=1, message=_msg(1, 10, photo=[photo, big], media_group_id="g", caption="ma 55")))
        document = Document(file_id="d1", file_unique_id="u3", file_name="a.png", mime_type="image/png", file_size=10)
        await dp.feed_update(bot, Update(update_id=2, message=_msg(1, 11, document=document, media_group_id="g")))
        await ctl.debouncer.drain()
        await dp.feed_update(bot, _cmd(1, "/xong", 3))
        cq = CallbackQuery(id="c", from_user=User(id=1, is_bot=False, first_name="t"), chat_instance="x",
                           data="redo", message=_msg(1, 4, text="x"))
        await dp.feed_update(bot, Update(update_id=4, callback_query=cq))
        return rig, tg

    rig, tg = asyncio.run(go())
    files = [c.file_id for c in tg.calls if isinstance(c, GetFile)]
    assert files == ["p2", "d1"]  # photo[-1] được chọn, album không tải ảnh nhỏ
    summaries = [t for t in _sent_texts(tg) if t.startswith("Đã nhận")]
    assert len(summaries) == 1 and "Đã nhận 2 ảnh" in summaries[0] and "mã 55" in summaries[0]
    assert sum(isinstance(c, SendPhoto) for c in tg.calls) == 2  # /xong + Làm lại
    docs = [c for c in tg.calls if isinstance(c, SendDocument)]
    assert docs[0].document.filename == "acc-55-20261021-1405.jpg"
    keyboard = docs[0].reply_markup.inline_keyboard
    assert [(b.text, b.callback_data) for b in keyboard[0]] == [("Đổi nền", "bg:next"), ("Làm lại", "redo")]
    assert any(isinstance(c, AnswerCallbackQuery) for c in tg.calls)


def test_chunks_split_long_text():
    text = "\n".join("dòng " + str(i) * 50 for i in range(300))
    parts = _chunks(text, 4000)
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
    assert "\n".join(parts) == text
    assert _chunks("x" * 9000, 4000) == ["x" * 4000, "x" * 4000, "x" * 1000]
