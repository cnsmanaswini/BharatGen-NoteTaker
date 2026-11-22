from app.config.settings import Settings
from app.main import MEET_URL_RE, parse_args


def test_defaults(monkeypatch):
    for k in ("BROWSER_CHANNEL", "HEADLESS", "BOT_DISPLAY_NAME", "ASR_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert s.bot_display_name == "BharatGen Notetaker"
    assert s.headless is False
    assert s.browser_channel == "chrome"
    assert s.asr_enabled is False
    assert s.audio_enabled is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.setenv("BROWSER_CHANNEL", "")
    s = Settings.from_env()
    assert s.headless is True and s.browser_channel == ""


def test_meet_url_validation():
    assert MEET_URL_RE.match("https://meet.google.com/abc-defg-hij")
    assert not MEET_URL_RE.match("https://example.com/abc-defg-hij")


def test_args_optional():
    assert parse_args([]).meet_url is None
