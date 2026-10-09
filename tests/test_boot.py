"""Смоук-тест: всё собирается и запускается (без сети и без Telegram)."""

from __future__ import annotations

from aiogram import Bot, Dispatcher

from bot.config import Config
from bot.fetcher import ScheduleClient
from bot.handlers import create_router
from bot.scheduler import build_scheduler
from bot.service import ScheduleService
from bot.storage import Storage


async def test_app_wires_up(tmp_path):
    config = Config(
        bot_token="123456:TESTTOKEN",
        chat_id="-1001234567890",
        klass="10Н",
        daily_hour=15,
        admin_id=42,
        db_path=tmp_path / "bot.db",
    )

    storage = Storage(config.db_path)
    await storage.open()

    client = ScheduleClient("https://example.invalid")
    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher()

    async def send_chat(text: str) -> None:  # pragma: no cover - заглушка
        raise AssertionError("отправлять не должны")

    service = ScheduleService(
        config,
        client,
        storage,
        send_chat=send_chat,
        notify_admin=None,
    )
    dispatcher.include_router(create_router(config, service))
    assert dispatcher.sub_routers, "роутер команд не подключён"

    scheduler = build_scheduler(service, config)
    scheduler.start()
    try:
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert {"poll", "daily"} <= job_ids
        assert scheduler.timezone.key == "Asia/Yekaterinburg"
    finally:
        scheduler.shutdown(wait=False)
        await client.aclose()
        await storage.close()
        await bot.session.close()


async def test_service_resolves_class_and_reports_missing(tmp_path):
    import time

    from bot.fetcher import Reference

    config = Config(
        bot_token="123456:TESTTOKEN",
        chat_id="-1001234567890",
        klass="10Н",
        db_path=tmp_path / "bot.db",
    )
    storage = Storage(config.db_path)
    await storage.open()
    client = ScheduleClient("https://example.invalid")
    client._reference = Reference(groups={"10Н": 22}, teachers={}, auditories={})
    client._reference_at = time.monotonic()

    async def send_chat(text: str) -> None:  # pragma: no cover
        raise AssertionError("отправлять не должны")

    service = ScheduleService(config, client, storage, send_chat=send_chat)
    try:
        assert await service.resolve_group("10Н") == 22
        try:
            await service.group_day("99Х", 1)
        except LookupError as exc:
            assert "не найден" in str(exc)
        else:
            raise AssertionError("ожидали LookupError для неизвестного класса")
    finally:
        await client.aclose()
        await storage.close()
