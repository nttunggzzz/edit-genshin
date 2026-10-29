from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from gacard.bot.session import AlreadyRunning, Debouncer, InstanceLock, SessionManager

from .test_bot_support import FakeClock


def make(tmp_path, ttl=900):
    clock = FakeClock()
    mgr = SessionManager(tmp_path / "sessions", ttl=ttl, clock=clock, now=lambda: datetime(2026, 10, 21, 1, 2, 3))
    return mgr, clock


def test_session_dir_naming_and_uniqueness(tmp_path):
    m, _ = make(tmp_path)
    a = m.get_or_create(-100123)
    assert a.dir.name == "-100123-20261021-010203" and a.dir.is_dir()
    assert m.get_or_create(-100123) is a
    m.drop(-100123)
    b = m.get_or_create(-100123)  # cùng giây, thư mục cũ đã xoá
    assert b is not a
    c = m.get_or_create(7)
    assert c.dir != b.dir


def test_expire_idle_boundary_and_busy(tmp_path):
    m, clock = make(tmp_path, ttl=900)
    s = m.get_or_create(1)
    clock.advance(900)
    assert m.expire_idle() == []  # đúng 15 phút chưa quá hạn
    clock.advance(1)
    s.busy = True
    assert m.expire_idle() == []  # đang render thì không xoá
    s.busy = False
    assert m.expire_idle() == [1]
    assert not s.dir.exists() and m.get(1) is None


def test_get_drops_expired_lazily(tmp_path):
    m, clock = make(tmp_path)
    s = m.get_or_create(1)
    clock.advance(1000)
    assert m.get(1) is None and not s.dir.exists()


def test_purge_orphans(tmp_path):
    m, _ = make(tmp_path)
    m.sessions_dir.mkdir(parents=True)
    (m.sessions_dir / "9-old").mkdir()
    keep = m.get_or_create(1)
    assert m.purge_orphans() == 1
    assert keep.dir.exists() and not (m.sessions_dir / "9-old").exists()


def test_debouncer_collapses_burst_into_one_call():
    async def go():
        calls = []
        d = Debouncer(0.05)

        async def cb():
            calls.append(1)

        for _ in range(5):
            d.trigger("k", cb)
            await asyncio.sleep(0.01)
        assert calls == []
        await d.drain()
        d.trigger("k", cb)
        await d.drain()
        return calls

    assert asyncio.run(go()) == [1, 1]


def test_debouncer_with_fake_sleep_and_independent_keys():
    async def go():
        waited = []

        async def fake_sleep(t):
            waited.append(t)

        calls = []
        d = Debouncer(2.0, sleep=fake_sleep)

        def cb(name):
            async def _():
                calls.append(name)

            return _

        d.trigger("a", cb("a"))
        d.trigger("b", cb("b"))
        await d.drain()
        return calls, waited

    calls, waited = asyncio.run(go())
    assert sorted(calls) == ["a", "b"] and waited == [2.0, 2.0]


def test_debouncer_swallows_callback_errors():
    async def go():
        d = Debouncer(0)

        async def bad():
            raise RuntimeError("x")

        d.trigger(1, bad)
        await d.drain()

    asyncio.run(go())


def test_instance_lock_blocks_second_holder(tmp_path):
    path = tmp_path / "data" / "bot.lock"
    first = InstanceLock(path)
    first.acquire()
    with pytest.raises(AlreadyRunning):
        InstanceLock(path).acquire()
    first.release()
    with InstanceLock(path):  # nhả rồi thì lấy lại được
        pass
