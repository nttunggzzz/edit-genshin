"""render-v3: ô banner vũ khí (302), khung thẻ tháng và khung số roll còn (nhập ngoài), bỏ hàng loạt trên web.
Dữ liệu giả, không mạng, không đăng nhập."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest
from PIL import Image

from gacard import bannerbox, webapp
from gacard.hoyolab import UrlIconCache, build_sheet_from_data
from gacard.models import ItemKind, RenderOptions
from gacard.render import render_image
from gacard.uidmode import IconCache
from gacard.wish import BannerCounter, banner_counter, banner_counter_for, counter_number, weapon_counter

from .hoyolab_fixture import make_character_list, make_index
from .test_wish import history


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("offline (test)")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def same(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes()


# ------------------------------------------------------------------ wish: bucket 302
def test_weapon_counter_uses_same_rule_on_bucket_302():
    recs = history(79, "Skirk", bucket=301) + history(23, "Amos Bow", bucket=302, start=500)
    w = weapon_counter(recs)
    assert w is not None and w.pity == 23 and w.total == 27 and w.n == counter_number(23) == 4


def test_weapon_counter_min_one_and_pity_not_summed_between_banners():
    recs = history(2, None, bucket=301) + history(3, None, bucket=302, start=500)
    assert weapon_counter(recs).n == 1                      # pity vũ khí 6 // 5 = 1
    recs = history(40, None, bucket=301) + history(12, None, bucket=302, start=500)
    assert weapon_counter(recs).pity == 15 and weapon_counter(recs).n == 3   # không cộng 301 vào


def test_weapon_counter_none_when_no_weapon_rolls():
    assert weapon_counter(history(30, "Skirk", bucket=301)) is None
    assert weapon_counter([]) is None


def test_character_counter_unchanged_by_refactor():
    recs = history(79, "Skirk", bucket=301) + history(23, None, bucket=302, start=500)
    c = banner_counter(recs)
    assert (c.n, c.pity, c.total, c.last5_name, c.limited) == (15, 79, 83, "Skirk", True)
    assert banner_counter_for(recs, 301) == c
    assert banner_counter(history(5, "Amos Bow", bucket=302)) is None
    assert banner_counter(history(3, "Keqing")).limited is False


# ------------------------------------------------------------------ sheet
def _sheet(tmp_path, options=None, wish=None, weapon=None):
    index, lst = make_index(30), make_character_list(30)
    return build_sheet_from_data(index, lst, tmp_path / "work", UrlIconCache(tmp_path / "h", offline=True),
                                 IconCache(tmp_path / "e", offline=True), None,
                                 options or RenderOptions(uid="800000001"), wish, ["Lịch sử roll: thử"],
                                 banner_templates=tmp_path / "none", weapon_wish=weapon)


def test_weapon_banner_item_added_next_to_character_banner(tmp_path):
    sheet, report = _sheet(tmp_path, wish=BannerCounter(n=15, pity=79, total=100, last5_name="Skirk", limited=True),
                           weapon=BannerCounter(n=4, pity=23, total=27))
    assert [i.kind for i in sheet.items] == [ItemKind.BANNER_COUNTER, ItemKind.BANNER_COUNTER]
    wp = Image.open(tmp_path / "work" / "item_wp.png")
    assert same(wp, bannerbox.render_banner_box(bannerbox.fallback_template("weapon"), 4))
    assert "Ô banner vũ khí: N = 4" in report.text_vi()


def test_no_weapon_banner_when_total_zero(tmp_path):
    recs = history(10, "Skirk", bucket=301)             # không có lượt roll vũ khí
    sheet, _ = _sheet(tmp_path, wish=banner_counter(recs), weapon=weapon_counter(recs))
    assert [i.kind for i in sheet.items] == [ItemKind.BANNER_COUNTER]
    assert not (tmp_path / "work" / "item_wp.png").exists()


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_month_card_draws_nothing(tmp_path, text):
    sheet, report = _sheet(tmp_path, RenderOptions(uid="800000001", month_card=text))
    assert sheet.items == [] and not (tmp_path / "work" / "item_month.png").exists()
    assert "Khung thẻ tháng" not in report.text_vi()


def test_empty_rolls_draws_nothing(tmp_path):
    sheet, report = _sheet(tmp_path, RenderOptions(uid="800000001", rolls_left=None))
    assert sheet.items == [] and not (tmp_path / "work" / "item_roll.png").exists()
    assert "Khung số roll" not in report.text_vi()


def test_month_card_draws_exact_text(tmp_path):
    sheet, _ = _sheet(tmp_path, RenderOptions(uid="800000001", month_card="Còn 15 ngày"))
    (item,) = sheet.items
    assert item.kind == ItemKind.PORTRAIT
    tpl = bannerbox.fallback_template("monthcard")
    got = Image.open(tmp_path / "work" / "item_month.png")
    assert same(got, bannerbox.render_text_box(tpl, "Còn 15 ngày"))
    assert not same(got, bannerbox.render_text_box(tpl, "Còn 16 ngày"))
    assert not same(got, tpl.image)                      # có chữ thật sự


def test_rolls_left_draws_exact_number_even_zero(tmp_path):
    sheet, _ = _sheet(tmp_path, RenderOptions(uid="800000001", rolls_left=84))
    (item,) = sheet.items
    assert item.kind == ItemKind.FATE
    tpl = bannerbox.fallback_template("roll")
    assert same(Image.open(tmp_path / "work" / "item_roll.png"), bannerbox.render_banner_box(tpl, 84))
    sheet0, _ = _sheet(tmp_path / "z", RenderOptions(uid="800000001", rolls_left=0))
    assert len(sheet0.items) == 1 and sheet0.items[0].kind == ItemKind.FATE


def test_full_set_layout_has_four_items_without_overlap(tmp_path):
    opts = RenderOptions(uid="800000001", month_card="Còn 15 ngày", rolls_left=84)
    sheet, _ = _sheet(tmp_path, opts, BannerCounter(n=1, pity=3, total=9, last5_name="Skirk", limited=True),
                      BannerCounter(n=3, pity=16, total=40))
    _, layout, _ = render_image(sheet, tmp_path / "work", opts)
    items = [b.rect for b in layout.boxes if b.kind == "item"]
    assert len(items) == 4 and layout.problems() == []
    # thứ tự dải: thẻ tháng, roll, banner nhân vật, banner vũ khí (trái -> phải); thẻ tháng cao hơn
    assert [r.x for r in items] == sorted(r.x for r in items)
    assert items[0].h > items[1].h == items[2].h == items[3].h


def test_empty_case_keeps_only_character_banner(tmp_path):
    sheet, _ = _sheet(tmp_path, wish=BannerCounter(n=1, pity=3, total=9, last5_name="Skirk", limited=True))
    assert [i.kind for i in sheet.items] == [ItemKind.BANNER_COUNTER]


# ------------------------------------------------------------------ web: bỏ hàng loạt, ô nhập
def _client_call(method, path, **kw):
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        app = webapp.create_app()
        async with TestClient(TestServer(app)) as c:
            resp = await getattr(c, method)(path, **kw)
            return resp.status, await resp.text(), app
    return asyncio.run(go())


@pytest.mark.parametrize("method,path", [("get", "/batch"), ("post", "/api/batch"), ("get", "/api/batch/abc"),
                                         ("get", "/api/batch/abc/download-zip")])
def test_batch_routes_return_removed_notice(method, path):
    status, body, _ = _client_call(method, path)
    body = json.loads(body)["error"] if path.startswith("/api/") else body
    assert status == 410 and "Đã bỏ chế độ hàng loạt, chỉ dùng Ghép đơn" in body


def test_single_page_has_no_batch_tab_but_has_new_inputs():
    status, body, _ = _client_call("get", "/single")
    assert status == 200 and "/batch" not in body
    assert 'id="month_card"' in body and 'id="rolls_left"' in body


def test_single_job_receives_optional_inputs(monkeypatch):
    monkeypatch.setattr(webapp, "run_single_job_thread", lambda job, state: None)
    payload = {"code": "1116", "username": "u", "password": "p", "month_card": " Còn 15 ngày ", "rolls_left": "84"}
    status, _, app = _client_call("post", "/api/single", json=payload)
    assert status == 200
    job = next(iter(app["state"].single_jobs.values()))
    assert job.month_card == "Còn 15 ngày" and job.rolls_left == 84


def test_single_job_optional_inputs_default_empty(monkeypatch):
    monkeypatch.setattr(webapp, "run_single_job_thread", lambda job, state: None)
    status, _, app = _client_call("post", "/api/single", json={"code": "1116", "username": "u", "password": "p"})
    job = next(iter(app["state"].single_jobs.values()))
    assert status == 200 and job.month_card == "" and job.rolls_left is None


def test_single_rejects_non_integer_rolls(monkeypatch):
    monkeypatch.setattr(webapp, "run_single_job_thread", lambda job, state: None)
    status, body, _ = _client_call("post", "/api/single",
                                   json={"code": "1116", "username": "u", "password": "p", "rolls_left": "abc"})
    assert status == 400 and "số nguyên" in json.loads(body)["error"]
