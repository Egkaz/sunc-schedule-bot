"""Бизнес-логика: опрос расписания, публикация, запросы по командам."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from bot import days as days_mod
from bot.config import Config
from bot.differ import Snapshot, diff_days
from bot.fetcher import FetchError, ScheduleClient
from bot.formatter import (
    AUDITORY_FIELDS,
    TEACHER_FIELDS,
    format_changes,
    format_changes_caption,
    format_day,
    format_entity_day,
    format_homework,
    format_homework_caption,
    format_status,
)
from bot.lycreg import CaptchaRequired, LycregClient, LycregError
from bot.normalize import normalize_day
from bot.render import ScheduleCard, changes_card, group_card
from bot.storage import KEY_FAIL_STREAK, KEY_LAST_OK, KEY_SNAPSHOT_AT, KEY_THREAD_ID, Storage

log = logging.getLogger(__name__)

SendChat = Callable[[str], Awaitable[None]]
SendPhoto = Callable[[ScheduleCard], Awaitable[int | None]]
NotifyAdmin = Callable[[str], Awaitable[None]]
AdminPhoto = Callable[[bytes, str], Awaitable[None]]

CAPTCHA_TIMEOUT = 600  # секунд на ответ администратора с кодом капчи


async def resolve_thread_id(storage: Storage, config: Config) -> int | None:
    """Ветка форума: из БД (команда /тема), иначе из THREAD_ID, иначе «Общая»."""
    stored = await storage.get_optional_int(KEY_THREAD_ID)
    if stored is not None:
        return stored
    return config.thread_id


def _is_suspiciously_empty(old: Snapshot, new: Snapshot) -> bool:
    """Старый снимок содержал уроки, новый пуст целиком -> это сбой сайта, а не каникулы."""
    return sum(len(entries) for entries in old.values()) > 0 and sum(
        len(entries) for entries in new.values()
    ) == 0


@dataclass
class PollResult:
    ok: bool
    first_run: bool = False
    changes: dict[int, list[str]] = field(default_factory=dict)
    error: str | None = None
    checked_at: datetime | None = None
    days: int = 0
    fail_streak: int = 0


class ScheduleService:
    def __init__(
        self,
        config: Config,
        client: ScheduleClient,
        storage: Storage,
        *,
        send_chat: SendChat,
        notify_admin: NotifyAdmin | None = None,
        send_photo: SendPhoto | None = None,
        admin_photo: AdminPhoto | None = None,
        lycreg: LycregClient | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.storage = storage
        self.send_chat = send_chat
        self.notify_admin = notify_admin
        self.send_photo = send_photo
        self.admin_photo = admin_photo
        self.lycreg = lycreg
        self._captcha: asyncio.Future[str] | None = None
        try:
            self.tz = ZoneInfo(config.tz)
        except Exception:  # noqa: BLE001 - tzdata может отсутствовать
            log.warning("не удалось загрузить часовой пояс %s, беру UTC", config.tz)
            self.tz = ZoneInfo("UTC")
        self._poll_lock = asyncio.Lock()

    def now(self) -> datetime:
        return datetime.now(self.tz)

    async def get_thread_id(self) -> int | None:
        return await resolve_thread_id(self.storage, self.config)

    async def set_thread_id(self, thread_id: int) -> None:
        await self.storage.set_int(KEY_THREAD_ID, thread_id)

    async def _safe_send(self, text: str) -> bool:
        try:
            await self.send_chat(text)
            return True
        except Exception:  # noqa: BLE001 - не роняем опрос из-за Telegram
            log.exception("не удалось отправить сообщение в чат")
            return False

    async def _safe_send_photo(self, card: ScheduleCard) -> tuple[bool, int | None]:
        """Отправка карточки-таблицы: (успех, id сообщения для ответа)."""
        try:
            message_id = await self.send_photo(card)  # type: ignore[misc]
            return True, message_id
        except Exception:  # noqa: BLE001 - не роняем публикацию из-за Telegram
            log.exception("не удалось отправить карточку расписания в чат")
            return False, None

    async def _safe_admin(self, text: str) -> None:
        if self.notify_admin is None:
            return
        try:
            await self.notify_admin(text)
        except Exception:  # noqa: BLE001
            log.exception("не удалось написать администратору")

    async def _handle_failure(self, error: Exception) -> int:
        streak = await self.storage.get_int(KEY_FAIL_STREAK, 0) + 1
        await self.storage.set_int(KEY_FAIL_STREAK, streak)
        log.error("опрос расписания не удался (подряд: %d): %s", streak, error)
        if streak == 3:
            await self._safe_admin(
                f"Опрос расписания провалился 3 раза подряд: {error}. "
                "Дальше сообщать не буду, проверю сам после восстановления."
            )
        return streak

    @property
    def captcha_pending(self) -> bool:
        return self._captcha is not None and not self._captcha.done()

    async def submit_captcha(self, code: str) -> None:
        if self._captcha is not None and not self._captcha.done():
            self._captcha.set_result(code.strip())

    async def _login_with_captcha(self, exc: CaptchaRequired) -> bool:
        """Капча админу в личку → ждём код → вход. True — вход выполнен."""
        if self.lycreg is None or self.captcha_pending or self.admin_photo is None:
            return False
        self._captcha = asyncio.get_running_loop().create_future()
        try:
            await self.admin_photo(
                exc.image,
                "Нужен вход в журнал lycreg (капча) — ответь на это сообщение кодом с картинки.",
            )
            code = await asyncio.wait_for(self._captcha, CAPTCHA_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("журнал: код с капчей не прислали за %d мин", CAPTCHA_TIMEOUT // 60)
            return False
        except Exception:  # noqa: BLE001
            log.exception("журнал: не удалось выполнить вход по капче")
            return False
        finally:
            self._captcha = None
        try:
            await self.lycreg.login(exc.ci, code)
        except LycregError as error:
            log.warning("журнал: вход не удался: %s", error)
            return False
        return True

    async def homework_items(
        self, klass: str, lessons: list[dict], target: date
    ) -> list[tuple[str, str]] | None:
        """ДЗ под уроки дня target; None — журнал недоступен."""
        if self.lycreg is None:
            return None
        for attempt in (1, 2):
            try:
                return await self.lycreg.homework(klass, lessons, target)
            except CaptchaRequired as exc:
                if attempt > 1 or not await self._login_with_captcha(exc):
                    return None
            except LycregError as error:
                log.warning("журнал lycreg: %s", error)
                return None
        return None

    async def poll(self) -> PollResult:
        """Опрос горизонта дней для CLASS.

        Первый запуск (снимка ещё нет) — только сохранить, ничего не слать.
        """
        async with self._poll_lock:
            old = await self.storage.get_snapshot()
            try:
                reference = await self.client.reference()
                group_id = reference.group_id(self.config.klass)
                if group_id is None:
                    raise FetchError(f"класс {self.config.klass} не найден в справочнике сайта")

                now = self.now()
                new: Snapshot = {}
                for day in days_mod.upcoming_dates(now.date(), self.config.horizon_days):
                    weekday = days_mod.api_weekday(day)
                    lessons, diffs = await self.client.fetch_group_day(group_id, weekday)
                    new[weekday] = normalize_day(lessons, diffs)
            except FetchError as exc:
                streak = await self._handle_failure(exc)
                return PollResult(ok=False, error=str(exc), checked_at=self.now(), fail_streak=streak)

            if old is not None and _is_suspiciously_empty(old, new):
                error = "сайт вернул пустое расписание на все дни — считаю сбоем"
                streak = await self._handle_failure(RuntimeError(error))
                return PollResult(ok=False, error=error, checked_at=now, fail_streak=streak)

            await self.storage.set_int(KEY_FAIL_STREAK, 0)
            await self.storage.set(KEY_LAST_OK, now.isoformat(timespec="seconds"))

            merged: Snapshot = dict(old or {})
            merged.update(new)
            await self.storage.save_snapshot(merged, at=now)

            if old is None:
                log.info("первый снимок сохранён (%d дн.), уведомления не отправляются", len(new))
                return PollResult(ok=True, first_run=True, days=len(new), checked_at=now)

            changes = diff_days(old, merged)
            if not changes:
                log.info("изменений нет (%d дн.)", len(new))
                return PollResult(ok=True, days=len(new), checked_at=now)

            log.info("обнаружены изменения: %s", {day: len(lines) for day, lines in changes.items()})
            for day in sorted(changes):
                await self._notify_changes(day, changes[day], merged.get(day, []))
            return PollResult(ok=True, changes=changes, days=len(new), checked_at=now)

    def _changed_numbers(self, lines: list[str]) -> set[int]:
        numbers: set[int] = set()
        for line in lines:
            try:
                numbers.add(int(line.split(" ", 1)[0]))
            except (ValueError, IndexError):
                continue
        return numbers

    async def _notify_changes(self, day: int, lines: list[str], entries: list[dict]) -> None:
        """Изменения: таблица дня с оранжевыми строками + HTML-подпись; иначе текст."""
        text = format_changes(self.config.klass, day, lines)
        if self.send_photo is None:
            await self._safe_send(text)
            return
        card = changes_card(
            self.config.klass,
            day,
            entries,
            self._changed_numbers(lines),
            caption=format_changes_caption(lines),
            text=text,
        )
        delivered, _ = await self._safe_send_photo(card)
        if not delivered:
            await self._safe_send(text)

    async def publish_tomorrow(self) -> str | None:
        """Ежедневный пост расписания на завтра (вс–пт); ДЗ из журнала — в подписи к фото."""
        now = self.now()
        tomorrow = now.date() + timedelta(days=1)
        weekday = days_mod.api_weekday(tomorrow)
        if weekday == days_mod.SUNDAY:
            return None
        try:
            entries = await self.group_day(self.config.klass, weekday)
        except FetchError as exc:
            log.error("не удалось получить расписание на завтра: %s", exc)
            await self._safe_admin(f"Не смог отправить расписание на завтра ({days_mod.day_name(weekday)}): {exc}")
            return None
        text = format_day(self.config.klass, weekday, entries)
        hw_items = await self.homework_items(self.config.klass, entries, tomorrow)
        caption = ""
        if hw_items:
            caption = format_homework_caption(tomorrow, weekday, hw_items)
            text = f"{text}\n\n{format_homework(tomorrow, weekday, hw_items)}"
        card = group_card(self.config.klass, weekday, entries, caption=caption)
        if self.send_photo is not None:
            delivered, _ = await self._safe_send_photo(card)
        else:
            delivered = await self._safe_send(text)
        if delivered:
            log.info(
                "расписание на завтра опубликовано (%s%s)",
                days_mod.day_name(weekday),
                ", ДЗ в подписи" if hw_items else "",
            )
            return text
        return None

    async def reference(self):
        return await self.client.reference()

    async def group_day(self, klass: str, weekday: int) -> list[dict]:
        reference = await self.client.reference()
        group_id = reference.group_id(klass)
        if group_id is None:
            raise LookupError(f"класс {klass.strip()} не найден")
        lessons, diffs = await self.client.fetch_group_day(group_id, weekday)
        return normalize_day(lessons, diffs)

    async def resolve_group(self, klass: str) -> int | None:
        return (await self.client.reference()).group_id(klass)

    async def teacher_matches(self, query: str) -> list[tuple[str, int]]:
        return (await self.client.reference()).teacher_matches(query)

    async def teacher_day(self, teacher_id: int, weekday: int) -> list[dict]:
        lessons, diffs = await self.client.fetch_teacher_day(teacher_id, weekday)
        return normalize_day(lessons, diffs)

    async def resolve_auditory(self, room: str) -> int | None:
        return (await self.client.reference()).auditory_id(room)

    async def auditory_day(self, auditory_id: int, weekday: int) -> list[dict]:
        lessons, diffs = await self.client.fetch_auditory_day(auditory_id, weekday)
        return normalize_day(lessons, diffs)

    async def entity_day_text(
        self,
        entity: str,
        weekday: int,
        entries: list[dict],
        fields: tuple[str, ...],
    ) -> str:
        return format_entity_day(entity, weekday, entries, fields)

    async def status_text(self) -> str:
        snapshot = await self.storage.get_snapshot()
        return format_status(
            klass=self.config.klass,
            last_ok=await self.storage.get(KEY_LAST_OK),
            fail_streak=await self.storage.get_int(KEY_FAIL_STREAK, 0),
            snapshot_days=len(snapshot or {}),
            snapshot_updated=await self.storage.get(KEY_SNAPSHOT_AT),
            poll_minutes=self.config.poll_minutes,
            daily_hour=self.config.daily_hour,
        )

    async def check_now(self) -> PollResult:
        return await self.poll()


__all__ = ["PollResult", "ScheduleService", "TEACHER_FIELDS", "AUDITORY_FIELDS", "resolve_thread_id"]
