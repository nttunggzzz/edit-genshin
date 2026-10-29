from gacard import theme
from gacard.theme import FONT_DIR, font, reset_font_cache

STAND_IN = FONT_DIR / "BeVietnamPro-Bold.ttf"


def _fresh(monkeypatch, value):
    monkeypatch.setenv("GACARD_DISPLAY_FONT", value)
    reset_font_cache()


def test_missing_display_font_falls_back(monkeypatch, tmp_path):
    _fresh(monkeypatch, str(tmp_path / "nope.ttf"))
    monkeypatch.setattr(theme, "DISPLAY_FONT_DEFAULT", tmp_path / "also-missing.ttf")
    reset_font_cache()
    f = font(40, "display")
    assert f.path == font(40, "extrabold").path
    reset_font_cache()


def test_display_font_uses_env_path(monkeypatch):
    _fresh(monkeypatch, str(STAND_IN))
    assert font(40, "display").path == str(STAND_IN)
    assert font(40, "extrabold").path != str(STAND_IN)
    reset_font_cache()
