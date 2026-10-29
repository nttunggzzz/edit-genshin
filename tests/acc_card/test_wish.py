"""Test gacard.wish (offline: transport giả, không bao giờ gọi mạng, không dùng cookie thật)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pytest

from gacard import wish
from gacard.wish import (STANDARD_5, banner_counter, compute_pity, counter_number, fetch_wish_history,
                         is_limited_5, merge_records)

UID = "800000001"
TOKEN = "account_id_v2=111; cookie_token_v2=v2_SECRETCOOKIETOKEN_zzzz; account_mid_v2=abc"
AUTHKEY = "SECRETAUTHKEY%2Babc%3D"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("test không được gọi mạng thật")
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def rec(i: int, name: str = "Filler", rank: str = "3", bucket: int = 301, gt: str | None = None) -> dict:
    return {"id": str(1000 + i), "gacha_type": gt or str(bucket), "bucket": bucket, "name": name,
            "item_type": "Character", "rank_type": rank, "time": f"2026-01-01 00:{i % 60:02d}:00"}


def history(n_after_five: int, five: str | None, bucket: int = 301, start: int = 0) -> list[dict]:
    """3 lượt thường, một lần 5★ (nếu có), rồi n_after_five lượt thường."""
    out, i = [], start
    for _ in range(3):
        out.append(rec(i, bucket=bucket))
        i += 1
    if five:
        out.append(rec(i, five, "5", bucket))
        i += 1
    for _ in range(n_after_five):
        out.append(rec(i, bucket=bucket))
        i += 1
    return out


# ------------------------------------------------------------------ luật số N
@pytest.mark.parametrize("pity,n", [(0, 1), (1, 1), (3, 1), (4, 1), (5, 1), (9, 1), (10, 2), (14, 2), (79, 15)])
def test_counter_number_rule(pity, n):
    assert counter_number(pity) == n


def test_standard_set_and_limited_detection():
    for name in ("Diluc", "Jean", "Mona", "Qiqi", "Keqing", "Tighnari", "Dehya", "Yumemizuki Mizuki"):
        assert name in STANDARD_5 and not is_limited_5(name)
    assert is_limited_5("Skirk") and is_limited_5("Zibai") and not is_limited_5(None)


def test_banner_counter_limited_standard_and_none():
    c = banner_counter(history(79, "Skirk"))
    assert (c.pity, c.n, c.limited, c.last5_name) == (79, 15, True, "Skirk")
    c = banner_counter(history(3, "Keqing"))
    assert (c.pity, c.n, c.limited, c.last5_name) == (3, 1, False, "Keqing")
    c = banner_counter(history(12, None))
    assert (c.pity, c.n, c.limited, c.last5_name, c.total) == (15, 3, None, None, 15)
    assert banner_counter([]) is None
    assert banner_counter(history(5, "Amos Bow", bucket=302)) is None      # chỉ banner nhân vật mới tính


def test_pity_is_per_banner_and_400_merges_into_301():
    recs = history(4, "Skirk", 301) + history(7, "Mona", 301, start=50)
    recs.append(rec(200, "Mona", "5", 301, gt="400"))          # banner 400 chung pity với 301
    recs.append(rec(201, bucket=301, gt="400"))
    recs += history(20, "Amos Bow", 302, start=300) + history(2, None, 200, start=400)
    info = compute_pity(recs)
    assert info[301].pity == 1 and info[301].last5_name == "Mona" and info[301].prev5_name == "Mona"
    assert info[301].guaranteed is True                         # Mona = thường trú → lần tới chắc chắn rate-up
    assert info[302].pity == 20 and info[302].last5_name == "Amos Bow"
    assert info[200].pity == 5 and info[200].last5_name is None


def test_compute_pity_orders_by_id_not_input_order():
    recs = history(6, "Skirk")
    assert compute_pity(list(reversed(recs)))[301].pity == 6


# ------------------------------------------------------------------ lấy lịch sử (transport giả)
class FakeServer:
    """authkey + getGachaLog theo banner; mỗi banner trả trang 20 dòng mới → cũ."""

    def __init__(self, data: dict[int, list[dict]], authkey_retcode: int = 0, log_retcode: int = 0,
                 uid: str = UID):
        self.data, self.authkey_retcode, self.log_retcode, self.uid = data, authkey_retcode, log_retcode, uid
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url))
        if "genAuthKeyByCookieToken" in url:
            assert "cookie_token_v2" in headers["Cookie"] and json.loads(body)["game_uid"] == UID
            doc = {"retcode": self.authkey_retcode, "message": "x", "data": {"authkey": AUTHKEY}}
            return 200, json.dumps(doc).encode()
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert urllib.parse.unquote(AUTHKEY) == q["authkey"][0]
        if self.log_retcode:
            return 200, json.dumps({"retcode": self.log_retcode, "message": "visit too frequently",
                                    "data": None}).encode()
        gt, end = int(q["gacha_type"][0]), int(q["end_id"][0])
        rows = sorted(self.data.get(gt, []), key=lambda r: -int(r["id"]))
        rows = [r for r in rows if end == 0 or int(r["id"]) < end][:20]
        page = [{"uid": self.uid, "gacha_type": r["gacha_type"], "name": r["name"], "item_type": r["item_type"],
                 "rank_type": r["rank_type"], "id": r["id"], "time": r["time"]} for r in rows]
        return 200, json.dumps({"retcode": 0, "message": "OK", "data": {"list": page}}).encode()

    def log_calls(self) -> int:
        return sum(1 for _, u in self.calls if "getGachaLog" in u)


def run(tmp_path, server, now=1_000_000.0, token=TOKEN, **kw):
    return fetch_wish_history(UID, directory=tmp_path, transport=server, cookie_token=token,
                              now=lambda: now, sleep=lambda s: None, **kw)


def full_data():
    return {301: history(30, "Skirk"), 302: history(5, None, 302, start=500), 200: [], 500: []}


def test_fetch_pages_merges_and_saves_without_secrets(tmp_path):
    server = FakeServer(full_data())
    got = run(tmp_path, server)
    assert got.reason is None and got.source == "HoYoLAB" and len(got.records) == 34 + 8
    assert server.log_calls() >= 4 + 1                      # >1 trang cho banner 301 (34 dòng)
    saved = (tmp_path / f"{UID}.json").read_text(encoding="utf-8")
    for secret in ("SECRETCOOKIETOKEN", "SECRETAUTHKEY", "cookie_token", "authkey", "account_id_v2"):
        assert secret not in saved
    blob = json.loads(saved)
    assert blob["uid"] == UID and len(blob["list"]) == 42 and set(blob["list"][0]) == set(wish.RECORD_KEYS)
    assert banner_counter(got.records).limited is True


def test_fetch_uses_cache_for_30_minutes_then_refreshes_incrementally(tmp_path):
    server = FakeServer(full_data())
    run(tmp_path, server)
    first = len(server.calls)
    again = run(tmp_path, server, now=1_000_000.0 + 29 * 60, token=None)       # cache: không gọi mạng, không cần token
    assert len(server.calls) == first and again.source.startswith("cache") and len(again.records) == 42
    data = full_data()
    data[301] = data[301] + [rec(900, "Mavuika", "5")]
    server2 = FakeServer(data)
    later = run(tmp_path, server2, now=1_000_000.0 + 31 * 60)
    assert later.source == "HoYoLAB" and later.new_count == 1 and len(later.records) == 43
    assert (tmp_path / f"{UID}.json.bak").is_file()
    assert server2.log_calls() <= 4 + 1                      # gặp id đã lưu thì dừng, không tải lại toàn bộ


def test_merge_dedupes_by_id():
    a, b = history(3, "Skirk"), history(3, "Skirk")
    assert len(merge_records(a, b)) == len(a)


def test_no_cookie_token_returns_reason_without_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("GACARD_HOYOLAB_COOKIE_TOKEN", raising=False)
    monkeypatch.setenv("GACARD_ENV_FILE", str(tmp_path / "none.env"))
    server = FakeServer(full_data())
    got = fetch_wish_history(UID, directory=tmp_path, transport=server, now=lambda: 1.0)
    assert got.records is None and "GACARD_HOYOLAB_COOKIE_TOKEN" in got.reason and not server.calls


def test_cookie_without_cookie_token_is_rejected(tmp_path):
    server = FakeServer(full_data())
    got = run(tmp_path, server, token="ltuid_v2=1; ltoken_v2=abc")
    assert got.records is None and "cookie_token" in got.reason and not server.calls


def test_authkey_error_stops_without_retry_and_hides_secrets(tmp_path):
    server = FakeServer(full_data(), authkey_retcode=-100)
    got = run(tmp_path, server)
    assert got.records is None and "-100" in got.reason and len(server.calls) == 1
    assert "SECRETCOOKIETOKEN" not in got.reason and not (tmp_path / f"{UID}.json").exists()


def test_log_retcode_stops_at_first_error(tmp_path):
    server = FakeServer(full_data(), log_retcode=-110)
    got = run(tmp_path, server)
    assert got.records is None and "giới hạn" in got.reason and server.log_calls() == 1
    assert "SECRETAUTHKEY" not in got.reason and not (tmp_path / f"{UID}.json").exists()


def test_uid_mismatch_is_not_saved(tmp_path):
    got = run(tmp_path, FakeServer(full_data(), uid="999999999"))
    assert got.records is None and "UID khác" in got.reason and not (tmp_path / f"{UID}.json").exists()


def test_invalid_uid(tmp_path):
    got = fetch_wish_history("12", directory=tmp_path, cookie_token=TOKEN)
    assert got.records is None and got.reason
