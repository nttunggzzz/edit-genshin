"""Test gacard.enka: parse_profile (thuần) + EnkaClient qua máy chủ aiohttp giả (không gọi mạng thật)."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp import web

from gacard.enka import EnkaClient, EnkaError, format_summary_vi, parse_profile, validate_uid

FIX = Path(__file__).parent / "fixtures_enka"
UID = "800000001"
UA = "gacard-test/0.1 (pytest)"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parse_profile
def test_parse_detailed():
    p = parse_profile(load("detailed.json"), UID)
    assert (p.uid, p.nickname, p.adventure_rank, p.world_level) == (UID, "TestUser", 60, 9)
    got = [(a.character, a.level, a.constellation) for a in p.avatars]
    assert got == [
        ("kamisato-ayaka", 90, 0),  # có chi tiết nhưng không có talentIdList = 0 cung
        ("nilou", 90, 1),
        ("ganyu", 90, 0),
        ("keqing", 70, 1),
    ]
    assert p.avatars[0].avatar_id == 10000002


def test_parse_showcase_only_uses_talent_level_when_shown():
    raw = load("showcase_only.json")
    assert "avatarInfoList" not in raw
    p = parse_profile(raw, UID)
    assert len(p.avatars) == 12
    by = {a.character: a for a in p.avatars}
    assert by["xiao"].constellation == 1 and by["xiao"].level == 90
    assert by["zhongli"].constellation == 0 and by["zhongli"].level == 80


def test_parse_showcase_only_hidden_talents_gives_none():
    raw = load("showcase_only.json")
    raw["playerInfo"]["isShowAvatarTalent"] = False
    p = parse_profile(raw, UID)
    assert len(p.avatars) == 12
    assert all(a.constellation is None for a in p.avatars)
    assert all(a.level is not None for a in p.avatars)
    assert "chưa có cung mệnh" in format_summary_vi(p)


def test_parse_empty_showcase():
    p = parse_profile(load("empty_showcase.json"), UID)
    assert p.avatars == [] and p.nickname == "TestUser" and p.adventure_rank == 60
    assert "trống" in format_summary_vi(p)


def test_parse_traveler_and_new_characters():
    p = parse_profile(load("traveler_new.json"), UID)
    by = {a.avatar_id: a for a in p.avatars}
    assert by[10000007].character == "lumine" and by[10000007].constellation == 3
    assert by[10000125].character == "columbina" and by[10000125].constellation == 2
    assert by[10000126].character == "zibai" and by[10000126].constellation == 0


def test_parse_unknown_avatar_keeps_entry():
    raw = load("detailed.json")
    raw["playerInfo"]["showAvatarInfoList"].append({"avatarId": 19999999, "level": 10})
    p = parse_profile(raw, UID)
    last = p.avatars[-1]
    assert (last.avatar_id, last.character, last.level, last.constellation) == (19999999, None, 10, 0)


def test_parse_detail_without_show_entry_is_kept():
    raw = load("detailed.json")
    raw["playerInfo"]["showAvatarInfoList"] = raw["playerInfo"]["showAvatarInfoList"][:1]
    p = parse_profile(raw, UID)
    assert len(p.avatars) == 4  # 3 mục chỉ có chi tiết vẫn được giữ, level lấy từ propMap
    assert p.avatars[-1].level == 70 and p.avatars[-1].constellation == 1


def test_parse_missing_player_info():
    with pytest.raises(EnkaError) as e:
        parse_profile({"foo": 1}, UID)
    assert e.value.kind == "bad_response"


@pytest.mark.parametrize("bad", ["", "12345678", "12345678901", "81350039a", "８１３５００３９１", None])
def test_validate_uid_rejects(bad):
    with pytest.raises(EnkaError) as e:
        validate_uid(bad)
    assert e.value.kind == "invalid_uid" and e.value.message_vi


def test_validate_uid_ok():
    assert validate_uid(" 813500391 ") == "813500391"
    assert validate_uid(1234567890) == "1234567890"


# --------------------------------------------------------------- client + fake server
class Fake:
    """Máy chủ Enka giả: đếm request, trả status/body tùy chỉnh."""

    def __init__(self):
        self.status = 200
        self.body: dict | str = load("detailed.json")
        self.delay = 0.0
        self.calls = 0
        self.user_agents: list[str | None] = []
        self.paths: list[str] = []

    async def handler(self, request: web.Request) -> web.Response:
        self.calls += 1
        self.user_agents.append(request.headers.get("User-Agent"))
        self.paths.append(request.path)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status != 200:
            return web.Response(status=self.status, text="err")
        if isinstance(self.body, str):
            return web.Response(text=self.body, content_type="application/json")
        return web.json_response(self.body)


def run_with_server(fake: Fake, coro_fn):
    async def main():
        app = web.Application()
        app.router.add_get("/api/uid/{uid}/", fake.handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            return await coro_fn(f"http://127.0.0.1:{port}")
        finally:
            await runner.cleanup()

    return asyncio.run(main())


def make_client(tmp_path, base_url, **kw) -> EnkaClient:
    return EnkaClient(tmp_path / "cache", UA, timeout=kw.pop("timeout", 5.0), base_url=base_url, **kw)


def test_fetch_ok_sends_user_agent_and_caches(tmp_path):
    fake = Fake()

    async def go(url):
        c = make_client(tmp_path, url)
        p1 = await c.fetch(UID)
        p2 = await c.fetch(UID)  # trong ttl (60s) -> đọc cache
        return p1, p2

    p1, p2 = run_with_server(fake, go)
    assert fake.calls == 1
    assert fake.user_agents == [UA] and fake.paths == [f"/api/uid/{UID}/"]
    assert p1.nickname == "TestUser" and len(p1.avatars) == 4
    assert p2.fetched_at == p1.fetched_at and p2.avatars == p1.avatars
    assert (tmp_path / "cache" / f"{UID}.json").exists()


def test_cache_expires_after_ttl(tmp_path):
    fake = Fake()
    fake.body = {**load("detailed.json"), "ttl": 0}

    async def go(url):
        c = make_client(tmp_path, url)
        await c.fetch(UID)
        await c.fetch(UID)

    run_with_server(fake, go)
    assert fake.calls == 2


def test_cache_respects_ttl_in_file(tmp_path):
    fake = Fake()
    cache = tmp_path / "cache"
    cache.mkdir()
    old = datetime.now(timezone.utc) - timedelta(seconds=30)

    def write(fetched, ttl):
        (cache / f"{UID}.json").write_text(
            json.dumps({"fetched_at": fetched.isoformat(), "ttl": ttl, "raw": load("detailed.json")}), encoding="utf-8"
        )

    async def go(url):
        c = make_client(tmp_path, url)
        write(old, 60)  # còn hạn -> không gọi mạng
        await c.fetch(UID)
        assert fake.calls == 0
        write(old, 10)  # hết hạn -> gọi mạng
        await c.fetch(UID)
        assert fake.calls == 1

    run_with_server(fake, go)


@pytest.mark.parametrize(
    "status,kind",
    [(400, "invalid_uid"), (404, "not_found"), (424, "maintenance"), (429, "rate_limited"), (500, "server"), (503, "server")],
)
def test_http_error_mapping(tmp_path, status, kind):
    fake = Fake()
    fake.status = status

    async def go(url):
        with pytest.raises(EnkaError) as e:
            await make_client(tmp_path, url).fetch(UID)
        return e.value

    err = run_with_server(fake, go)
    assert err.kind == kind and err.message_vi
    assert not (tmp_path / "cache" / f"{UID}.json").exists()


def test_invalid_uid_makes_no_request(tmp_path):
    fake = Fake()

    async def go(url):
        with pytest.raises(EnkaError) as e:
            await make_client(tmp_path, url).fetch("123")
        assert e.value.kind == "invalid_uid"

    run_with_server(fake, go)
    assert fake.calls == 0


def test_bad_json_body(tmp_path):
    fake = Fake()
    fake.body = "<html>oops</html>"

    async def go(url):
        with pytest.raises(EnkaError) as e:
            await make_client(tmp_path, url).fetch(UID)
        return e.value

    assert run_with_server(fake, go).kind == "bad_response"
    assert not (tmp_path / "cache" / f"{UID}.json").exists()


def test_timeout_maps_to_network(tmp_path):
    fake = Fake()
    fake.delay = 1.0

    async def go(url):
        with pytest.raises(EnkaError) as e:
            await make_client(tmp_path, url, timeout=0.2).fetch(UID)
        return e.value

    assert run_with_server(fake, go).kind == "network"


def test_connection_refused_maps_to_network(tmp_path):
    async def go():
        with pytest.raises(EnkaError) as e:
            await make_client(tmp_path, "http://127.0.0.1:9").fetch(UID)
        return e.value

    assert asyncio.run(go()).kind == "network"


def test_stale_cache_used_on_transient_error(tmp_path):
    fake = Fake()
    cache = tmp_path / "cache"
    cache.mkdir()
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    (cache / f"{UID}.json").write_text(
        json.dumps({"fetched_at": old.isoformat(), "ttl": 60, "raw": load("detailed.json")}), encoding="utf-8"
    )
    fake.status = 429

    async def go(url):
        p = await make_client(tmp_path, url).fetch(UID)  # ttl hết, Enka 429 -> dùng bản cũ (<6h)
        assert p.fetched_at == old and len(p.avatars) == 4
        fake.status = 404  # lỗi không tạm thời -> không dùng cache cũ
        with pytest.raises(EnkaError) as e:
            await make_client(tmp_path, url).fetch(UID)
        assert e.value.kind == "not_found"
        fake.status = 429
        with pytest.raises(EnkaError):  # cache quá cũ
            await make_client(tmp_path, url, stale_max_age=60).fetch(UID)

    run_with_server(fake, go)


def test_corrupt_cache_is_ignored(tmp_path):
    fake = Fake()
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / f"{UID}.json").write_text("{not json", encoding="utf-8")

    async def go(url):
        return await make_client(tmp_path, url).fetch(UID)

    p = run_with_server(fake, go)
    assert fake.calls == 1 and p.nickname == "TestUser"


def test_requires_user_agent(tmp_path):
    with pytest.raises(ValueError):
        EnkaClient(tmp_path, "  ")


def test_summary_vietnamese_has_names():
    text = format_summary_vi(parse_profile(load("traveler_new.json"), UID))
    assert "Columbina" in text and "C2" in text and "Nhà Lữ Hành (Lumine)" in text
