"""Точка входа: конфиг, БД, клиент, планировщик, graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import AiogramError, TelegramUnauthorizedError
from aiogram.types import BufferedInputFile

from bot.config import Config, ConfigError, load_config, load_dotenv
from bot.fetcher import ScheduleClient
from bot.handlers import create_router
from bot.lycreg import LycregClient
from bot.render import ScheduleCard, schedule_photo
from bot.scheduler import build_scheduler
from bot.service import ScheduleService, resolve_thread_id
from bot.storage import Storage

log = logging.getLogger("bot")

POLL_RETRY_DELAY = 30


def setup_logging(level: str = "INFO") -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # noqa: PTH123 - лог в файле должен быть UTF-8
        except (AttributeError, OSError, ValueError):
            pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def run(config: Config) -> int:
    storage = Storage(config.db_path)
    await storage.open()

    client = ScheduleClient(config.base_url)
    bot = Bot(token=config.bot_token)
    dispatcher = Dispatcher()

    async def send_chat(text: str) -> None:
        await bot.send_message(
            config.chat_id,
            text,
            message_thread_id=await resolve_thread_id(storage, config),
        )

    async def send_photo_chat(card: ScheduleCard) -> int | None:
        thread = await resolve_thread_id(storage, config)
        png = schedule_photo(card)
        if png is None:
            message = await bot.send_message(
                config.chat_id,
                card.text or card.title,
                message_thread_id=thread,
            )
            return message.message_id
        message = await bot.send_photo(
            config.chat_id,
            BufferedInputFile(png, filename="raspisanie.png"),
            caption=card.caption or None,
            parse_mode=ParseMode.HTML if card.caption else None,
            message_thread_id=thread,
        )
        return message.message_id

    async def send_admin_photo(png: bytes, caption: str) -> None:
        if config.admin_id is None:
            log.warning("ADMIN_ID не задан, капчу не отправить: %s", caption)
            return
        await bot.send_photo(
            config.admin_id,
            BufferedInputFile(png, filename="captcha.png"),
            caption=caption,
        )

    async def notify_admin(text: str) -> None:
        if config.admin_id is None:
            log.warning("ADMIN_ID не задан, сообщение не отправлено: %s", text)
            return
        await bot.send_message(config.admin_id, text)

    lycreg = None
    if config.lycreg_login and config.lycreg_password:
        lycreg = LycregClient(config.lycreg_login, config.lycreg_password, storage=storage)

    service = ScheduleService(
        config,
        client,
        storage,
        send_chat=send_chat,
        notify_admin=notify_admin,
        send_photo=send_photo_chat,
        admin_photo=send_admin_photo,
        lycreg=lycreg,
    )
    dispatcher.include_router(create_router(config, service))

    scheduler = build_scheduler(service, config)
    scheduler.start()
    log.info(
        "планировщик запущен: опрос каждые %d мин, пост в %02d:00 (%s), класс %s, чат %s",
        config.poll_minutes,
        config.daily_hour,
        config.tz,
        config.klass,
        config.chat_id,
    )

    try:
        await service.poll()
    except Exception:  # noqa: BLE001 - первый опрос не должен мешать старту
        log.exception("первый опрос завершился ошибкой")

    code = 0
    try:
        code = await _poll_forever(dispatcher, bot)
    finally:
        log.info("остановка…")
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001
            log.exception("ошибка остановки планировщика")
        await client.aclose()
        if lycreg is not None:
            await lycreg.aclose()
        await storage.close()
        await bot.session.close()
    return code


async def _poll_forever(dispatcher: Dispatcher, bot: Bot) -> int:
    """Поллинг с повторами: Telegram может быть недоступен на старте.

    Неверный токен — выход сразу, сетевые ошибки — пауза и новая попытка.
    """
    attempt = 0
    while True:
        try:
            await dispatcher.start_polling(bot)
            return 0
        except TelegramUnauthorizedError:
            log.error("бот не авторизован в Telegram: проверь BOT_TOKEN")
            return 1
        except AiogramError as exc:
            attempt += 1
            delay = min(POLL_RETRY_DELAY * attempt, 300)
            log.error(
                "поллинг Telegram не удался (%s: %s), повтор через %d с (попытка %d)",
                type(exc).__name__,
                exc,
                delay,
                attempt,
            )
            await asyncio.sleep(delay)


def main() -> int:
    load_dotenv()
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    setup_logging(config.log_level)
    log.info("старт бота: класс=%s, часовой пояс=%s", config.klass, config.tz)
    try:
        return asyncio.run(run(config))
    except KeyboardInterrupt:
        log.info("прервано пользователем")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
