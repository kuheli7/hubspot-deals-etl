"""Unit tests for the startup checks in config.py."""
import pytest

from config import Config


def test_startup_fails_without_config_password(monkeypatch):
    monkeypatch.setattr(Config, "ENCRYPTION_ENABLED", True)
    monkeypatch.setattr(Config, "ENCRYPTION_PASSWORD", None)
    with pytest.raises(ValueError, match="CONFIG_PASSWORD"):
        Config.validate_required_settings()


def test_startup_accepts_a_set_config_password(monkeypatch):
    monkeypatch.setattr(Config, "ENCRYPTION_PASSWORD", "a-real-key")
    Config.validate_required_settings()
