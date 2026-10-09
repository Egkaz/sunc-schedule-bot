"""Дни недели и звонки СУНЦ.

API расписания нумерует дни 1..6 (Пн..Сб), 0 и 7 — «пусто» (в т.ч. воскресенье).
Номера уроков 1..7, времена даны по расписанию звонков.
"""

from __future__ import annotations

from datetime import date, timedelta

LESSON_TIMES: tuple[str, ...] = (
    "9:00-9:40",
    "9:50-10:30",
    "10:45-11:25",
    "11:40-12:20",
    "12:35-13:15",
    "13:35-14:15",
    "14:35-15:15",
)

DAY_NAMES: tuple[str, ...] = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)

# Винительный падеж — для заголовков «Расписание на …», «Изменения в расписании на …»
DAY_ACCUSATIVE: tuple[str, ...] = (
    "Понедельник",
    "Вторник",
    "Среду",
    "Четверг",
    "Пятницу",
    "Субботу",
    "Воскресенье",
)

DAY_SHORT: tuple[str, ...] = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

SUNDAY = 7


def api_weekday(day: date) -> int:
    """date.weekday() (Пн=0..Вс=6) -> номер дня в API (Пн=1..Вс=7)."""
    return day.weekday() + 1


def day_name(api_wd: int) -> str:
    if 1 <= api_wd <= 7:
        return DAY_NAMES[api_wd - 1]
    return "Неизвестный день"


def day_name_acc(api_wd: int) -> str:
    """Имя дня для предлога «на»: «на Пятницу», «на Среду»."""
    if 1 <= api_wd <= 7:
        return DAY_ACCUSATIVE[api_wd - 1]
    return "Неизвестный день"


def lesson_time(number: int) -> str:
    if 1 <= number <= len(LESSON_TIMES):
        return LESSON_TIMES[number - 1]
    return ""


def parse_day(value: str) -> int | None:
    """«пн», «Пт», «среда», «Четверг» -> номер дня в API (1..6).

    Воскресенье (7) не принимается: занятий нет, опрос его пропускает.
    """
    raw = (value or "").strip().lower().replace("ё", "е")
    if not raw:
        return None
    for idx, short in enumerate(DAY_SHORT):
        if raw == short:
            return idx + 1 if idx + 1 != SUNDAY else None
    for idx, name in enumerate(DAY_NAMES):
        if raw == name.lower().replace("ё", "е"):
            return idx + 1 if idx + 1 != SUNDAY else None
    return None


def upcoming_dates(today: date, span: int = 7) -> list[date]:
    """Даты today..today+span-1 без воскресеньй.

    При span=7 попадает ровно по одному каждому буднему дню (Пн..Сб).
    """
    result: list[date] = []
    for offset in range(max(1, span)):
        day = today + timedelta(days=offset)
        if day.weekday() == 6:
            continue
        result.append(day)
    return result


def next_weekday(today: date, target_api_wd: int) -> date:
    """Ближайшая дата с нужным днём недели (сегодня, если он совпал)."""
    delta = (target_api_wd - 1) - today.weekday()
    if delta < 0:
        delta += 7
    return today + timedelta(days=delta)
