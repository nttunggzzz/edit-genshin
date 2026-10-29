from __future__ import annotations

import logging

import pytest

from gacard.bot import app
from gacard.bot.config import ConfigError, load_config, parse_chat_ids


def test_missing_token_raises_vietnamese_error(tmp_path):
    env = {"GACARD_ENV_FILE": str(tmp_path / "none.env")}
    with pytest.raises(ConfigError) as ei:
        load_config(env)
    assert "GACARD_BOT_TOKEN" in ei.value.message_vi and "BotFather" in ei.value.message_vi


def test_env_file_and_override_precedence(tmp_path):
    f = tmp_path / "test.env"
    f.write_text(
        "# ghi chú\nGACARD_BOT_TOKEN='111:AAA'\nGACARD_ALLOWED_CHAT_IDS=5, -100200\n"
        "GACARD_DATA_DIR=dat\nGACARD_FIXTURE=1043\nTELEGRAM_BOT_TOKEN=999:GAIS\n",
        encoding="utf-8",
    )
    cfg = load_config({"GACARD_ENV_FILE": str(f), "GACARD_FIXTURE": "other"})
    assert cfg.token == "111:AAA"  # không bao giờ đọc token của GAIS
    assert cfg.allowed_chat_ids == frozenset({5, -100200})
    assert cfg.data_dir == tmp_path.resolve() / "dat"  # tương đối so với thư mục chứa .env
    assert cfg.fixture == "other"  # biến môi trường thắng file
    assert cfg.lock_file.parent == cfg.data_dir


def test_defaults_and_token_hidden_in_repr(tmp_path):
    cfg = load_config({"GACARD_ENV_FILE": str(tmp_path / "x.env"), "GACARD_BOT_TOKEN": "1:SECRETVALUE"})
    assert cfg.data_dir.parts[-2:] == ("data", "acc-card")
    assert cfg.allowed_chat_ids == frozenset() and cfg.enka_user_agent
    assert "SECRETVALUE" not in repr(cfg)


def test_bad_chat_ids():
    assert parse_chat_ids("") == frozenset()
    with pytest.raises(ConfigError) as ei:
        parse_chat_ids("12,abc")
    assert "abc" in ei.value.message_vi


def test_main_without_token_exits_2_with_friendly_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GACARD_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("GACARD_BOT_TOKEN", raising=False)
    assert app.main([]) == 2
    err = capsys.readouterr().err
    assert "GACARD_BOT_TOKEN" in err and "LỖI CẤU HÌNH" in err


def test_redact_filter_hides_token():
    f = app.RedactFilter("123:SECRET")
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "url https://api/bot%s/getMe", ("123:SECRET",), None)
    assert f.filter(rec)
    assert "SECRET" not in rec.getMessage() and "***" in rec.getMessage()
