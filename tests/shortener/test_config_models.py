from datetime import datetime, timedelta, timezone

import pytest

from shortener.config import Settings
from shortener.models import Link


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("SHORTENER_BASE_URL", "https://sho.rt/")
    monkeypatch.setenv("SHORTENER_CODE_LENGTH", "9")
    settings = Settings.from_env()
    assert settings.base_url == "https://sho.rt"
    assert settings.code_length == 9


def test_settings_reject_non_integer(monkeypatch):
    monkeypatch.setenv("SHORTENER_CODE_LENGTH", "seven")
    with pytest.raises(ValueError, match="SHORTENER_CODE_LENGTH"):
        Settings.from_env()


def test_link_expiry_boundary():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    link = Link("abc", "https://example.com", created, created + timedelta(seconds=10))
    assert not link.is_expired(created + timedelta(seconds=9))
    assert link.is_expired(created + timedelta(seconds=10))
    assert not Link("abc", "https://example.com", created).is_expired()


def test_abuse_control_settings_from_env(monkeypatch):
    monkeypatch.setenv("SHORTENER_CREATE_RATE_LIMIT", "5")
    monkeypatch.setenv("SHORTENER_BLOCKED_DOMAINS", "Evil.test, ,bad.example")
    settings = Settings.from_env()
    assert settings.create_rate_limit_per_minute == 5
    assert settings.blocked_domains == ("evil.test", "bad.example")
