"""Нормализация ответа API в структуру бота.

Внутренний вид дня: список записей

    {"lesson": 1, "time": "9:00-9:40", "subject": "...", "teacher": "...",
     "room": "...", "klass": "10Н", "diff": False}

Правила разбора (по поведению custom.js на сайте):

* `lessons` — базовое расписание, `diffs` — изменения, которые сайт рисует поверх
  базы красным. Элемент `diffs` с `subgroup=0` перекрывает весь номер целиком,
  с `subgroup=1/2` — только свою подгруппу.
* Запись с предметом «Нет» означает «занятий нет» и в расписание не попадает.
* Параллельные подгруппы склеиваются в одну запись урока через «/».
"""

from __future__ import annotations

from bot.days import LESSON_TIMES, lesson_time

MERGE_FIELDS = ("subject", "teacher", "room", "klass")

EMPTY_MARKERS = {"нет", "-", "—"}


def _number(raw: dict) -> int | None:
    try:
        return int(raw.get("number"))
    except (TypeError, ValueError):
        return None


def _subgroup(raw: dict) -> int:
    try:
        return int(raw.get("subgroup") or 0)
    except (TypeError, ValueError):
        return 0


def _make_entry(raw: dict, *, is_diff: bool) -> dict:
    return {
        "subject": str(raw.get("subject") or "").strip(),
        "teacher": str(raw.get("teacher") or "").strip(),
        "room": str(raw.get("auditory") or "").strip(),
        "klass": str(raw.get("group") or "").strip(),
        "diff": bool(is_diff),
    }


def _is_empty(entry: dict) -> bool:
    subject = str(entry.get("subject") or "").strip().lower().replace("ё", "е")
    return subject in EMPTY_MARKERS


def _merge(subs: dict[int, dict]) -> dict:
    ordered = [subs[key] for key in sorted(subs)]
    merged: dict = {}
    for field_name in MERGE_FIELDS:
        values: list[str] = []
        for entry in ordered:
            value = str(entry.get(field_name) or "").strip()
            if value and value not in values:
                values.append(value)
        merged[field_name] = " / ".join(values)
    merged["diff"] = any(bool(entry.get("diff")) for entry in ordered)
    return merged


def normalize_day(
    lessons: list[dict],
    diffs: list[dict] | None = None,
    *,
    times: tuple[str, ...] | None = None,
) -> list[dict]:
    """Сырые lessons+diffs -> список записей, отсортированный по номерам уроков."""
    diffs = diffs or []
    times = times or LESSON_TIMES

    rows: dict[int, dict[int, dict]] = {}
    overrides: dict[int, dict] = {}

    for raw in list(lessons):
        if not isinstance(raw, dict):
            continue
        number = _number(raw)
        if number is None:
            continue
        rows.setdefault(number, {})[_subgroup(raw)] = _make_entry(raw, is_diff=False)

    for raw in list(diffs):
        if not isinstance(raw, dict):
            continue
        number = _number(raw)
        if number is None:
            continue
        subgroup = _subgroup(raw)
        entry = _make_entry(raw, is_diff=True)
        if subgroup == 0:
            # Элемент изменения на весь номер: базовые подгруппы перекрываются.
            overrides[number] = entry
        else:
            rows.setdefault(number, {})[subgroup] = entry

    for number, entry in overrides.items():
        rows[number] = {0: entry}

    result: list[dict] = []
    for number in sorted(rows):
        subs = {sg: entry for sg, entry in rows[number].items() if not _is_empty(entry)}
        if not subs:
            continue
        merged = _merge(subs)
        if not merged["subject"]:
            continue
        result.append(
            {
                "lesson": number,
                "time": lesson_time(number) if number <= len(times) else "",
                **merged,
            }
        )
    return result


def index_by_lesson(entries: list[dict]) -> dict[int, dict]:
    return {int(entry["lesson"]): entry for entry in entries if "lesson" in entry}
