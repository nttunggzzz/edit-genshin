"""/uid vẽ ảnh + lưu bản sao, /xong lưu bản sao, /moi /bomoi /danhsach (admin) và allowed.json."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from aiogram.methods import SendMessage

from gacard.bot import texts
from gacard.bot.access import AllowList
from gacard.bot.config import DEFAULT_ADMIN_CHAT_IDS, DEFAULT_OUTPUT_DIR, load_config
from gacard.bot.handlers import BotController
from gacard.bot.services import parse_uid_args, saved_copy_name

from .test_bot_handlers import _cmd, build
from .test_bot_support import FakeCard, FakeEnkaError, FakeSheet, make_profile, make_rig, photo


def run(coro):
    return asyncio.run(coro)


class FakeReport:
    def text_vi(self) -> str:
        return "Chế độ chỉ UID: AR 60, Cấp Thế Giới 8."


class FakeUidCard:
    def __init__(self, error: Exception | None = None):
        self.calls: list = []
        self.error = error

    def build(self, uid, work_dir):
        self.calls.append((uid, work_dir, __import__("threading").current_thread().name))
        if self.error:
            raise self.error
        work_dir.mkdir(parents=True, exist_ok=True)
        sheet = FakeSheet([FakeCard("columbina", 2), FakeCard("zibai", None)])
        return sheet, make_profile(uid, [("columbina", 2), ("zibai", None)]), FakeReport()


def uid_rig(tmp_path, **kw):
    r = make_rig(tmp_path, **kw)
    r.ctl.svc.uidcard = FakeUidCard()
    return r


# ---------------------------------------------------------------- /uid


def test_uid_renders_sends_and_saves_copy(tmp_path):
    async def go():
        r = uid_rig(tmp_path)
        r.store.seed("columbina")
        await r.ctl.handle_command(1, "uid", "887630784 ma 1116 nen columbina")
        return r

    r = run(go())
    assert r.msg.texts()[0] == texts.UID_RENDERING.format(uid="887630784")  # "đang vẽ…" trước
    assert r.msg.kinds()[1:3] == ["photo", "doc"]
    uid, work_dir, thread = r.ctl.svc.uidcard.calls[0]
    assert uid == "887630784" and thread != "MainThread"  # chạy trong asyncio.to_thread
    saved = tmp_path / "output" / "acc-1116-887630784-uid.jpg"
    assert saved.read_bytes() == b"\xff\xd8JPEGBYTES\xff\xd9"
    req = r.renderer.calls[0]["request"]
    assert req.code == "1116" and req.uid == "887630784" and req.background_id == "bg1"
    doc = next(m for m in r.msg.sent if m["kind"] == "doc")
    assert "887630784" not in doc["filename"]  # file gửi đi không lộ UID
    assert "acc-1116-887630784-uid.jpg" in doc["caption"]
    note = r.msg.last_text()
    assert "Chế độ chỉ UID" in note and "Lữ Khách" in note and texts.UID_CM_HINT in note
    assert r.renderer.calls[0]["badges"] == {"columbina": 2, "zibai": None}


def test_uid_without_code_and_redo_and_cm_override(tmp_path):
    async def go():
        r = uid_rig(tmp_path)
        await r.ctl.handle_command(1, "uid", "887630784")
        await r.ctl.handle_command(1, "cm", "zibai 1")
        await r.ctl.handle_callback(1, "redo")
        return r

    r = run(go())
    assert (tmp_path / "output" / "acc-887630784-uid.jpg").is_file()
    assert r.renderer.calls[0]["request"].code is None
    assert len(r.ctl.svc.uidcard.calls) == 2  # Làm lại = tra + vẽ lại theo UID, không gọi cropper
    assert r.cropper.calls == []
    assert r.renderer.calls[1]["badges"] == {"columbina": 2, "zibai": 1}


def test_uid_bad_and_enka_error(tmp_path):
    async def go():
        r = uid_rig(tmp_path)
        await r.ctl.handle_command(1, "uid", "12ab ma 1")
        r.ctl.svc.uidcard.error = FakeEnkaError("UID không tồn tại trên Enka.")
        await r.ctl.handle_command(1, "uid", "887630784")
        return r

    r = run(go())
    t = r.msg.texts()
    assert t[0] == texts.BAD_UID
    assert "UID không tồn tại trên Enka." in t[-1] and r.renderer.calls == []
    assert not (tmp_path / "output").exists()


def test_uid_without_builder_keeps_old_behaviour(tmp_path):
    async def go():
        r = make_rig(tmp_path)  # svc.uidcard = None
        await r.ctl.handle_command(1, "uid", "850363095")
        await r.settle()
        return r

    r = run(go())
    assert r.msg.texts()[:2] == [texts.UID_SET.format(uid="850363095"), texts.ENKA_FETCHING.format(uid="850363095")]
    assert r.renderer.calls == []


def test_photos_after_uid_start_new_screenshot_session(tmp_path):
    async def go():
        r = uid_rig(tmp_path)
        await r.ctl.handle_command(1, "uid", "887630784 ma 1116")
        await r.ctl.handle_media(1, photo(5, r.msg, caption="ma 2000 uid 850363095"))
        await r.settle()
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    s = r.sessions.get(1)
    assert s.mode == "chup" and len(s.items) == 1 and s.code == "2000"
    assert (tmp_path / "output" / "acc-2000-850363095-chup.jpg").is_file()


# ---------------------------------------------------------------- /xong lưu bản sao


def test_xong_saves_copy_with_code_and_uid(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg, caption="ma 1116 uid 887630784 nen columbina"))
        await r.ctl.handle_media(1, photo(2, r.msg))
        await r.settle()
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    saved = tmp_path / "output" / "acc-1116-887630784-chup.jpg"
    assert saved.read_bytes() == b"\xff\xd8JPEGBYTES\xff\xd9"
    assert (r.config.out_dir / "acc-1116-20261021-1405.jpg").is_file()  # bản tạm cũ vẫn còn


def test_xong_without_code_or_uid_falls_back_to_timestamp(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.settle()
        await r.ctl.handle_command(1, "xong", None)
        return r

    run(go())
    assert [p.name for p in (tmp_path / "output").iterdir()] == ["acc-20261021-140533-chup.jpg"]


def test_saved_copy_name_and_parse_uid_args():
    when = datetime(2026, 10, 28, 9, 1, 2)
    assert saved_copy_name("uid", "CN#1076", "887630784", when) == "acc-CN1076-887630784-uid.jpg"
    assert saved_copy_name("chup", None, "887630784", when) == "acc-887630784-chup.jpg"
    assert saved_copy_name("chup", "", None, when) == "acc-20261028-090102-chup.jpg"
    a = parse_uid_args("887630784 ma 1116 nen hu tao")
    assert (a.uid, a.code, a.nen, a.errors) == ("887630784", "1116", "hu tao", [])
    assert parse_uid_args("uid 887630784 ma 7").uid == "887630784"
    assert parse_uid_args("abc").uid is None
    assert parse_uid_args("887630784 ma !!").errors


# ---------------------------------------------------------------- /moi /bomoi /danhsach


def test_moi_bomoi_admin_only_and_persistent(tmp_path):
    async def go():
        r = make_rig(tmp_path)  # admin = 1, .env cho phép {1, 2}
        await r.ctl.handle_command(2, "moi", "555")  # chat 2 được phép nhưng không phải admin
        await r.ctl.handle_command(2, "danhsach", None)
        refused = list(r.msg.texts())
        await r.ctl.handle_command(555, "start", None)  # chưa được mời
        await r.ctl.handle_command(1, "moi", "555")
        await r.ctl.handle_command(1, "moi", "555")
        await r.ctl.handle_command(555, "start", None)  # có hiệu lực ngay
        await r.ctl.handle_command(1, "moi", "abc")
        await r.ctl.handle_command(1, "bomoi", "2")  # bỏ cả ID ghi trong .env
        await r.ctl.handle_command(1, "bomoi", "1")  # admin không tự bỏ được
        await r.ctl.handle_command(1, "danhsach", None)
        return r, refused

    r, refused = run(go())
    assert refused == [texts.ADMIN_ONLY, texts.ADMIN_ONLY]
    assert not r.ctl.is_allowed(2) and r.ctl.is_allowed(555) and r.ctl.is_allowed(1)
    t = r.msg.texts()
    assert texts.MOI_DONE.format(id=555) in t and texts.MOI_ALREADY.format(id=555) in t
    assert texts.START in t and texts.BAD_USERNAME.format(raw="abc") in t
    assert texts.BOMOI_DONE.format(id=2) in t and texts.BOMOI_ADMIN.format(id=1) in t
    assert "- 555 (/moi)" in t[-1] and "- 1 (admin)" in t[-1] and "- 2 " not in t[-1]

    data = json.loads((r.config.data_dir / "allowed.json").read_text(encoding="utf-8"))
    assert data == {"added": [555], "removed": [2], "usernames": {}}
    # khởi động lại bot: danh sách vẫn còn
    again = BotController(r.config, r.ctl.svc, r.msg)
    assert again.is_allowed(555) and not again.is_allowed(2)


def test_allowlist_reads_file_edits_live_and_survives_bad_file(tmp_path):
    f = tmp_path / "allowed.json"
    acl = AllowList(f, env_ids={10}, admin_ids={1})
    assert acl.ids() == {1, 10}
    assert acl.add(20) and not acl.add(20)
    assert acl.remove(10) and not acl.remove(10)
    assert acl.add(10)  # mời lại ID .env: gỡ khỏi "removed"
    assert json.loads(f.read_text(encoding="utf-8")) == {"added": [20], "removed": [], "usernames": {}}
    f.write_text('{"added": [30], "removed": []}', encoding="utf-8")
    import os

    os.utime(f, (1, 1))  # đổi mtime để chắc chắn nhận ra file được sửa tay
    assert acl.is_allowed(30) and not acl.is_allowed(20)
    f.write_text("{hỏng", encoding="utf-8")
    os.utime(f, (2, 2))
    assert acl.ids() == {1, 10}


def test_whitelist_middleware_uses_live_list(tmp_path):
    async def go():
        rig, ctl, dp, bot, tg = build(tmp_path)
        await dp.feed_update(bot, _cmd(777, "/start"))
        await dp.feed_update(bot, _cmd(1, "/moi 777", 2))
        await dp.feed_update(bot, _cmd(777, "/huongdan", 3))
        return [c.text for c in tg.calls if isinstance(c, SendMessage)]

    t = run(go())
    assert "777" in t[0] and t[1] == texts.MOI_DONE.format(id=777) and t[2] == texts.HUONGDAN
    assert len(texts.HUONGDAN.splitlines()) <= 5


# ---------------------------------------------------------------- cấu hình


def test_config_admin_and_output_dir(tmp_path):
    env = {"GACARD_ENV_FILE": str(tmp_path / "x.env"), "GACARD_BOT_TOKEN": "1:X"}
    cfg = load_config(env)
    assert cfg.admin_chat_ids == DEFAULT_ADMIN_CHAT_IDS and cfg.output_dir == DEFAULT_OUTPUT_DIR
    assert cfg.allowed_file == cfg.data_dir / "allowed.json"
    cfg = load_config({**env, "GACARD_ADMIN_CHAT_IDS": "7, 8", "GACARD_OUTPUT_DIR": "out2"})
    assert cfg.admin_chat_ids == frozenset({7, 8})
    assert cfg.output_dir == tmp_path.resolve() / "out2"
