"""Команды бота (работают и в чате класса, и в личке)."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import BufferedInputFile, Message
from aiogram.exceptions import AiogramError

from bot import days as days_mod
from bot.config import Config
from bot.fetcher import FetchError
from bot.formatter import format_changes_all, format_help, format_homework, format_usage
from bot.render import (
    SITE_AUDITORY_FIELDS,
    SITE_TEACHER_FIELDS,
    ScheduleCard,
    entity_card,
    group_card,
    schedule_photo,
)
from bot.service import ScheduleService

log = logging.getLogger(__name__)


def _split_args(args: str | None) -> list[str]:
    return (args or "").split()


def _parse_trailing_day(parts: list[str]) -> tuple[str, int | None]:
    """Последний токен может быть днём недели: «Бондарь пн» -> («Бондарь», 1)."""
    if len(parts) >= 2:
        day = days_mod.parse_day(parts[-1])
        if day is not None:
            return " ".join(parts[:-1]), day
    return " ".join(parts), None


def _default_weekday(service: ScheduleService, weekday: int | None = None) -> tuple[int, str]:
    if weekday is not None:
        return weekday, ""
    today = service.now().date()
    current = days_mod.api_weekday(today)
    if current == days_mod.SUNDAY:
        return days_mod.api_weekday(today + timedelta(days=1)), " (ближайший будний день)"
    return current, ""


def _target_date(service: ScheduleService, weekday: int | None = None) -> date:
    """Дата для /дз: по умолчанию завтра, иначе ближайший такой день."""
    today = service.now().date()
    if weekday is None:
        return today + timedelta(days=1)
    return days_mod.next_weekday(today, weekday)


class CaptchaCodeFilter(BaseFilter):
    """Цифровой ответ администратора, пока бот ждёт код с картинки."""

    def __init__(self, service: ScheduleService) -> None:
        self.service = service

    async def __call__(self, message: Message) -> bool:
        if not self.service.captcha_pending:
            return False
        return bool((message.text or "").strip().isdigit())


def create_router(config: Config, service: ScheduleService) -> Router:
    router = Router(name="commands")

    async def answer(message: Message, text: str) -> None:
        try:
            await message.answer(text)  # aiogram сам укажет ветку (is_topic_message)
        except AiogramError:
            log.exception("не удалось ответить на %s", message.text)

    async def answer_schedule(message: Message, card: ScheduleCard) -> None:
        """Расписание отправляем таблицей-картинкой; при сбое отрисовки — текстом."""
        png = schedule_photo(card)
        if png is None:
            await answer(message, card.text or card.title)
            return
        try:
            await message.answer_photo(
                BufferedInputFile(png, filename="raspisanie.png"),
                caption=card.caption or None,
                parse_mode=ParseMode.HTML if card.caption else None,
            )
        except AiogramError:
            log.exception("не удалось отправить карточку, отправляю текстом")
            await answer(message, card.text or card.title)

    @asynccontextmanager
    async def guard(message: Message):
        try:
            yield
        except LookupError as exc:
            await answer(message, str(exc))
        except FetchError as exc:
            log.warning("сайт расписания недоступен: %s", exc)
            await answer(message, "Сайт расписания сейчас недоступен, попробуйте позже.")
        except Exception:  # noqa: BLE001
            log.exception("ошибка обработки команды %s", message.text)
            await answer(message, "Внутренняя ошибка, подробности в логах бота.")

    async def is_admin(message: Message) -> bool:
        return config.admin_id is not None and message.from_user is not None and message.from_user.id == config.admin_id

    @router.message(Command("start", "help", "помощь"))
    async def cmd_help(message: Message) -> None:
        await answer(message, format_help(config.klass, config.daily_hour, admin=await is_admin(message)))

    @router.message(Command("сегодня"))
    async def cmd_today(message: Message) -> None:
        async with guard(message):
            weekday, _ = _default_weekday(service, days_mod.api_weekday(service.now().date()))
            entries = await service.group_day(config.klass, weekday)
            await answer_schedule(message, group_card(config.klass, weekday, entries))

    @router.message(Command("завтра"))
    async def cmd_tomorrow(message: Message) -> None:
        async with guard(message):
            weekday = days_mod.api_weekday(service.now().date() + timedelta(days=1))
            entries = await service.group_day(config.klass, weekday)
            await answer_schedule(message, group_card(config.klass, weekday, entries))

    @router.message(Command("день"))
    async def cmd_day(message: Message, command: CommandObject) -> None:
        async with guard(message):
            parts = _split_args(command.args)
            if len(parts) != 1:
                await answer(message, format_usage("/день", "пн..сб"))
                return
            weekday = days_mod.parse_day(parts[0])
            if weekday is None:
                await answer(message, format_usage("/день", "пн..сб"))
                return
            entries = await service.group_day(config.klass, weekday)
            await answer_schedule(message, group_card(config.klass, weekday, entries))

    @router.message(Command("класс"))
    async def cmd_class(message: Message, command: CommandObject) -> None:
        async with guard(message):
            parts = _split_args(command.args)
            if not parts:
                await answer(message, format_usage("/класс", "10Б [пн..сб]"))
                return
            klass = parts[0]
            weekday, note = _default_weekday(service, days_mod.parse_day(parts[1]) if len(parts) > 1 else None)
            entries = await service.group_day(klass, weekday)
            await answer_schedule(message, group_card(klass, weekday, entries, note=note))

    @router.message(Command("препод"))
    async def cmd_teacher(message: Message, command: CommandObject) -> None:
        async with guard(message):
            parts = _split_args(command.args)
            if not parts:
                await answer(message, format_usage("/препод", "Бондарь [пн..сб]"))
                return
            query, explicit_day = _parse_trailing_day(parts)
            weekday, note = _default_weekday(service, explicit_day)
            matches = await service.teacher_matches(query)
            if not matches:
                await answer(message, f"Преподавателя «{query}» не нашёл.")
                return
            if len(matches) > 1:
                listing = "\n".join(f"• {label}" for label, _ in matches)
                await answer(message, f"Нашлось несколько преподавателей:\n{listing}\n\nУточните: /препод {matches[0][0]}")
                return
            label, teacher_id = matches[0]
            entries = await service.teacher_day(teacher_id, weekday)
            await answer_schedule(message, entity_card(label, weekday, entries, SITE_TEACHER_FIELDS, note=note))

    @router.message(Command("аудитория"))
    async def cmd_room(message: Message, command: CommandObject) -> None:
        async with guard(message):
            parts = _split_args(command.args)
            if not parts:
                await answer(message, format_usage("/аудитория", "304 [пн..сб]"))
                return
            room = parts[0]
            weekday, note = _default_weekday(service, days_mod.parse_day(parts[1]) if len(parts) > 1 else None)
            auditory_id = await service.resolve_auditory(room)
            if auditory_id is None:
                await answer(message, f"Аудиторию «{room}» не нашёл.")
                return
            entries = await service.auditory_day(auditory_id, weekday)
            await answer_schedule(
                message,
                entity_card(f"Аудитория {room}", weekday, entries, SITE_AUDITORY_FIELDS, note=note),
            )

    @router.message(Command("проверить"), is_admin)
    async def cmd_check(message: Message) -> None:
        async with guard(message):
            result = await service.check_now()
            if not result.ok:
                await answer(message, f"Опрос не удался: {result.error}")
            elif result.first_run:
                await answer(message, "Первый снимок расписания сохранён, уведомления не отправляю.")
            elif result.changes:
                await answer(message, format_changes_all(config.klass, result.changes))
            else:
                stamp = result.checked_at.strftime("%H:%M") if result.checked_at else ""
                await answer(message, f"Изменений нет (проверено {result.days} дн. в {stamp}).")

    @router.message(Command("статус"), is_admin)
    async def cmd_status(message: Message) -> None:
        async with guard(message):
            await answer(message, await service.status_text())

    @router.message(Command("пост"), is_admin)
    async def cmd_post(message: Message) -> None:
        async with guard(message):
            text = await service.publish_tomorrow()
            if text is None:
                await answer(message, "Расписание не отправлено: завтра воскресенье либо сайт недоступен (подробности в логе).")
            else:
                await answer(message, "Отправил расписание на завтра в чат.")

    @router.message(Command("дз"), is_admin)
    async def cmd_homework(message: Message, command: CommandObject) -> None:
        """Домашнее задание из lycreg на день (по умолчанию — завтра)."""
        async with guard(message):
            parts = _split_args(command.args)
            weekday = None
            if parts:
                weekday = days_mod.parse_day(parts[0])
                if weekday is None:
                    await answer(message, format_usage("/дз", "пн..сб"))
                    return
            target = _target_date(service, weekday)
            day_key = days_mod.api_weekday(target)
            entries = await service.group_day(config.klass, day_key)
            items = await service.homework_items(config.klass, entries, target)
            if items is None:
                await answer(
                    message,
                    "Журнал недоступен: либо жду код с капчей в личке, либо ошибка (подробности в логе).",
                )
            elif not items:
                await answer(
                    message,
                    f"ДЗ на {days_mod.day_name_acc(day_key)} {target:%d.%m} в журнале не найдено.",
                )
            else:
                await answer(message, format_homework(target, day_key, items))

    @router.message(Command("тема"), is_admin)
    async def cmd_topic(message: Message) -> None:
        """Запомнить ветку форума, куда слать посты и уведомления."""
        thread = message.message_thread_id
        if thread is None:
            await answer(message, "Напишите /тема внутри нужной ветки форума — посты пойдут именно туда.")
            return
        await service.set_thread_id(thread)
        await answer(message, f"Принято: посты и уведомления теперь в этой ветке (id {thread}).")

    @router.message(is_admin, CaptchaCodeFilter(service))
    async def captcha_reply(message: Message) -> None:
        """Ответ администратора цифрами — код капчи входа в журнал."""
        await service.submit_captcha((message.text or "").strip())
        await answer(message, "Код принят, вхожу в журнал…")

    bot_username: str | None = None

    async def _resolve_username(message: Message) -> str | None:
        nonlocal bot_username
        if bot_username is None:
            me = await message.bot.get_me()
            bot_username = me.username
        return bot_username

    trigger = config.ai_trigger.casefold()
    _bg_tasks: set[asyncio.Task] = set()

    def _spawn(coro) -> None:
        """Фоновая задача ИИ: держим ссылку, чтобы GC не убил её на середине."""
        task = asyncio.create_task(coro)
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    @router.message(~F.from_user.is_bot, F.text | F.caption)
    async def ai_chat_message(message: Message) -> None:
        """Сообщения чата: ИИ собирает ДЗ в БД и отвечает на вопросы (в фоне).

        Бот считает сообщение своим, если в нём есть слово-триггер (AI_TRIGGER,
        по умолчанию «мяу»), упоминание @бота или ответ на его сообщение.
        """
        if service.ai is None:
            return
        raw = (message.text or message.caption or "").strip()
        if not raw or raw.startswith("/"):
            return

        async def work() -> None:
            try:
                text = raw
                addressed = False
                if trigger and trigger in raw.casefold():
                    addressed = True
                    # убираем сам триггер, чтобы модель отвечала по существу
                    text = re.sub(re.escape(config.ai_trigger), "", raw, flags=re.IGNORECASE).strip(" ,!?.…-–") or raw
                username = await _resolve_username(message)
                addressed = addressed or (bool(username) and f"@{username}".lower() in raw.lower())
                reply_to = message.reply_to_message
                if reply_to is not None and reply_to.from_user is not None:
                    me = await message.bot.get_me()
                    addressed = addressed or reply_to.from_user.id == me.id
                reply = await service.handle_chat_message(
                    text=text, msg_id=message.message_id, addressed=addressed
                )
                if reply:
                    await message.answer(reply)
            except Exception:  # noqa: BLE001 - фоновая работа ИИ не должна ронять поллинг
                log.exception("ИИ: сбой обработки сообщения чата")

        _spawn(work())

    return router


__all__ = ["create_router"]
