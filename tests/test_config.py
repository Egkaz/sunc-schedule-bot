from __future__ import annotations

import pytest

from bot.config import ConfigError, load_config

BASE = {"BOT_TOKEN": "123456:TESTTOKEN", "CHAT_ID": "-1001234567890", "CLASS": "10Н"}


def _set_env(monkeypatch, **extra: str) -> None:
    for key in (*BASE, "THREAD_ID", "LYCREG_LOGIN", "LYCREG_PASSWORD", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**BASE, **extra}.items():
        monkeypatch.setenv(key, value)


def test_thread_id_absent_means_general_topic(monkeypatch):
    _set_env(monkeypatch)
    assert load_config().thread_id is None


def test_thread_id_parsed_as_int(monkeypatch):
    _set_env(monkeypatch, THREAD_ID="777")
    assert load_config().thread_id == 777


def test_thread_id_rejects_garbage(monkeypatch):
    _set_env(monkeypatch, THREAD_ID="не число")
    with pytest.raises(ConfigError, match="THREAD_ID"):
        load_config()


def test_lycreg_credentials_absent_means_homework_off(monkeypatch):
    _set_env(monkeypatch)
    config = load_config()
    assert config.lycreg_login == "" and config.lycreg_password == ""


def test_lycreg_credentials_loaded(monkeypatch):
    _set_env(monkeypatch, LYCREG_LOGIN="user42", LYCREG_PASSWORD="secret")
    config = load_config()
    assert config.lycreg_login == "user42"
    assert config.lycreg_password == "secret"


def test_openrouter_key_absent_means_ai_off(monkeypatch):
    _set_env(monkeypatch)
    assert load_config().openrouter_key == ""


def test_openrouter_key_loaded(monkeypatch):
    _set_env(monkeypatch, OPENROUTER_API_KEY="sk-or-v1-test")
    assert load_config().openrouter_key == "sk-or-v1-test"
