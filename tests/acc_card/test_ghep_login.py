"""Test nhánh /ghep <tk>-<mk>: chỉ test phần thuần (phân tích lệnh, cookie, thẻ
game), không chạy Playwright, không chạy bot Telegram, không đụng bộ edit cũ
(render/compose/layout không được test ở đây vì chúng không hề bị sửa)."""

from __future__ import annotations

import pytest

from gacard.bot.services import looks_like_credentials, parse_ghep_login_args
from gacard.hoyolab_login import LoginError, build_cookie_headers, parse_game_card


# ------------------------------------------------------------------ phân tích lệnh
def test_login_args_email_dash_password():
    got = parse_ghep_login_args("ten@gmail.com-MatKhau123 ma 1116 nen columbina")
    assert got is not None
    assert (got.username, got.password, got.code, got.nen) == ("ten@gmail.com", "MatKhau123", "1116", "columbina")


def test_login_args_password_may_contain_dash():
    got = parse_ghep_login_args("ten@gmail.com-Pass-123-xyz")
    assert got is not None and got.password == "Pass-123-xyz"


def test_login_args_colon_separator_for_dash_username():
    got = parse_ghep_login_args("my-user:Pass123")
    assert got is not None and (got.username, got.password) == ("my-user", "Pass123")


def test_login_args_uid_is_not_login():
    assert parse_ghep_login_args("887630784 ma 1116") is None


def test_login_args_no_separator_is_not_login():
    assert parse_ghep_login_args("chituan") is None
    assert parse_ghep_login_args("ten@gmail.com") is None


def test_login_args_reuses_old_caption_options():
    got = parse_ghep_login_args("tk-Mk ma 999")
    assert got is not None and got.code == "999"


def test_old_credential_filter_still_blocks_plain_messages():
    # Bộ lọc cũ không đổi: tin thường giống tk/mk vẫn bị chặn (chỉ lệnh /ghep
    # login mới là ngoại lệ, và ngoại lệ đó nằm ở handlers, không nằm ở đây).
    assert looks_like_credentials("ten@gmail.com-MatKhau123")
    assert looks_like_credentials("user:pass123")


# ------------------------------------------------------------------ cookie từ trình duyệt
def test_build_cookie_headers_keeps_only_needed_names():
    cookies = [
        {"name": "ltuid_v2", "value": "123"},
        {"name": "ltoken_v2", "value": "v2_abc"},
        {"name": "cookie_token_v2", "value": "v2_tok"},
        {"name": "account_id_v2", "value": "123"},
        {"name": "DEVICEFP", "value": "zzz"},
    ]
    got = build_cookie_headers(cookies)
    assert got.cookie == "ltuid_v2=123; ltoken_v2=v2_abc"
    assert got.cookie_token == "account_id_v2=123; cookie_token_v2=v2_tok"
    assert got.ltuid == "123"
    assert "DEVICEFP" not in got.cookie and "DEVICEFP" not in (got.cookie_token or "")


def test_build_cookie_headers_without_token_is_not_an_error():
    got = build_cookie_headers([{"name": "ltuid_v2", "value": "1"}, {"name": "ltoken_v2", "value": "x"}])
    assert got.cookie_token is None  # ô banner sẽ bị bỏ qua, các phần khác vẫn vẽ


def test_build_cookie_headers_missing_main_raises():
    with pytest.raises(LoginError):
        build_cookie_headers([{"name": "ltuid_v2", "value": "1"}])


# ------------------------------------------------------------------ thẻ game đã liên kết
def test_parse_game_card_only_genshin():
    data = {"list": [
        {"game_id": 2, "game_role_id": "887630784", "has_role": True},
        {"game_id": 6, "game_role_id": "123456789", "has_role": True},  # HSR: bỏ qua
        {"game_id": 2, "game_role_id": "887630784", "has_role": True},  # trùng: khử trùng
        {"game_id": 2, "game_role_id": "999", "has_role": True},  # UID sai định dạng: bỏ
        {"game_id": 2, "game_role_id": "850363095", "has_role": False},  # chưa liên kết: bỏ
    ]}
    assert parse_game_card(data) == ["887630784"]


def test_parse_game_card_empty():
    assert parse_game_card({}) == []
    assert parse_game_card({"list": "khong-phai-list"}) == []
