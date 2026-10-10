"""Конфигурация бота: .env + переменные окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Некорректная или неполная конфигурация."""


def load_dotenv(path: str | Path = ".env") -> None:
    """Минимальный читатель .env.

    Существующие переменные окружения имеют приоритет над файлом.
    """
    file = Path(path)
    if not file.is_file():
        return
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    klass: str
    tz: str = "Asia/Yekaterinburg"
    poll_minutes: int = 10
    daily_hour: int = 15
    admin_id: int | None = None
    thread_id: int | None = None
    lycreg_login: str = ""
    lycreg_password: str = ""
    openrouter_key: str = ""
    db_path: Path = Path("data/bot.db")
    horizon_days: int = 7
    base_url: str = "https://lyceum.urfu.ru"
    log_level: str = "INFO"


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} должен быть целым числом, получено: {raw!r}") from exc


def load_config() -> Config:
    missing = [name for name in ("BOT_TOKEN", "CHAT_ID", "CLASS") if not os.environ.get(name, "").strip()]
    if missing:
        raise ConfigError("Не заданы переменные: " + ", ".join(missing))

    admin_raw = os.environ.get("ADMIN_ID", "").strip()
    admin_id: int | None
    if admin_raw:
        try:
            admin_id = int(admin_raw)
        except ValueError as exc:
            raise ConfigError(f"ADMIN_ID должен быть целым числом, получено: {admin_raw!r}") from exc
    else:
        admin_id = None

    thread_raw = os.environ.get("THREAD_ID", "").strip()
    thread_id: int | None
    if thread_raw:
        try:
            thread_id = int(thread_raw)
        except ValueError as exc:
            raise ConfigError(f"THREAD_ID должен быть целым числом, получено: {thread_raw!r}") from exc
    else:
        thread_id = None

    poll_minutes = _get_int("POLL_MINUTES", 10)
    if poll_minutes < 1:
        raise ConfigError("POLL_MINUTES должен быть >= 1")
    daily_hour = _get_int("DAILY_HOUR", 15)
    if not 0 <= daily_hour <= 23:
        raise ConfigError("DAILY_HOUR должен быть в диапазоне 0..23")

    horizon = _get_int("HORIZON_DAYS", 7)
    if horizon < 7:
        horizon = 7

    return Config(
        bot_token=os.environ["BOT_TOKEN"].strip(),
        chat_id=os.environ["CHAT_ID"].strip(),
        klass=os.environ["CLASS"].strip().upper(),
        tz=os.environ.get("TZ", "Asia/Yekaterinburg").strip() or "Asia/Yekaterinburg",
        poll_minutes=poll_minutes,
        daily_hour=daily_hour,
        admin_id=admin_id,
        thread_id=thread_id,
        lycreg_login=os.environ.get("LYCREG_LOGIN", "").strip(),
        lycreg_password=os.environ.get("LYCREG_PASSWORD", "").strip(),
        openrouter_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        db_path=Path(os.environ.get("DB_PATH", "data/bot.db").strip() or "data/bot.db"),
        horizon_days=horizon,
        base_url=(os.environ.get("BASE_URL", "").strip() or "https://lyceum.urfu.ru").rstrip("/"),
        log_level=(os.environ.get("LOG_LEVEL", "").strip() or "INFO").upper(),
    )
