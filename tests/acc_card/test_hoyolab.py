"""Test gacard.hoyolab (offline hoàn toàn: transport giả, urlopen bị chặn)."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from gacard import enka, hoyolab
from gacard.hoyolab import (BATTLE_CHRONICLE_URL, DS_SALT_OVERSEAS, EnkaExtras, HoyolabClient, HoyolabError,
                            HoyoChar, HoyoWeapon, UrlIconCache, allowed_icon_url, build_sheet_from_data, classify_retcode,
                            generate_ds, icon_stem, parse_character_list, parse_cookie, recognize_server,
                            redact, select_characters, select_weapons)
from gacard.layout import TEXT_STROKE, compute_layout
from gacard.models import RenderOptions
from gacard.render import build_spec, load_pieces, render_image
from gacard.uidmode import (PROFILE_COMPACT_PAD, PROFILE_COMPACT_ROW, PROFILE_SIZE, IconCache,
                            draw_profile_box_compact)

from .hoyolab_fixture import envelope, make_character_list, make_index

UID = "800000001"
COOKIE = "ltuid_v2=123456789; ltoken_v2=v2_SECRETTOKENVALUE_abcdef; mi18nLang=vi-vn"
REAL_FETCH_EXTRAS = hoyolab.fetch_enka_extras


@pytest.fixture(autouse=True)
def no_enka(monkeypatch):
    """Không gọi Enka (aiohttp) trong test; test riêng của fetch_enka_extras dùng REAL_FETCH_EXTRAS."""
    monkeypatch.setattr(hoyolab, "fetch_enka_extras", lambda uid, **k: None)


@pytest.fixture(autouse=True)
def net_attempts(monkeypatch):
    """Chặn mạng: urlopen luôn lỗi như mất mạng, ghi lại URL bị thử (chỉ được là URL icon)."""
    attempts: list[str] = []

    def offline(req, *a, **k):
        attempts.append(getattr(req, "full_url", str(req)))
        raise urllib.error.URLError("offline (test)")
    monkeypatch.setattr(urllib.request, "urlopen", offline)
    return attempts


class FakeTransport:
    """Trả lần lượt các response theo endpoint; ghi lại mọi request."""

    def __init__(self, responses: dict[str, list]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):
        path = urllib.parse.urlsplit(url).path.rsplit("/api/", 1)[-1]
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": json.loads(body) if body else None, "path": path})
        queue = self.responses[path]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, doc = item if isinstance(item, tuple) else (200, item)
        return status, json.dumps(doc).encode()


def ok_transport(n: int = 30) -> FakeTransport:
    return FakeTransport({"index": [envelope(make_index(n))],
                          "character/list": [envelope(make_character_list(n))]})


def client(tmp_path, transport, **kw) -> HoyolabClient:
    return HoyolabClient(COOKIE, cache_dir=tmp_path / "cache", transport=transport, **kw)


# ------------------------------------------------------------------ DS, server, cookie
def test_ds_format_and_hash():
    t, r = 1700000000, "AbCdEf"
    expect = hashlib.md5(f"salt={DS_SALT_OVERSEAS}&t={t}&r={r}".encode()).hexdigest()
    assert generate_ds(t=t, r=r) == f"{t},{r},{expect}"
    assert re.fullmatch(r"\d{10},[A-Za-z]{6},[0-9a-f]{32}", generate_ds())


@pytest.mark.parametrize("uid,server", [("600000001", "os_usa"), ("700000001", "os_euro"),
                                        ("800000001", "os_asia"), ("1800000001", "os_asia"),
                                        ("900000001", "os_cht")])
def test_server_from_uid(uid, server):
    assert recognize_server(uid) == server


@pytest.mark.parametrize("uid,kind", [("100000001", "unsupported_server"), ("500000001", "unsupported_server"),
                                      ("6100000001", "invalid_uid"), ("abc", "invalid_uid"),
                                      ("12345", "invalid_uid")])
def test_server_from_uid_rejects(uid, kind):
    with pytest.raises(HoyolabError) as ei:
        recognize_server(uid)
    assert ei.value.kind == kind


def test_parse_cookie():
    assert parse_cookie(COOKIE)["ltuid_v2"] == "123456789"
    for bad, kind in [("", "no_cookie"), (None, "no_cookie"), ("ltuid_v2=1", "bad_cookie"),
                      ("ltoken_v2=x", "bad_cookie"), ("ltuid_v2=1;\nltoken_v2=2", "bad_cookie")]:
        with pytest.raises(HoyolabError) as ei:
            parse_cookie(bad)
        assert ei.value.kind == kind


def test_redact_removes_all_values():
    text = f"boom {COOKIE} và v2_SECRETTOKENVALUE_abcdef, 123456789"
    out = redact(text, COOKIE)
    assert "SECRETTOKEN" not in out and "123456789" not in out and "***" in out


# ------------------------------------------------------------------ request
def test_request_headers_and_bodies(tmp_path):
    tr = ok_transport()
    c = client(tmp_path, tr)
    c.index(UID)
    c.character_list(UID)
    idx, lst = tr.calls
    assert idx["method"] == "GET"
    assert idx["url"].startswith("https://sg-act-public-api.hoyolab.com/event/game_record/genshin/api/index?")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(idx["url"]).query)
    assert q == {"avatar_list_type": ["1"], "server": ["os_asia"], "role_id": [UID]}
    h = idx["headers"]
    assert h["Cookie"] == COOKIE
    assert re.fullmatch(r"\d+,[A-Za-z]{6},[0-9a-f]{32}", h["DS"])
    assert h["x-rpc-app_version"] == "1.5.0" and h["x-rpc-client_type"] == "5"
    assert h["x-rpc-language"] == "vi-vn"
    assert h["Origin"] == "https://act.hoyolab.com" and h["Referer"].startswith("https://act.hoyolab.com")
    assert lst["method"] == "POST" and lst["path"] == "character/list"
    assert lst["body"] == {"role_id": UID, "server": "os_asia"}
    assert "COOKIE" not in repr(c) and "SECRET" not in repr(c)


@pytest.mark.parametrize("retcode,message,kind", [
    (-100, "Please login", "cookie_invalid"), (10001, "Cookie invalid", "cookie_invalid"),
    (10102, "Data is not public", "private"), (10101, "too many", "rate_limit"),
    (10103, "not bound", "not_linked"),
    (1034, "", "verification"), (10035, "", "verification"), (5003, "", "verification"),
    (-1, "geetest required", "verification"), (12345, "lạ", "api")])
def test_retcode_mapping(tmp_path, retcode, message, kind):
    assert classify_retcode(retcode, message) == kind
    tr = FakeTransport({"index": [envelope(None, retcode, message)]})
    with pytest.raises(HoyolabError) as ei:
        client(tmp_path, tr).index(UID)
    assert ei.value.kind == kind and ei.value.retcode == retcode
    assert len(tr.calls) == 1                     # không retry
    if kind == "private":
        assert "công khai" in ei.value.message_vi
    if kind == "verification":
        assert "trình duyệt" in ei.value.message_vi and BATTLE_CHRONICLE_URL in ei.value.message_vi


def test_http_429_is_rate_limit(tmp_path):
    tr = FakeTransport({"index": [(429, {"retcode": -1, "message": "x"})]})
    with pytest.raises(HoyolabError) as ei:
        client(tmp_path, tr).index(UID)
    assert ei.value.kind == "rate_limit"


def test_error_text_never_contains_cookie(tmp_path):
    tr = FakeTransport({"index": [RuntimeError(f"socket died with header {COOKIE}")]})
    with pytest.raises(HoyolabError) as ei:
        client(tmp_path, tr).index(UID)
    assert "SECRETTOKEN" not in str(ei.value) and "123456789" not in str(ei.value)
    tr = FakeTransport({"index": [envelope(None, 777, "echo v2_SECRETTOKENVALUE_abcdef")]})
    with pytest.raises(HoyolabError) as ei:
        client(tmp_path, tr).index(UID)
    assert ei.value.kind == "api" and "SECRETTOKEN" not in ei.value.message_vi


def test_cache_10_minutes(tmp_path, monkeypatch):
    tr = ok_transport()
    c = client(tmp_path, tr)
    c.index(UID)
    c.index(UID)
    assert len(tr.calls) == 1
    files = list((tmp_path / "cache").glob("*.json"))
    assert files and all("SECRET" not in f.read_text(encoding="utf-8") for f in files)
    now = hoyolab.time.time()
    monkeypatch.setattr(hoyolab.time, "time", lambda: now + 601)
    c.index(UID)
    assert len(tr.calls) == 2


# ------------------------------------------------------------------ xác minh (captcha do người dùng tự giải)
VERIFY = envelope(None, 1034, "need verification")


def _patch_browser(monkeypatch, answers=("",)):
    opened, asked = [], []
    monkeypatch.setattr(hoyolab.webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    it = iter(answers)

    def fake_input(*a):
        asked.append(1)
        v = next(it)
        if isinstance(v, BaseException):
            raise v
        return v
    monkeypatch.setattr("builtins.input", fake_input)
    return opened, asked


def test_verification_opens_browser_and_retries_once(tmp_path, monkeypatch, capsys):
    opened, asked = _patch_browser(monkeypatch)
    tr = FakeTransport({"index": [VERIFY, envelope(make_index())]})
    data = client(tmp_path, tr, on_verification=hoyolab.interactive_verification).index(UID)
    assert data["role"]["level"] == 60
    assert len(tr.calls) == 2 and opened == [BATTLE_CHRONICLE_URL] and len(asked) == 1
    assert "tự kéo captcha" in capsys.readouterr().out


def test_verification_twice_stops(tmp_path, monkeypatch):
    opened, asked = _patch_browser(monkeypatch)
    tr = FakeTransport({"index": [VERIFY, VERIFY, envelope(make_index())]})
    with pytest.raises(HoyolabError) as ei:
        client(tmp_path, tr, on_verification=hoyolab.interactive_verification).index(UID)
    assert ei.value.kind == "verification"
    assert len(tr.calls) == 2 and len(opened) == 1 and len(asked) == 1


def test_verification_eof_no_retry(tmp_path, monkeypatch):
    opened, _ = _patch_browser(monkeypatch, answers=(EOFError(),))
    tr = FakeTransport({"index": [VERIFY, envelope(make_index())]})
    with pytest.raises(HoyolabError):
        client(tmp_path, tr, on_verification=hoyolab.interactive_verification).index(UID)
    assert len(tr.calls) == 1 and len(opened) == 1


def test_verification_response_payload_is_not_used(tmp_path, monkeypatch):
    """Dữ liệu captcha (gt/challenge) trong response không được đọc, lưu hay in ra."""
    _patch_browser(monkeypatch)
    payload = {"gt": "GTVALUE123", "challenge": "CHALLENGE456"}
    tr = FakeTransport({"index": [envelope(payload, 1034, "geetest"), envelope(payload, 1034, "geetest")]})
    with pytest.raises(HoyolabError) as ei:
        client(tmp_path, tr, on_verification=lambda: True).index(UID)
    assert "GTVALUE" not in ei.value.message_vi and "CHALLENGE" not in ei.value.message_vi
    assert not list((tmp_path / "cache").glob("*.json"))


# ------------------------------------------------------------------ icon URL, dữ liệu
@pytest.mark.parametrize("url,ok", [
    ("https://upload-os-bbs.mihoyo.com/game_record/genshin/character_icon/UI_AvatarIcon_Ayaka.png", True),
    ("https://act-upload.hoyoverse.com/x/y.png", True), ("https://act-webstatic.hoyolab.com/a.png", True),
    ("https://enka.network/ui/UI_AvatarIcon_Ayaka.png", True),
    ("http://upload-os-bbs.mihoyo.com/a.png", False), ("https://evil.com/hoyolab.com.png", False),
    ("https://hoyolab.com.evil.com/a.png", False), ("https://user:pw@act-upload.hoyoverse.com/a.png", False),
    ("https://mihoyo.com.evil/a.png", False), ("file:///etc/passwd", False), (None, False)])
def test_allowed_icon_url(url, ok):
    assert allowed_icon_url(url) is ok


def test_icon_stem():
    assert icon_stem("https://x.hoyolab.com/a/UI_AvatarIcon_Ayaka.png") == "UI_AvatarIcon_Ayaka"
    assert icon_stem("https://x.hoyolab.com/a/12345abc.png") is None


def test_parse_sort_and_aloy():
    chars = parse_character_list(make_character_list(30, with_aloy=True))
    assert len(chars) == 30
    keys = [(-(c.rarity or 0), -(c.level or 0), -(c.constellation or 0), c.avatar_id) for c in chars]
    assert keys == sorted(keys)
    aloy = next(c for c in chars if c.avatar_id == 10000062)
    assert aloy.rarity == 5 and aloy.slug == "aloy"
    assert all(c.slug for c in chars)


def _build(tmp_path, n, extras=None, options=None):
    lst = make_character_list(n)
    icons = UrlIconCache(tmp_path / "hicons", offline=True)
    enka_icons = IconCache(tmp_path / "eicons", offline=True)
    sheet, report = build_sheet_from_data(make_index(n), lst, tmp_path / "work", icons, enka_icons, extras,
                                          options)
    return lst, sheet, report


def _rendered_weapon_cols(sheet, work: Path, options: RenderOptions) -> int:
    layout = compute_layout(build_spec(sheet, load_pieces(sheet, work), options, None), options.family)
    return layout.weapon_plan.cols if layout.weapon_plan else 0


def _full(n: int, cols: int) -> bool:
    return n <= cols or n % cols == 0


def test_sheet_characters_badges_weapons(tmp_path):
    lst, sheet, report = _build(tmp_path, 30)
    chars = parse_character_list(lst)
    # hết 5★ + 4★ mạnh, hàng cuối đủ; vũ khí chỉ 4–5★, đủ hàng theo số cột bố cục chọn
    assert sum(c.rarity == 5 for c in sheet.characters) == sum(c.rarity == 5 for c in chars)
    cols = _rendered_weapon_cols(sheet, tmp_path / "work", RenderOptions())
    assert _full(len(sheet.characters), 5) and len(sheet.weapons) % cols == 0
    assert {w.rarity for w in sheet.weapons} <= {4, 5}
    assert [c.badge for c in sheet.characters] == [c.constellation for c in select_characters(chars)]
    assert all((tmp_path / "work" / c.image).is_file() for c in sheet.characters + sheet.weapons)
    assert (tmp_path / "work" / "profile.png").is_file() and (tmp_path / "work" / "sheet.json").is_file()
    text = report.text_vi()
    assert "AR 60" in text and f"Nhân vật ({len(sheet.characters)})" in text
    assert f"Vũ khí: vẽ {len(sheet.weapons)} (" in text and "/ 30 đang trang bị" in text
    assert "4 vũ khí 1–3★" in text
    assert "vũ khí trong túi" in text and "Mối Duyên" in text and "Sinh Nhật" in text
    assert "Không có Cấp Thế Giới" in text
    assert len(report.missing_icons) > 0          # offline: ô thay thế, không lỗi


def _w(i, rarity, refinement=1, level=90):
    return HoyoWeapon(i, f"w{i}", None, rarity, level, refinement, None, None, None)


def test_strong_definitions():
    assert hoyolab.strong_weapon(_w(1, 4, refinement=3, level=20))
    assert hoyolab.strong_weapon(_w(1, 4, refinement=1, level=80))
    assert not hoyolab.strong_weapon(_w(1, 4, refinement=2, level=70))
    assert hoyolab.strong_character(_c(1, 4, cons=0, level=70))
    assert hoyolab.strong_character(_c(1, 4, cons=4, level=1))
    assert not hoyolab.strong_character(_c(1, 4, cons=3, level=60))


def test_select_weapons_drops_low_rarity_and_fills_with_best_weak():
    lows = [_w(900 + i, r) for i, r in enumerate([1, 1, 2, 3, 3, None])]
    fives = [_w(i, 5) for i in range(10)]
    weak = [_w(100 + i, 4, refinement=1 + i % 2, level=(70, 60, 40, 20)[i % 4]) for i in range(12)]
    got = select_weapons(lows + weak + fives)                             # 10 → lấp 6 thẻ 4★ → 16
    assert len(got) == 16 and all(w.rarity in (4, 5) for w in got)
    rank = sorted(weak, key=lambda w: (-w.refinement, -w.level, w.weapon_id))
    assert {w.weapon_id for w in got if w.rarity == 4} == {w.weapon_id for w in rank[:6]}
    assert [w.rarity for w in got] == [5] * 10 + [4] * 6                 # 5★ trước
    assert len(select_weapons(lows + fives[:6] + weak)) == 6              # 6 ≤ 8: một hàng đủ


@pytest.mark.parametrize("n5,strong,weak,total", [
    (6, 2, 0, 8),       # 5★ + 4★ mạnh vừa đủ hàng
    (6, 3, 10, 16),     # hàng cuối thiếu → lấp bằng 4★ yếu tốt nhất
    (3, 2, 0, 5),       # một hàng < 8 thẻ: số cột = số thẻ, không ô trống
    (10, 3, 2, 8),      # 4★ không đủ lấp → bỏ thẻ yếu nhất tới hàng đủ
    (20, 21, 0, 40),    # mặc định 8 cột: 41 → bớt về 40
    (20, 20, 8, 40),    # không giới hạn số lượng
    (30, 30, 4, 64),
    (0, 0, 0, 0), (0, 0, 11, 0),
])
def test_select_weapons_counts(n5, strong, weak, total):
    got = select_weapons([_w(i, 5) for i in range(n5)] + [_w(100 + i, 4, level=90) for i in range(strong)]
                         + [_w(200 + i, 4, level=40) for i in range(weak)])
    assert len(got) == total


def _w_pool(n5, strong, weak):
    return ([_w(i, 5) for i in range(n5)] + [_w(100 + i, 4, level=90) for i in range(strong)]
            + [_w(200 + i, 4, level=40) for i in range(weak)])


@pytest.mark.parametrize("n5,strong,weak,total", [
    (20, 20, 8, 48),    # 40 → lấp tới 48 (12 cột, phủ bề rộng)
    (20, 20, 0, 36),    # không đủ 4★ lấp → bớt về 36
    (6, 2, 0, 8),       # 8 ≤ 12: một hàng
])
def test_select_weapons_uses_layout_columns(n5, strong, weak, total):
    fit = lambda t: (min(t, 12), True)  # noqa: E731 — bố cục giả: 12 cột
    got = select_weapons(_w_pool(n5, strong, weak), fit)
    assert len(got) == total and (total <= 12 or total % 12 == 0)


def test_select_weapons_prefers_total_that_fills_width():
    # 30 thẻ: 10 cột đủ hàng nhưng chưa phủ bề rộng; 33 = 11 cột phủ hết → lấp thêm 3 thẻ 4★
    fit = lambda t: (11, True) if t >= 33 else (10, False)  # noqa: E731
    assert len(select_weapons(_w_pool(20, 10, 5), fit)) == 33
    assert len(select_weapons(_w_pool(20, 10, 0), fit)) == 30        # không đủ để phủ → giữ 30 (đủ hàng)


def test_select_weapons_drops_one_row_for_full_size_grid():
    # acc 887630784: 30 thẻ (6×5★ + 24×4★ mạnh) → 10×3 thẻ bị thu nhỏ; 27 = 9×3 phủ bề rộng, thẻ đủ cỡ
    # → bỏ 3 thẻ 4★ yếu nhất (không lấp được vì không tổng lớn hơn nào "đẹp")
    fit = lambda t: (9, True) if t == 27 else (min(t, 10), False)  # noqa: E731
    pool = ([_w(i, 5) for i in range(6)] + [_w(100 + i, 4, refinement=1 + i % 5, level=90) for i in range(24)]
            + [_w(200 + i, 4, level=40) for i in range(11)])
    got = select_weapons(pool, fit)
    assert len(got) == 27 and sum(w.rarity == 5 for w in got) == 6
    kept = {w.weapon_id for w in got}
    dropped = [w for w in pool[6:30] if w.weapon_id not in kept]
    assert len(dropped) == 3 and all(w.refinement == 1 for w in dropped)   # yếu nhất: tinh luyện thấp nhất
    # bớt tối đa một hàng: tổng "đẹp" xa hơn một hàng thì giữ đủ hàng như cũ
    assert len(select_weapons(pool, lambda t: (9, True) if t == 18 else (min(t, 10), False))) == 30


def _region(rid, name, permille, parent=0, icon=True):
    url = f"https://act-webstatic.hoyoverse.com/game_record/genshin/city_icon/UI_ChapterIcon_{rid}.png"
    return {"id": rid, "name": name, "exploration_percentage": permille, "parent_id": parent,
            "icon": url if icon else "", "background_image": url.replace("Icon", "Background") if icon else ""}


EXPLORATIONS = [
    _region(1, "Mondstadt", 860), _region(8, "Sumeru", 390), _region(5, "Enkanomiya", 369),
    _region(16, "Núi Thánh Viễn Cổ", 239, icon=False), _region(20, "Snezhnaya", 0),
    _region(21, "Đúng 40", 400), _region(22, "Gần 40", 399),
    _region(10, "Trầm Ngọc Cốc", 0), _region(11, "Lai Hâm Sơn", 906, parent=10),
    _region(12, "Trầm Ngọc Cốc - Nam Lăng", 955, parent=10),
    _region(6, "Vực Đá Sâu", 769), _region(7, "Vực Đá Sâu - Khu Mỏ Ngầm", 100, parent=6),
]


def test_explorations_below_40_percent_top_level_only():
    regions = hoyolab.parse_explorations({"world_explorations": EXPLORATIONS})
    by = {r.name: r for r in regions}
    assert "Vực Đá Sâu - Khu Mỏ Ngầm" not in by and "Lai Hâm Sơn" not in by     # vùng con bị bỏ
    assert by["Trầm Ngọc Cốc"].permille == round((906 + 955) / 2)               # vùng cha 0 → TB vùng con
    picked = hoyolab.select_explorations(regions)
    assert [r.name for r in picked] == ["Snezhnaya", "Núi Thánh Viễn Cổ", "Enkanomiya", "Sumeru", "Gần 40"]
    assert by["Núi Thánh Viễn Cổ"].icon_url is None
    assert hoyolab.parse_explorations({}) == [] and hoyolab.select_explorations([]) == []


@pytest.mark.parametrize("permille,text", [(670, "67%"), (355, "35.5%"), (0, "0%"), (1000, "100%"), (390, "39%")])
def test_format_percent(permille, text):
    assert hoyolab.format_percent(permille) == text


def test_exploration_card_drawn_without_icons():
    img = hoyolab.draw_exploration_card("Núi Thánh Viễn Cổ", 239)
    assert img.size == hoyolab.EXPLORATION_SIZE and img.mode == "RGBA"
    assert 0 < img.getchannel("A").getextrema()[1] < 255                 # hơi trong


def test_sheet_exploration_cards_fit_layout(tmp_path):
    options = RenderOptions(code="1116", uid=UID, watermark="@sora kiwi")
    lst = make_character_list(45)
    index = make_index(45) | {"world_explorations": EXPLORATIONS}
    sheet, report = build_sheet_from_data(index, lst, tmp_path / "work", UrlIconCache(tmp_path / "h", offline=True),
                                          IconCache(tmp_path / "e", offline=True), None, options)
    work = tmp_path / "work"
    layout = compute_layout(build_spec(sheet, load_pieces(sheet, work), options, None), options.family)
    assert 1 <= len(sheet.explorations) <= 5
    assert len(layout.of("exploration")) == len(sheet.explorations)      # sheet = đúng số thẻ vẽ được
    assert all((work / p.image).is_file() for p in sheet.explorations)
    text = report.text_vi()
    assert "Khám phá < 40%: Snezhnaya 0%" in text
    assert len(report.explorations) + len(report.explorations_dropped) == 5
    assert len(sheet.explorations) == 5 and not report.explorations_dropped    # mọi vùng < 40 % đều có thẻ
    assert layout.problems(build_spec(sheet, load_pieces(sheet, work), options, None)) == []


def test_sheet_without_low_regions_has_no_exploration_cards(tmp_path):
    lst = make_character_list(30)
    index = make_index(30) | {"world_explorations": [_region(1, "Mondstadt", 860)]}
    sheet, report = build_sheet_from_data(index, lst, tmp_path / "work", UrlIconCache(tmp_path / "h", offline=True),
                                          IconCache(tmp_path / "e", offline=True))
    assert sheet.explorations == [] and "Không vùng nào khám phá < 40%" in report.text_vi()


REAL_INDEX = Path(__file__).resolve().parents[2] / "data" / "acc-card" / "hoyolab-cache" / "887630784-index-vi-vn.json"


@pytest.mark.skipif(not REAL_INDEX.is_file(), reason="không có cache HoYoLAB thật (git-ignored)")
def test_real_cache_887630784_low_regions():
    data = json.loads(REAL_INDEX.read_text(encoding="utf-8"))["data"]
    picked = hoyolab.select_explorations(hoyolab.parse_explorations(data))
    assert all(r.permille < 400 for r in picked)
    assert {"Snezhnaya", "Sumeru", "Enkanomiya"} <= {r.name for r in picked}
    assert "Trầm Ngọc Cốc" not in {r.name for r in picked}               # vùng cha 0 % nhưng vùng con ~90 %


@pytest.mark.parametrize("n_chars,options", [
    (30, RenderOptions(code="1116", uid=UID, watermark="@sora kiwi")),
    (45, RenderOptions(code="1116", uid=UID, watermark="@sora kiwi")),
    (45, RenderOptions(code="1116", uid=UID, family="wide")),
])
def test_selection_matches_layout_columns(tmp_path, n_chars, options):
    _, sheet, _ = _build(tmp_path, n_chars, options=options)
    cols = _rendered_weapon_cols(sheet, tmp_path / "work", options)
    assert sheet.weapons and cols > 0
    assert len(sheet.weapons) % cols == 0                            # không ô trống ở hàng cuối


def _c(i, rarity, cons=0, level=90):
    return HoyoChar(i, None, f"c{i}", rarity, "pyro", level, cons, None, None, None, None)


@pytest.mark.parametrize("n5,fours,total", [
    (10, 15, 25),      # dưới 25 → thêm 4★ cho đủ 5 hàng
    (23, 9, 25),       # dư 3 → thêm đúng 2 thẻ 4★, cũng đủ 5 hàng
    (7, 9, 16),        # dưới 25, chỉ có 9 thẻ 4★ → thêm hết, tổng 16 (không bịa)
    (7, 1, 8),         # dưới 25, chỉ có 1 thẻ 4★ → thêm hết, tổng 8
    (3, 5, 8), (5, 5, 10), (0, 9, 9),   # dưới 25 → thêm hết 4★ có
    (28, 20, 30),      # 28 → lấp hàng cuối thành 30 (6 hàng)
    (35, 20, 35), (62, 20, 65),        # nhiều 5★: chỉ lấp hàng, không trần 50 trên một khối
])
def test_select_characters_counts(n5, fours, total):
    got = select_characters([_c(i, 5) for i in range(n5)] + [_c(100 + i, 4, level=80) for i in range(fours)])
    assert len(got) == total
    assert sum(c.rarity == 5 for c in got) == (n5 if total >= n5 else 0)
    assert len(got) % 5 == 0 or len(got) == n5 + fours  # hàng cuối đủ 5, trừ khi hết nhân vật để thêm


def test_select_characters_adds_only_strongest_fours_to_fill_row():
    fives = [_c(i, 5, cons=0, level=20) for i in range(28)]
    fours = [_c(100 + i, 4, cons=i % 4, level=(60, 50, 40, 20)[i % 4]) for i in range(12)]
    got = select_characters(fours + fives)                 # 28 → thêm đúng 2 thẻ 4★ mạnh nhất → 30
    rank = sorted(fours, key=lambda c: (-c.constellation, -c.level, c.avatar_id))
    assert len(got) == 30 and {c.avatar_id for c in got if c.rarity == 4} == {c.avatar_id for c in rank[:2]}
    keys = [(-c.rarity, -c.level, -c.constellation, c.avatar_id) for c in got]
    assert keys == sorted(keys) and got[0].rarity == 5                 # thứ tự game, 5★ trước


def test_select_characters_minimum_five_rows():
    fives = [_c(i, 5, cons=0, level=20) for i in range(12)]
    fours = [_c(100 + i, 4, cons=i % 4, level=(60, 50, 40, 20)[i % 4]) for i in range(20)]
    got = select_characters(fours + fives)                 # 12 → thêm 13 thẻ 4★ mạnh nhất → 25 (5 hàng)
    rank = sorted(fours, key=lambda c: (-c.constellation, -c.level, c.avatar_id))
    assert len(got) == 25 and len(got) % 5 == 0
    assert sum(c.rarity == 5 for c in got) == 12
    assert {c.avatar_id for c in got if c.rarity == 4} == {c.avatar_id for c in rank[:13]}


def test_select_characters_48_fives_draws_50():
    got = select_characters([_c(i, 5) for i in range(48)] + [_c(100 + i, 4, cons=i % 3) for i in range(20)])
    assert len(got) == 50 and sum(c.rarity == 5 for c in got) == 48     # lấp đúng 2 thẻ 4★ cho đủ hàng


def test_report_states_total_when_account_has_under_25_characters(tmp_path):
    _, sheet, report = _build(tmp_path, 20)
    text = report.text_vi()
    assert len(sheet.characters) == 20                       # vẽ hết số có sẵn, không bịa thêm
    assert "tổng 20 nhân vật" in text and "không thêm nhân vật nào" in text


def test_select_characters_no_fixed_cap():
    got = select_characters([_c(i, 5) for i in range(68)] + [_c(100 + i, 4) for i in range(9)])
    assert len(got) == 70 and not hasattr(hoyolab, "MAX_DRAWN_CHARACTERS")


def test_compact_profile_two_rows_or_one():
    two = draw_profile_box_compact(59, 9)
    assert two.size == (PROFILE_SIZE[0], 2 * PROFILE_COMPACT_PAD + 2 * PROFILE_COMPACT_ROW)
    one = draw_profile_box_compact(59, None)      # không có Cấp Thế Giới → bỏ hàng, không "–"
    assert one.size == (PROFILE_SIZE[0], 2 * PROFILE_COMPACT_PAD + PROFILE_COMPACT_ROW)
    nc = Image.new("RGB", (840, 400), (30, 60, 140))
    assert draw_profile_box_compact(59, 9, namecard=nc).size == two.size


def test_profile_uses_world_level_from_enka(tmp_path):
    _, _, report = _build(tmp_path, 12, EnkaExtras(world_level=9, source="Enka"))
    with Image.open(tmp_path / "work" / "profile.png") as im:
        assert im.height == 2 * PROFILE_COMPACT_PAD + 2 * PROFILE_COMPACT_ROW
    text = report.text_vi()
    assert "Cấp Thế Giới 9 (từ Enka)" in text and "Không có Cấp Thế Giới" not in text
    _build(tmp_path, 12)
    with Image.open(tmp_path / "work" / "profile.png") as im:
        assert im.height == 2 * PROFILE_COMPACT_PAD + PROFILE_COMPACT_ROW


def _enka_cache(root: Path, uid: str, world_level=9, name_card=210180):
    d = root / "enka-cache"
    d.mkdir(parents=True)
    raw = {"playerInfo": {"nickname": "x", "level": 59, "worldLevel": world_level, "nameCardId": name_card},
           "ttl": 60}
    (d / f"{uid}.json").write_text(json.dumps({"fetched_at": "2020-01-01T00:00:00+00:00", "ttl": 60,
                                               "raw": raw}), encoding="utf-8")
    store = root / "enka-store"
    store.mkdir()
    (store / "namecards.json").write_text(json.dumps(
        {str(name_card): {"Icon": "UI_NameCardPic_Test_P"}}), encoding="utf-8")


def test_enka_extras_from_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GACARD_DATA_DIR", str(tmp_path))
    _enka_cache(tmp_path, UID)
    ex = REAL_FETCH_EXTRAS(UID, offline=True)
    assert ex == EnkaExtras(world_level=9, namecard_icon="UI_NameCardPic_Test_P", source="cache Enka")

    def down(client, uid):
        raise enka.EnkaError("maintenance")
    monkeypatch.setattr(enka, "fetch_sync", down)          # Enka lỗi → cache cũ
    assert REAL_FETCH_EXTRAS(UID).world_level == 9
    assert REAL_FETCH_EXTRAS("800000002", offline=True) is None   # không có gì → ẩn hàng


def test_many_characters_layout_uid_above_weapons(tmp_path):
    _, sheet, report = _build(tmp_path, 48)
    cols = _rendered_weapon_cols(sheet, tmp_path / "work", RenderOptions())
    assert _full(len(sheet.characters), 5) and _full(len(sheet.weapons), cols)
    if len(sheet.characters) < 48:
        assert f"Đã vẽ {len(sheet.characters)}/48 nhân vật" in report.text_vi()
    img, layout, _ = render_image(sheet, tmp_path / "work", RenderOptions(uid=UID, code="1116"))
    assert img.size == (1920, 1080) and layout.problems() == []
    uid = layout.of("uid")[0]
    weapons = layout.of("weapons")[0].rect
    assert uid.text == f"CHECK BUILD UID {UID}" and uid.size == 29
    assert uid.rect.x == weapons.x - TEXT_STROKE and weapons.y - 40 < uid.rect.bottom < weapons.y


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GACARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GACARD_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("GACARD_HOYOLAB_COOKIE", COOKIE)
    return tmp_path


def test_cli_writes_image(cli_env, monkeypatch, capsys, net_attempts):
    monkeypatch.setattr(hoyolab, "urllib_transport", ok_transport(30))
    out = cli_env / "o" / "x.jpg"
    assert hoyolab.main([UID, "--code", "1116", "--out", str(out), "--watermark", "@hoavu", "--no-open"]) == 0
    with Image.open(out) as im:
        assert im.size == (1920, 1080)
    printed = capsys.readouterr()
    assert "Nhân vật (" in printed.out and str(out) in printed.out
    assert "SECRET" not in printed.out + printed.err
    assert all(allowed_icon_url(u) and "game_record/genshin/api" not in u for u in net_attempts)


def test_cli_default_out_name(monkeypatch, tmp_path):
    monkeypatch.setenv("GACARD_OUTPUT_DIR", str(tmp_path))
    assert hoyolab.default_out_path(UID, "CN#1076") == tmp_path / f"acc-CN1076-{UID}-hoyolab.jpg"
    assert hoyolab.default_out_path(UID, None) == tmp_path / f"acc-{UID}-hoyolab.jpg"


def test_cli_missing_cookie_exit_2(cli_env, monkeypatch, capsys):
    monkeypatch.delenv("GACARD_HOYOLAB_COOKIE")
    assert hoyolab.main([UID, "--out", str(cli_env / "x.jpg")]) == 2
    err = capsys.readouterr().err
    assert "GACARD_HOYOLAB_COOKIE" in err and "ltoken_v2" in err


def test_cli_cookie_from_env_file(cli_env, monkeypatch):
    env_file = cli_env / "real.env"
    env_file.write_text(f"GACARD_HOYOLAB_COOKIE={COOKIE}\n", encoding="utf-8")
    monkeypatch.setenv("GACARD_ENV_FILE", str(env_file))
    monkeypatch.delenv("GACARD_HOYOLAB_COOKIE")
    assert hoyolab.load_cookie() == COOKIE


def test_cli_no_open_verification_exit_1(cli_env, monkeypatch, capsys):
    opened, _ = _patch_browser(monkeypatch)
    tr = FakeTransport({"index": [VERIFY]})
    monkeypatch.setattr(hoyolab, "urllib_transport", tr)
    assert hoyolab.main([UID, "--out", str(cli_env / "x.jpg"), "--no-open"]) == 1
    assert opened == [] and len(tr.calls) == 1
    assert "verification" in capsys.readouterr().err


def test_cli_interactive_verification(cli_env, monkeypatch):
    opened, asked = _patch_browser(monkeypatch)
    tr = FakeTransport({"index": [VERIFY, envelope(make_index())],
                        "character/list": [envelope(make_character_list(12))]})
    monkeypatch.setattr(hoyolab, "urllib_transport", tr)
    out = cli_env / "x.jpg"
    assert hoyolab.main([UID, "--out", str(out)]) == 0
    assert out.is_file() and opened == [BATTLE_CHRONICLE_URL] and len(asked) == 1
    assert [c["path"] for c in tr.calls] == ["index", "index", "character/list"]
