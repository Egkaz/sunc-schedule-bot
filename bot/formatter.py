"""Сборка текстов сообщений."""

from __future__ import annotations

from datetime import date
from html import escape

from bot.differ import NO_LESSON, canonical
from bot.days import DAY_SHORT, day_name, day_name_acc

GROUP_FIELDS = ("subject", "teacher", "room")
TEACHER_FIELDS = ("subject", "klass", "room")
AUDITORY_FIELDS = ("subject", "teacher", "klass")

FIELD_TITLES = {
    "subject": "предмет",
    "teacher": "преподаватель",
    "room": "аудитория",
    "klass": "класс",
}


def _line(entry: dict, fields: tuple[str, ...]) -> str:
    parts = [str(entry.get(name) or "").strip() for name in fields]
    body = ", ".join(part for part in parts if part)
    time_part = f" ({entry['time']})" if entry.get("time") else ""
    line = f"{entry.get('lesson', '?')} урок{time_part}: {body}"
    if entry.get("diff"):
        line += " — изменение"
    return line


def format_entries(entries: list[dict], fields: tuple[str, ...] = GROUP_FIELDS) -> list[str]:
    return [_line(entry, fields) for entry in sorted(entries, key=lambda e: int(e.get("lesson", 0)))]


def format_day(
    klass: str,
    api_wd: int,
    entries: list[dict],
    *,
    fields: tuple[str, ...] = GROUP_FIELDS,
) -> str:
    """«Расписание на Четверг 10Н» + список уроков; пустые дни -> «Занятий нет»."""
    header = f"Расписание на {day_name_acc(api_wd)} {klass}".strip()
    lines = format_entries(entries, fields)
    if not lines:
        lines = ["Занятий нет"]
    return "\n".join([header, *lines])


def format_changes(klass: str, api_wd: int, lines: list[str]) -> str:
    header = f"Изменения в расписании на {day_name_acc(api_wd)} {klass}".strip()
    return "\n".join([header, *lines])


CAPTION_LIMIT = 1024  # лимит подписи к медиа после разбора сущностей


def format_changes_caption(lines: list[str]) -> str:
    """Подпись к картинке с изменениями: HTML в стиле Telegram (blockquote, strike, bold).

    «4 урок: Нет -> АнглЯзык, …» превращается в
    ``4 урок: <s>Нет</s> → <b>АнглЯзык, …</b>``; убывшие уроки вычёркиваются.
    """
    header = "<b>Что изменилось</b>"
    overhead = len(header) + len("\n<blockquote></blockquote>")
    used = overhead
    items: list[str] = []
    for line in lines:
        if " -> " in line:
            left, right = line.split(" -> ", 1)
            head, was = left.split(": ", 1) if ": " in left else (left, "")
            if right.strip() == NO_LESSON:
                item = f"{escape(head)}: {escape(was)} → <s>{escape(right.strip())}</s>"
            else:
                item = f"{escape(head)}: <s>{escape(was)}</s> → <b>{escape(right.strip())}</b>"
        else:
            item = escape(line)
        if used + len(item) + 1 > CAPTION_LIMIT:
            items.append("…")
            break
        items.append(item)
        used += len(item) + 1
    if not items:
        return header
    return f"{header}\n<blockquote>{chr(10).join(items)}</blockquote>"


def format_changes_all(klass: str, changes: dict[int, list[str]]) -> str:
    parts = [format_changes(klass, day, changes[day]) for day in sorted(changes)]
    return "\n\n".join(parts)


def format_entity_day(entity: str, api_wd: int, entries: list[dict], fields: tuple[str, ...]) -> str:
    header = f"{entity}, {day_name(api_wd)}".strip()
    lines = format_entries(entries, fields)
    if not lines:
        lines = ["Занятий нет"]
    return "\n".join([header, *lines])


def format_status(
    *,
    klass: str,
    last_ok: str | None,
    fail_streak: int,
    snapshot_days: int,
    snapshot_updated: str | None,
    poll_minutes: int,
    daily_hour: int,
) -> str:
    return "\n".join(
        [
            "Статус бота",
            f"Класс: {klass}",
            f"Последний успешный опрос: {last_ok or 'ещё не было'}",
            f"Неудач подряд: {fail_streak}",
            f"Снимок: {snapshot_days} дн., обновлён: {snapshot_updated or '-'}",
            f"Опрос каждые {poll_minutes} мин, пост в {daily_hour:02d}:00",
        ]
    )


def format_usage(command: str, hint: str) -> str:
    return f"Неверный формат. Пример: {command} {hint}"


def format_homework(target: date, weekday: int, items: list[tuple[str, str]]) -> str:
    lines = [f"ДЗ на {day_name_acc(weekday)} {target:%d.%m}:"]
    for subject, hw in items:
        lines.append(f"• {subject} — {_hw_one_line(hw)}")
    return "\n".join(lines)


def _hw_one_line(hw: str) -> str:
    hw_one = " ".join(str(hw).split())
    if len(hw_one) > 300:
        hw_one = hw_one[:297] + "…"
    return hw_one


def format_homework_caption(target: date, weekday: int, items: list[tuple[str, str]]) -> str:
    """Подпись к фото расписания: ДЗ в стиле Telegram-HTML (blockquote), лимит 1024."""
    header = f"<b>ДЗ на {day_name_acc(weekday)} {target:%d.%m}</b>"
    overhead = len(header) + len("\n<blockquote></blockquote>")
    used = overhead
    lines: list[str] = []
    for subject, hw in items:
        item = f"{escape(subject)} — {escape(_hw_one_line(hw))}"
        if used + len(item) + 1 > CAPTION_LIMIT:
            lines.append("…")
            break
        lines.append(item)
        used += len(item) + 1
    if not lines:
        return header
    return f"{header}\n<blockquote>{chr(10).join(lines)}</blockquote>"


def format_help(klass: str, daily_hour: int, admin: bool = False) -> str:
    lines = [
        "Команды бота расписания:",
        "/сегодня — расписание на сегодня",
        "/завтра — расписание на завтра",
        "/день пн..сб — ближайший такой день",
        f"/класс {klass} [день] — расписание другого класса",
        "/препод фамилия [день] — расписание преподавателя",
        "/аудитория 304 [день] — расписание аудитории",
        "/zov текст — поболтать с Алиной (персонаж), /zov стоп — выйти",
        f"Изменения опрашиваются автоматически, пост на завтра — в {daily_hour:02d}:00.",
    ]
    if admin:
        lines += [
            "/проверить — принудительный опрос",
            "/статус — время последнего опроса",
            "/пост — прислать расписание на завтра в чат сейчас",
            "/дз [день] — домашнее задание из журнала (по умолчанию завтра)",
            "/тема — запомнить ветку форума для постов",
        ]
    return "\n".join(lines)


__all__ = [
    "AUDITORY_FIELDS",
    "FIELD_TITLES",
    "GROUP_FIELDS",
    "NO_LESSON",
    "TEACHER_FIELDS",
    "canonical",
    "DAY_SHORT",
    "format_changes",
    "format_changes_all",
    "format_changes_caption",
    "format_day",
    "format_entries",
    "format_entity_day",
    "format_help",
    "format_homework",
    "format_homework_caption",
    "format_status",
    "format_usage",
]
