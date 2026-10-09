"""Сравнение снимков расписания: что изменилось."""

from __future__ import annotations

NO_LESSON = "Нет"

Snapshot = dict[int, list[dict]]


def canonical(entry: dict) -> str:
    """«Предмет, Преподаватель, Аудитория» — как в сообщении об изменениях."""
    parts = [
        str(entry.get("subject") or "").strip(),
        str(entry.get("teacher") or "").strip(),
        str(entry.get("room") or "").strip(),
    ]
    return ", ".join(part for part in parts if part) or NO_LESSON


def slots(entries: list[dict]) -> dict[int, str]:
    return {int(entry["lesson"]): canonical(entry) for entry in entries if "lesson" in entry}


def coerce_snapshot(raw: dict | None) -> Snapshot:
    """JSON-снимок (ключи-строки) -> снимок с целочисленными днями."""
    if not raw:
        return {}
    result: Snapshot = {}
    for key, value in raw.items():
        try:
            day = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, list):
            result[day] = [entry for entry in value if isinstance(entry, dict)]
    return result


def diff_days(old: Snapshot, new: Snapshot) -> dict[int, list[str]]:
    """Изменения по дням: {1: ["3 урок: Химия, ... -> Нет"], ...}"""
    changes: dict[int, list[str]] = {}
    for day in sorted(set(old) | set(new)):
        before = slots(old.get(day, []))
        after = slots(new.get(day, []))
        lines: list[str] = []
        for number in sorted(set(before) | set(after)):
            was = before.get(number, NO_LESSON)
            now = after.get(number, NO_LESSON)
            if was != now:
                lines.append(f"{number} урок: {was} -> {now}")
        if lines:
            changes[day] = lines
    return changes


def snapshot_digest(snapshot: Snapshot) -> int:
    """Хэш снимка для быстрой проверки «изменилось ли вообще»."""
    return hash(repr(sorted((day, tuple(sorted(slots(entries).items()))) for day, entries in snapshot.items())))
