"""/help (/trogiup) và mời theo @username: /moi @ten, nhận diện chat ID khi người đó nhắn bot, /bomoi, /danhsach."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from gacard.bot import texts
from gacard.bot.access import AllowList, normalize_username
from gacard.bot.handlers import COMMANDS, BotController

from .test_bot_handlers import build
from .test_bot_support import make_rig

ADMIN = 1  # make_rig: admin = 1, .env cho phép {1, 2}


def run(coro):
    return asyncio.run(coro)


def _umsg(chat_id, text, mid=1, username=None, user_id=None, chat_type="private") -> Message:
    name = text.split()[0]
    entities = [MessageEntity(type="bot_command", offset=0, length=len(name))] if text.startswith("/") else None
    return Message(
        message_id=mid,
        date=datetime.now(),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=chat_id if user_id is None else user_id, is_bot=False, first_name="t", username=username),
        text=text,
        entities=entities,
    )


def _ucmd(chat_id, text, mid=1, **kw) -> Update:
    return Update(update_id=mid, message=_umsg(chat_id, text, mid, **kw))


def _sent(tg):
    return [(c.chat_id, c.text) for c in tg.calls if isinstance(c, SendMessage)]


def _allowed_json(rig) -> dict:
    return json.loads((rig.config.data_dir / "allowed.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- /help


def test_help_lists_only_real_commands_and_marks_admin(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(2, "help", None)  # chat được phép, không phải admin
        await r.ctl.handle_command(ADMIN, "trogiup", None)
        return r

    r = run(go())
    user_help, admin_help = r.msg.texts()
    assert user_help == texts.help_text(False) and admin_help == texts.help_text(True)

    mentioned = set(re.findall(r"/([a-z]+)\b", user_help))
    assert mentioned <= set(COMMANDS), mentioned - set(COMMANDS)  # không bịa lệnh
    assert set(COMMANDS) <= mentioned, set(COMMANDS) - mentioned  # không sót lệnh
    for _title, items in texts.HELP_GROUPS:
        assert all(cmd.lstrip("/") in COMMANDS for cmd, _ in items)

    titles = [t for t, _ in texts.HELP_GROUPS]
    assert titles == ["Tạo ảnh", "Thông tin cung mệnh và nền", "Quản trị", "Khác"]
    for line in user_help.splitlines():
        if any(c in line for c in ("/moi", "/bomoi", "/danhsach")) and line.startswith("- "):
            assert line.endswith("(chỉ admin)"), line
    assert texts.HELP_NOT_ADMIN in user_help and texts.HELP_NOT_ADMIN not in admin_help
    assert "/uid 887630784" in user_help and "/cm columbina 2" in user_help


def test_start_huongdan_and_hints_point_to_help():
    assert "/help" in texts.START and "/help" in texts.HUONGDAN
    assert len(texts.HUONGDAN.splitlines()) <= 5
    assert "/help" in texts.UNKNOWN_COMMAND and "/help" in texts.TEXT_HINT


def test_help_routed_through_aiogram(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        await dp.feed_update(bot, _ucmd(2, "/help"))
        await dp.feed_update(bot, _ucmd(2, "/trogiup", 2))
        return tg

    t = [x for _, x in _sent(run(go()))]
    assert t == [texts.help_text(False)] * 2


# ---------------------------------------------------------------- kiểm tra username


def test_normalize_username_rules():
    assert normalize_username("@Ten_Ban") == "ten_ban"
    assert normalize_username("abcde") == "abcde"
    assert normalize_username("a" * 32) == "a" * 32
    for bad in ("", "@", "abcd", "@abcd", "a" * 33, "ten-ban", "ten ban", "tên_bạn", "@@tenban"):
        assert normalize_username(bad) is None, bad


def test_moi_username_stored_lowercase_and_validated(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(ADMIN, "moi", "@Ten_Ban")
        await r.ctl.handle_command(ADMIN, "moi", "TEN_BAN")  # không phân biệt hoa thường
        await r.ctl.handle_command(ADMIN, "moi", "ten.ban")  # ("ten-ban" bị chặn như tài khoản/mật khẩu)
        await r.ctl.handle_command(ADMIN, "moi", "@abc")
        await r.ctl.handle_command(ADMIN, "moi", None)
        await r.ctl.handle_command(ADMIN, "moi", "123456789")  # chat ID vẫn dùng được
        return r

    r = run(go())
    assert r.msg.texts() == [
        texts.MOI_USER_DONE.format(name="ten_ban"),
        texts.MOI_USER_ALREADY.format(name="ten_ban"),
        texts.BAD_USERNAME.format(raw="ten.ban"),
        texts.BAD_USERNAME.format(raw="@abc"),
        texts.NEED_MOI_ARGS,
        texts.MOI_DONE.format(id=123456789),
    ]
    assert _allowed_json(r) == {"added": [123456789], "removed": [], "usernames": {"ten_ban": None}}


def test_non_admin_refused_for_username_commands(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(2, "moi", "@ten_ban")
        await r.ctl.handle_command(2, "bomoi", "@ten_ban")
        await r.ctl.handle_command(2, "danhsach", None)
        return r

    r = run(go())
    assert r.msg.texts() == [texts.ADMIN_ONLY] * 3
    assert not (r.config.data_dir / "allowed.json").exists()


# ---------------------------------------------------------------- nhận diện qua middleware


def test_invited_username_let_through_recorded_and_admin_notified_once(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        await dp.feed_update(bot, _ucmd(4242, "/start", 1, username="TenBan_99"))  # chưa mời → từ chối
        await dp.feed_update(bot, _ucmd(ADMIN, "/moi @tenban_99", 2))
        await dp.feed_update(bot, _ucmd(4242, "/huongdan", 3, username="TenBan_99"))  # hoa/thường khác
        await dp.feed_update(bot, _ucmd(4242, "xin chào", 4, username="TenBan_99"))
        await dp.feed_update(bot, _ucmd(4242, "/mau", 5))  # đổi/bỏ username: chat ID đã được ghi
        return rig, tg

    rig, tg = run(go())
    sent = _sent(tg)
    to_user = [t for cid, t in sent if cid == 4242]
    to_admin = [t for cid, t in sent if cid == ADMIN]
    assert "4242" in to_user[0]  # lời từ chối lần đầu
    assert to_user[1:] == [texts.HUONGDAN, texts.TEXT_HINT, texts.MAU]
    recog = texts.USERNAME_RECOGNIZED.format(name="tenban_99", id=4242)
    assert to_admin == [texts.MOI_USER_DONE.format(name="tenban_99"), recog]  # báo đúng một lần
    assert _allowed_json(rig) == {"added": [4242], "removed": [], "usernames": {"tenban_99": 4242}}
    # khởi động lại bot: vẫn được phép dù không có username
    again = BotController(rig.config, rig.ctl.svc, rig.msg)
    assert again.is_allowed(4242)


def test_username_not_invited_or_group_chat_stays_refused(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        await dp.feed_update(bot, _ucmd(ADMIN, "/moi @ten_ban", 1))
        await dp.feed_update(bot, _ucmd(5555, "/start", 2, username="nguoi_khac"))
        # người được mời nhắn từ trong một nhóm: không cấp quyền cho cả nhóm
        await dp.feed_update(bot, _ucmd(-1001, "/start", 3, username="ten_ban", user_id=4242, chat_type="group"))
        return rig, tg

    rig, tg = run(go())
    sent = _sent(tg)
    assert [cid for cid, _ in sent] == [ADMIN, 5555, -1001]
    assert "5555" in sent[1][1] and "-1001" in sent[2][1]
    assert _allowed_json(rig)["usernames"] == {"ten_ban": None}
    assert not rig.ctl.access.is_allowed(-1001)


def test_invited_username_recognised_from_callback_query(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        await dp.feed_update(bot, _ucmd(ADMIN, "/moi ten_ban", 1))
        cq = CallbackQuery(
            id="c", chat_instance="x", data="redo",
            from_user=User(id=4242, is_bot=False, first_name="t", username="Ten_Ban"),
            message=_umsg(4242, "x", 2),
        )  # fmt: skip
        await dp.feed_update(bot, Update(update_id=2, callback_query=cq))
        return rig, tg

    rig, tg = run(go())
    assert rig.ctl.access.usernames() == {"ten_ban": 4242} and rig.ctl.access.is_allowed(4242)
    assert (ADMIN, texts.USERNAME_RECOGNIZED.format(name="ten_ban", id=4242)) in _sent(tg)


# ---------------------------------------------------------------- /bomoi, /danhsach


def test_bomoi_username_and_danhsach(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(ADMIN, "moi", "@ten_ban")
        await r.ctl.handle_command(ADMIN, "moi", "@chua_nhan")
        await r.ctl.handle_command(4242, "start", None, username="Ten_Ban")  # nhận diện
        await r.ctl.handle_command(ADMIN, "danhsach", None)
        listing = r.msg.texts()[-1]
        await r.ctl.handle_command(ADMIN, "bomoi", "@TEN_BAN")
        await r.ctl.handle_command(ADMIN, "bomoi", "@chua_nhan")
        await r.ctl.handle_command(ADMIN, "bomoi", "@khong_co_ai")
        await r.ctl.handle_command(ADMIN, "bomoi", "ten ban")
        n_before = len(r.msg.texts())
        await r.ctl.handle_command(4242, "start", None, username="Ten_Ban")  # hết quyền
        return r, listing, n_before

    r, listing, n_before = run(go())
    assert "- 4242 (/moi @ten_ban)" in listing and "- 1 (admin)" in listing
    assert texts.DANHSACH_USERS_HEAD.format(n=2) in listing
    assert texts.DANHSACH_USER_SEEN.format(name="ten_ban", id=4242) in listing
    assert texts.DANHSACH_USER_UNSEEN.format(name="chua_nhan") in listing
    t = r.msg.texts()
    assert texts.BOMOI_USER_DONE_ID.format(name="ten_ban", id=4242) in t
    assert texts.BOMOI_USER_DONE.format(name="chua_nhan") in t
    assert texts.BOMOI_USER_NOT_FOUND.format(name="khong_co_ai") in t
    assert texts.BAD_USERNAME.format(raw="ten") in t
    assert "4242" in t[n_before] and t[n_before] != texts.START  # bị từ chối, không tự nhận diện lại
    assert _allowed_json(r) == {"added": [], "removed": [], "usernames": {}}


def test_bomoi_chat_id_also_drops_linked_username(tmp_path):
    f = tmp_path / "allowed.json"
    acl = AllowList(f, env_ids={10}, admin_ids={ADMIN})
    assert acl.add_username("ten_ban") and acl.claim(4242, "@Ten_Ban")
    assert not acl.claim(4242, "ten_ban")  # đã nhận diện: không báo lại
    assert acl.source(4242) == "/moi @ten_ban"
    assert acl.remove(4242)
    assert acl.usernames() == {} and not acl.claim(4242, "ten_ban") and not acl.is_allowed(4242)


def test_allowlist_reads_hand_edited_username_list(tmp_path):
    f = tmp_path / "allowed.json"
    f.write_text('{"added": [], "removed": [], "usernames": ["@Ten_Ban", "x"]}', encoding="utf-8")
    acl = AllowList(f, admin_ids={ADMIN})
    assert acl.usernames() == {"ten_ban": None}  # "x" sai quy tắc → bỏ qua
    assert acl.claim(4242, "TEN_BAN") and acl.is_allowed(4242)
