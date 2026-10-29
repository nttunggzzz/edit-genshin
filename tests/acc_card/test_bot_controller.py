from __future__ import annotations

import asyncio

from gacard.bot import texts

from .test_bot_support import (
    FakeCropper,
    FakeEnka,
    FakeEnkaError,
    doc,
    make_profile,
    make_rig,
    photo,
)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- whitelist


def test_whitelist_refuses_once_and_processes_nothing(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(999, "start", None)
        await r.ctl.handle_command(999, "xong", None)
        await r.ctl.handle_media(999, photo(1, r.msg))
        await r.settle()
        return r

    r = run(go())
    assert len(r.msg.sent) == 1  # đúng một câu từ chối, các lần sau im lặng
    assert "999" in r.msg.sent[0]["text"]
    assert r.msg.downloads == []
    assert r.sessions.active_chats() == []
    assert not (r.config.sessions_dir.exists() and any(r.config.sessions_dir.iterdir()))
    assert r.cropper.calls == [] and r.renderer.calls == []


def test_allowed_chat_gets_start(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "/start@gacardbot", None)
        return r

    r = run(go())
    assert r.msg.last_text() == texts.START


# ---------------------------------------------------------------- phiên + album


def test_album_is_debounced_into_one_summary(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        for i in (11, 12, 13):
            await r.ctl.handle_media(1, photo(i, r.msg, group="alb"))
        assert r.msg.sent == []  # chưa gửi gì trong lúc debounce
        await r.settle()
        return r

    r = run(go())
    assert len(r.msg.texts()) == 1
    assert "Đã nhận 3 ảnh" in r.msg.texts()[0]
    s = r.sessions.get(1)
    assert len(s.items) == 3 and all(m.path.exists() for m in s.items)
    assert s.dir.parent == r.config.sessions_dir and s.dir.name.startswith("1-")


def test_concurrent_downloads_keep_message_order(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await asyncio.gather(*(r.ctl.handle_media(1, photo(i, r.msg, group="a")) for i in (30, 10, 20)))
        await r.settle()
        return r

    r = run(go())
    names = [p.name for p in r.sessions.get(1).image_paths()]
    assert [n.split("-")[1].split(".")[0] for n in names] == ["10", "20", "30"]


def test_small_image_warns_to_send_as_file(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg, w=1000, h=700))
        await r.ctl.handle_media(1, photo(2, r.msg, w=1280, h=720))  # đúng 1280: không cảnh báo
        await r.settle()
        return r

    r = run(go())
    text = r.msg.texts()[0]
    assert "ảnh #1 nhỏ (1000x700" in text and "File" in text
    assert "ảnh #2" not in text


def test_max_images_and_size_limits(tmp_path):
    async def go():
        r = make_rig(tmp_path, max_images=3)
        for i in range(1, 6):
            await r.ctl.handle_media(1, photo(i, r.msg))
        await r.ctl.handle_media(1, doc(50, r.msg, size=25 * 1024 * 1024))
        await r.settle()
        return r

    r = run(go())
    assert len(r.sessions.get(1).items) == 3
    assert len(r.msg.downloads) == 3  # ảnh thừa và ảnh quá lớn không bị tải
    assert any("tối đa 3 ảnh" in t for t in r.msg.texts())
    assert any("quá lớn" in t for t in r.msg.texts())


def test_non_image_document_and_webp_document(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, doc(1, r.msg, name="a.pdf", mime="application/pdf"))
        assert r.msg.texts() == [texts.NOT_IMAGE]
        await r.ctl.handle_media(1, doc(2, r.msg, name="b.webp", mime="image/webp", w=2000, h=1000))
        await r.settle()
        return r

    r = run(go())
    items = r.sessions.get(1).items
    assert len(items) == 1 and items[0].path.suffix == ".webp" and items[0].is_document


def test_caption_options_on_first_image(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg, caption="ma 1043 uid 850363095 nen columbina"))
        await r.settle()
        return r

    r = run(go())
    s = r.sessions.get(1)
    assert (s.code, s.uid, s.bg_override) == ("1043", "850363095", "columbina")
    assert r.enka.calls == ["850363095"]
    assert any("Enka UID 850363095" in t for t in r.msg.texts())
    summary = [t for t in r.msg.texts() if t.startswith("Đã nhận")][0]
    assert "mã 1043" in summary and "UID 850363095" in summary and "Columbina" in summary


def test_caption_bad_values_are_reported_not_fatal(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg, caption="uid 123 ma 1043"))
        await r.settle()
        return r

    r = run(go())
    s = r.sessions.get(1)
    assert s.code == "1043" and s.uid is None
    assert any("UID '123' không hợp lệ" in t for t in r.msg.texts())


def test_expiry_cleans_folder_and_notifies(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.settle()
        folder = r.sessions.get(1).dir
        assert folder.exists()
        r.clock.advance(14 * 60)
        assert await r.ctl.sweep() == []
        assert folder.exists()
        r.clock.advance(2 * 60)
        gone = await r.ctl.sweep()
        return r, folder, gone

    r, folder, gone = run(go())
    assert gone == [1] and not folder.exists()
    assert r.sessions.get(1) is None
    assert r.msg.last_text() == texts.SESSION_EXPIRED


def test_activity_extends_session(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.settle()
        r.clock.advance(10 * 60)
        await r.ctl.handle_command(1, "trangthai", None)
        r.clock.advance(10 * 60)
        return r, await r.ctl.sweep()

    r, gone = run(go())
    assert gone == [] and r.sessions.get(1) is not None


def test_huy_deletes_session_and_pending_background(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "themnen", "columbina")
        folder = r.sessions.get(1).dir
        await r.ctl.handle_command(1, "huy", None)
        return r, folder

    r, folder = run(go())
    assert not folder.exists() and r.sessions.get(1) is None
    assert r.ctl._pending_bg == {}


# ---------------------------------------------------------------- lệnh đơn giản


def test_ma_uid_cm_commands(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "ma", "abc")
        await r.ctl.handle_command(1, "ma", "CN#1076")
        await r.ctl.handle_command(1, "ma", "")
        await r.ctl.handle_command(1, "uid", "12ab")
        await r.ctl.handle_command(1, "cm", "columbina 2 zibai 0 hu tao 6 nobody 3 furina 9")
        await r.settle()
        return r

    r = run(go())
    s = r.sessions.get(1)
    assert s.code == "CN#1076"
    assert s.manual_cm == {"columbina": 2, "zibai": 0, "hutao": 6}
    texts_ = r.msg.texts()
    assert texts.BAD_CODE in texts_ and texts.BAD_UID in texts_
    assert any("Không nhận ra nhân vật 'nobody'" in t and "từ 0 đến 6" in t for t in texts_)


def test_uid_command_fetches_enka_in_background_and_summarises(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=FakeEnka(make_profile(avatars=[("columbina", 2), ("zibai", None)]), delay=0.02))
        await r.ctl.handle_command(1, "uid", "850363095")
        first = list(r.msg.texts())
        await r.settle()
        return r, first

    r, first = run(go())
    assert first == [texts.UID_SET.format(uid="850363095"), texts.ENKA_FETCHING.format(uid="850363095")]
    summary = r.msg.last_text()
    assert "Lữ Khách" in summary and "AR 60" in summary
    assert "Columbina C2" in summary
    assert "Zibai" in summary and "không trả chi tiết" in summary
    assert "/cm" in summary  # nhắc nhân vật không trưng bày cần nhập tay
    assert r.sessions.get(1).enka_profile is not None


def test_enka_error_is_friendly_and_does_not_block_render(tmp_path):
    async def go():
        err = FakeEnkaError("UID không tồn tại trên Enka.")
        r = make_rig(tmp_path, enka=FakeEnka(error=err))
        await r.ctl.handle_command(1, "uid", "850363095")
        await r.settle()
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    assert any("UID không tồn tại trên Enka." in t for t in r.msg.texts())
    assert r.sessions.get(1).enka_error
    assert len(r.renderer.calls) == 1  # vẫn render được (cung mệnh thiếu thì báo)


def test_enka_disabled(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=None)
        await r.ctl.handle_command(1, "uid", "850363095")
        return r

    assert make_rig  # noqa
    r = run(go())
    assert texts.ENKA_OFF in r.msg.texts()


def test_nen_and_unknown_character(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        r.store.seed("columbina")
        await r.ctl.handle_command(1, "nen", "Columbina")
        await r.ctl.handle_command(1, "nen", "zzz")
        await r.ctl.handle_command(1, "nen", "furina")
        return r

    r = run(go())
    assert r.sessions.get(1).bg_override == "furina"
    ts = r.msg.texts()
    assert "Không nhận ra nhân vật 'zzz'" in ts[1]
    assert "kho chưa có nền nào của Furina" in ts[2]


def test_unknown_command_and_text(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "?", None)
        await r.ctl.handle_text(1)
        return r

    assert run(go()).msg.texts() == [texts.UNKNOWN_COMMAND, texts.TEXT_HINT]


def test_trangthai_lists_state(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "trangthai", None)
        await r.ctl.handle_media(1, photo(1, r.msg, w=1500, h=900))
        await r.ctl.handle_command(1, "ma", "1043")
        await r.ctl.handle_command(1, "cm", "columbina 2")
        await r.ctl.handle_command(1, "trangthai", None)
        return r

    r = run(go())
    assert r.msg.texts()[0] == texts.NO_SESSION
    last = r.msg.last_text()
    assert "1 ảnh" in last and "1500x900" in last and "1043" in last and "Columbina C2" in last


# ---------------------------------------------------------------- kho nền


def test_background_upload_via_themnen_document(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "themnen", "columbina")
        await r.ctl.handle_media(1, doc(1, r.msg, w=2560, h=1440))
        # ảnh kế tiếp không còn là nền nữa
        await r.ctl.handle_media(1, photo(2, r.msg))
        await r.settle()
        return r

    r = run(go())
    assert [b.character for b in r.store.bgs] == ["columbina"]
    assert any("Đã lưu nền cho Columbina" in t for t in r.msg.texts())
    assert len(r.sessions.get(1).items) == 1  # chỉ ảnh thứ hai vào phiên
    assert not list(r.config.tmp_dir.glob("*"))  # file tạm đã xoá


def test_background_upload_via_caption(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, doc(1, r.msg, w=3840, h=2160, caption="nen Columbina"))
        await r.ctl.handle_media(1, doc(2, r.msg, w=3840, h=2160, caption="/themnen hu tao"))
        return r

    r = run(go())
    assert [b.character for b in r.store.bgs] == ["columbina", "hutao"]
    assert r.sessions.get(1) is None  # ảnh nền không tạo phiên


def test_background_too_small_is_rejected_with_reason_and_file_hint(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "themnen", "columbina")
        await r.ctl.handle_media(1, photo(1, r.msg, w=1280, h=720))
        return r

    r = run(go())
    assert r.store.bgs == []
    last = r.msg.last_text()
    assert "Nền quá nhỏ (1280x720)" in last and "dạng File" in last
    assert 1 in r.ctl._pending_bg  # còn chờ để gửi lại


def test_background_caption_unknown_character(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, doc(1, r.msg, caption="nen zzz"))
        return r

    r = run(go())
    assert r.store.bgs == [] and "Không nhận ra nhân vật 'zzz'" in r.msg.last_text()


def test_dsnen_xoanen_uutien(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "dsnen", None)
        a, b, c = r.store.seed("columbina"), r.store.seed("columbina"), r.store.seed("zibai")
        await r.ctl.handle_command(1, "dsnen", None)
        await r.ctl.handle_command(1, "xoanen", "nope")
        await r.ctl.handle_command(1, "xoanen", b.id)
        await r.ctl.handle_command(1, "xoanen", "")
        await r.ctl.handle_command(1, "uutien", None)
        return r, (a, b, c)

    r, (a, b, c) = run(go())
    ts = r.msg.texts()
    assert ts[0] == texts.BG_EMPTY
    assert "Columbina (2)" in ts[1] and "Zibai (1)" in ts[1] and "3 ảnh" in ts[1]
    assert "Không thấy nền có id 'nope'" in ts[2]
    assert "Đã xoá nền" in ts[3] and [x.id for x in r.store.bgs] == [a.id, c.id]
    assert ts[4] == texts.NEED_XOANEN_ARGS
    hype = ts[5]
    assert hype.index("Zibai") < hype.index("Columbina") < hype.index("Furina")  # điểm cao xếp trước
    assert "1 nền" in hype


# ---------------------------------------------------------------- /xong


def _prepare(r):
    async def go():
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "ma", "1043")
        await r.ctl.handle_command(1, "uid", "850363095")
        await r.settle()
        r.msg.sent.clear()

    return go()


def test_xong_without_session_or_images(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_command(1, "xong", None)
        await r.ctl.handle_command(1, "ma", "1")  # có phiên nhưng chưa có ảnh
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    assert r.msg.texts() == [texts.NO_SESSION, texts.CODE_SET.format(code="1"), texts.NEED_IMAGES]
    assert r.cropper.calls == []


def test_xong_precedence_manual_over_enka_over_none_and_bg_choice(tmp_path):
    """Enka: columbina C1, zibai (không chi tiết), furina C3. Thẻ: columbina, zibai, hutao, (chưa nhận diện)."""

    async def go():
        r = make_rig(tmp_path)
        r.store.seed("hutao")
        col = r.store.seed("columbina")
        r.store.seed("zibai")
        r.store.seed("nahida")  # nahida không có trong acc → không bao giờ được chọn
        await _prepare(r)
        await r.ctl.handle_command(1, "cm", "columbina 5 hu tao 0")  # tay thắng Enka (C1) ; hutao ngoài Enka
        r.msg.sent.clear()
        await r.ctl.handle_command(1, "xong", None)
        return r, col

    r, col = run(go())
    assert len(r.renderer.calls) == 1
    call = r.renderer.calls[0]
    assert call["badges"] == {"columbina": 5, "zibai": None, "hutao": 0}
    assert call["thread"] != "MainThread"  # render chạy trong thread riêng
    assert call["request"].code == "1043" and call["request"].uid == "850363095"
    bg_id = call["request"].background_id
    assert bg_id in {"bg1", "bg2", "bg3"}  # ngẫu nhiên trong nền của nhân vật có trong acc
    # pick() nhận danh sách = Enka ∪ thẻ
    chars_arg, _ = r.store.pick_calls[-1]
    assert set(chars_arg) == {"columbina", "zibai", "furina", "hutao"}

    kinds = r.msg.kinds()
    assert kinds.index("photo") < kinds.index("doc")
    photo_msg = next(m for m in r.msg.sent if m["kind"] == "photo")
    doc_msg = next(m for m in r.msg.sent if m["kind"] == "doc")
    name = {"bg1": "Hu Tao", "bg2": "Columbina", "bg3": "Zibai"}[bg_id]
    assert photo_msg["caption"] == f"Nền: {name} — chọn ngẫu nhiên trong các nhân vật có trong acc"
    assert doc_msg["filename"] == "acc-1043-20261021-1405.jpg"
    assert "850363095" not in doc_msg["filename"]
    assert doc_msg["data"] == photo_msg["data"]
    assert [lbl for lbl, _ in doc_msg["buttons"][0]] == ["Đổi nền", "Làm lại"]
    notes = r.msg.last_text()
    assert "Zibai" in notes and "/cm" in notes  # zibai không có cung mệnh -> nhắc nhập tay
    assert "1 từ Enka, 2 nhập tay" in notes or "từ Enka" in notes
    assert (r.config.out_dir / "acc-1043-20261021-1405.jpg").read_bytes() == doc_msg["data"]
    assert r.store.used == [bg_id]
    assert r.sessions.get(1).rendered


def test_xong_enka_badges_when_no_manual(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await _prepare(r)
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    assert r.renderer.calls[0]["badges"] == {"columbina": 1, "zibai": None, "hutao": None}
    assert r.renderer.calls[0]["request"].background_id is None  # kho trống -> nền mặc định
    assert "Nền mặc định" in next(m for m in r.msg.sent if m["kind"] == "photo")["caption"]


def test_xong_keeps_existing_card_badge_when_no_source(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=None)
        r.cropper.preset_badges = {0: 4}
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    assert r.renderer.calls[0]["badges"]["columbina"] == 4


def test_nen_override_beats_auto_pick_and_missing_override_falls_back(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        r.store.seed("zibai")
        r.store.seed("furina")
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "nen", "furina")
        await r.ctl.handle_command(1, "xong", None)
        first = r.renderer.calls[0]["request"].background_id
        await r.ctl.handle_command(1, "huy", None)
        await r.ctl.handle_media(1, photo(2, r.msg))
        await r.ctl.handle_command(1, "nen", "nahida")  # chưa có nền nahida
        await r.ctl.handle_command(1, "xong", None)
        return r, first

    r, first = run(go())
    assert first == "bg2"  # furina dù zibai hot hơn
    second = r.renderer.calls[1]
    assert second["request"].background_id == "bg1"  # fallback chọn tự động
    assert any("Chưa có nền nào của Nahida" in t for t in r.msg.texts())


def test_doi_nen_random_among_account_backgrounds_never_repeats_current(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=None, seed=5)
        r.cropper.characters = ["columbina", "zibai"]
        r.store.seed("columbina")  # bg1
        r.store.seed("zibai")  # bg2
        r.store.seed("nahida")  # bg3 không có trong acc
        r.store.seed("zibai")  # bg4
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "xong", None)
        ids = [r.renderer.calls[0]["request"].background_id]
        for _ in range(12):
            toast = await r.ctl.handle_callback(1, "bg:next")
            assert toast is None
            ids.append(r.renderer.calls[-1]["request"].background_id)
        return r, ids

    r, ids = run(go())
    assert set(ids) <= {"bg1", "bg2", "bg4"}  # không bao giờ nền ngoài acc hay nền mặc định
    assert all(a != b for a, b in zip(ids, ids[1:]))  # Đổi nền không lặp nền đang dùng
    assert len(set(ids)) == 3
    assert len(r.cropper.calls) == 1  # Đổi nền không cắt lại
    captions = [m["caption"] for m in r.msg.sent if m["kind"] == "photo"]
    assert all(c.endswith("chọn ngẫu nhiên trong các nhân vật có trong acc") for c in captions)
    assert r.store.used == ids


def test_doi_nen_with_single_matching_background_says_only_one(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=None)
        r.cropper.characters = ["columbina"]
        r.store.seed("columbina")  # bg1: nền duy nhất của nhân vật trong acc
        r.store.seed("nahida")  # bg2: ngoài acc
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "xong", None)
        toast = await r.ctl.handle_callback(1, "bg:next")
        return r, toast

    r, toast = run(go())
    assert toast == texts.BG_CYCLE_ONLY_ONE
    assert [c["request"].background_id for c in r.renderer.calls] == ["bg1"]


def test_doi_nen_with_only_default_and_expired_session(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        assert await r.ctl.handle_callback(1, "bg:next") == texts.CALLBACK_EXPIRED  # chưa render
        await r.ctl.handle_command(1, "xong", None)
        t1 = await r.ctl.handle_callback(1, "bg:next")
        t2 = await r.ctl.handle_callback(1, "weird")
        r.clock.advance(16 * 60)
        await r.ctl.sweep()
        t3 = await r.ctl.handle_callback(1, "redo")
        return t1, t2, t3

    t1, t2, t3 = run(go())
    assert t1 == texts.BG_CYCLE_ONLY_ONE
    assert t2 == texts.CALLBACK_UNKNOWN
    assert t3 == texts.CALLBACK_EXPIRED


def test_lam_lai_rebuilds_with_new_cm(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "xong", None)
        await r.ctl.handle_command(1, "cm", "columbina 6")
        await r.ctl.handle_callback(1, "redo")
        return r

    r = run(go())
    assert len(r.cropper.calls) == 2 and len(r.renderer.calls) == 2
    assert r.renderer.calls[1]["badges"]["columbina"] == 6


def test_new_images_after_render_start_a_fresh_session(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "ma", "1043")
        await r.ctl.handle_command(1, "xong", None)
        old = r.sessions.get(1)
        await r.ctl.handle_media(1, photo(2, r.msg))
        await r.settle()
        return r, old

    r, old = run(go())
    s = r.sessions.get(1)
    assert s is not old and s.code is None and len(s.items) == 1
    assert [p.name for p in s.dir.iterdir()] == ["01-2.jpg"]  # ảnh và thư mục work của phiên cũ đã bị xoá


def test_xong_waits_for_pending_enka(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=FakeEnka(make_profile(avatars=[("columbina", 4)]), delay=0.1))
        await r.ctl.handle_media(1, photo(1, r.msg))
        await r.ctl.handle_command(1, "uid", "850363095")
        await r.ctl.handle_command(1, "xong", None)  # Enka chưa xong lúc này
        return r

    r = run(go())
    assert r.renderer.calls[0]["badges"]["columbina"] == 4


def test_cropper_and_renderer_failures_are_friendly(tmp_path):
    class Boom(Exception):
        message_vi = "Không thấy khối nhân vật trong ảnh 3."

    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        r.cropper.fail = Boom()
        await r.ctl.handle_command(1, "xong", None)
        r.cropper.fail = None
        r.renderer.fail = RuntimeError("trace nội bộ không được lộ")
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    ts = "\n".join(r.msg.texts())
    assert "Không thấy khối nhân vật trong ảnh 3." in ts
    assert "Ghép ảnh thất bại" in ts and "trace nội bộ" not in ts
    assert not r.sessions.get(1).busy  # lỗi xong thì mở khoá


def test_fixture_cropper_needs_no_images(tmp_path):
    async def go():
        r = make_rig(tmp_path, enka=None)
        r.cropper.needs_images = False
        await r.ctl.handle_command(1, "ma", "9")
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    assert len(r.renderer.calls) == 1


def test_busy_session_rejects_second_xong(tmp_path):
    async def go():
        r = make_rig(tmp_path)
        await r.ctl.handle_media(1, photo(1, r.msg))
        r.sessions.get(1).busy = True
        await r.ctl.handle_command(1, "xong", None)
        return r

    r = run(go())
    assert r.msg.last_text() == texts.BUSY and r.cropper.calls == []


def test_cleanup_outputs_removes_old_files(tmp_path):
    import os
    import time

    r = make_rig(tmp_path)
    r.config.out_dir.mkdir(parents=True)
    old, new = r.config.out_dir / "old.jpg", r.config.out_dir / "new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - 8 * 86400
    os.utime(old, (past, past))
    assert r.ctl.cleanup_outputs() == 1
    assert not old.exists() and new.exists()


def test_cropper_unused_import_guard():
    assert FakeCropper().needs_images
