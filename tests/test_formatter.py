from __future__ import annotations

from datetime import date

from bot.days import LESSON_TIMES
from bot.formatter import (
    AUDITORY_FIELDS,
    GROUP_FIELDS,
    TEACHER_FIELDS,
    format_changes_caption,
    format_day,
    format_entries,
    format_entity_day,
    format_help,
    format_homework_caption,
    format_status,
    format_usage,
)
from bot.normalize import normalize_day


def _day(load_fixture, name: str):
    data = load_fixture(name)
    return normalize_day(data["lessons"], data.get("diffs"))


def test_format_day_header_and_lines(load_fixture):
    day = _day(load_fixture, "group_10n_wd4.json")
    text = format_day("10Н", 4, day)
    lines = text.splitlines()
    assert lines[0] == "Расписание на Четверг 10Н"
    assert lines[1] == f"1 урок ({LESSON_TIMES[0]}): Алгебра, Бондарь А. А., 304"
    assert lines[2] == f"2 урок ({LESSON_TIMES[1]}): Алгебра, Бондарь А. А., 304"
    assert lines[3] == f"3 урок ({LESSON_TIMES[2]}): Русский, Корюкалова М.С., 324"
    assert lines[5] == f"5 урок ({LESSON_TIMES[4]}): АнглЯзык, Учитель, Англ.язык"
    assert len(lines) == 7
    assert all("Занятий нет" not in line for line in lines)


def test_format_day_uses_accusative_day_name():
    text = format_day("10Н", 5, [])
    assert text.splitlines()[0] == "Расписание на Пятницу 10Н"
    text = format_day("10Н", 3, [])
    assert text.splitlines()[0] == "Расписание на Среду 10Н"
    text = format_day("10Н", 6, [])
    assert text.splitlines()[0] == "Расписание на Субботу 10Н"


def test_format_day_skips_missing_lessons(load_fixture):
    day = _day(load_fixture, "group_10n_wd6.json")
    numbers = [entry["lesson"] for entry in day]
    text = format_day("10Н", 6, day)
    for number in range(1, 8):
        line = f"{number} урок ("
        if number in numbers:
            assert line in text
        else:
            assert line not in text


def test_format_day_empty():
    text = format_day("10Н", 7, [])
    assert text.splitlines() == ["Расписание на Воскресенье 10Н", "Занятий нет"]


def test_subgroups_merged_with_slash(load_fixture):
    day = _day(load_fixture, "group_10n_wd1.json")
    first = next(entry for entry in day if entry["lesson"] == 1)
    assert first["subject"] == "Информатика"
    assert first["teacher"] == "Киселев П.Г. / Сандакова С. Л."
    assert first["room"] == "206 / 207"
    text = format_day("10Н", 1, day)
    assert "1 урок (9:00-9:40): Информатика, Киселев П.Г. / Сандакова С. Л., 206 / 207" in text


def test_lesson_times_cover_all_seven():
    assert len(LESSON_TIMES) == 7
    assert LESSON_TIMES[0] == "9:00-9:40"
    assert LESSON_TIMES[-1] == "14:35-15:15"


def test_teacher_view_shows_class(load_fixture):
    day = _day(load_fixture, "teacher_227_wd1.json")
    text = format_entity_day("Киселев П.Г.", 1, day, TEACHER_FIELDS)
    lines = text.splitlines()
    assert lines[0] == "Киселев П.Г., Понедельник"
    assert "10Н" in lines[1]
    assert "Киселев" not in lines[1]


def test_auditory_view_shows_teacher_and_class(load_fixture):
    day = _day(load_fixture, "auditory_87_wd1.json")
    text = format_entity_day("Аудитория 304", 1, day, AUDITORY_FIELDS)
    assert "Аудитория 304, Понедельник" in text.splitlines()[0]
    assert "10Д" in text


def test_group_view_fields(load_fixture):
    day = _day(load_fixture, "group_10n_wd4.json")
    line = format_entries(day, GROUP_FIELDS)[0]
    assert line == "1 урок (9:00-9:40): Алгебра, Бондарь А. А., 304"


def test_diff_marker_is_appended():
    entries = [
        {"lesson": 1, "time": "9:00-9:40", "subject": "Химия", "teacher": "Климова Л.И.", "room": "309", "diff": True}
    ]
    text = format_day("10Н", 5, entries)
    assert text.splitlines()[1] == "1 урок (9:00-9:40): Химия, Климова Л.И., 309 — изменение"


def test_status_and_usage_texts():
    status = format_status(
        klass="10Н",
        last_ok="2026-10-08T18:03:00",
        fail_streak=0,
        snapshot_days=6,
        snapshot_updated="2026-10-08T18:03:00",
        poll_minutes=10,
        daily_hour=15,
    )
    assert "Последний успешный опрос: 2026-10-08T18:03:00" in status
    assert "пост в 15:00" in status
    assert format_usage("/день", "пн..сб") == "Неверный формат. Пример: /день пн..сб"
    assert "/препод фамилия [день]" in format_help("10Н", 15)


def test_changes_caption_is_html_blockquote():
    caption = format_changes_caption(["4 урок: Нет -> АнглЯзык, Учитель, Англ.язык"])
    assert caption.startswith("<b>Что изменилось</b>")
    assert caption.endswith("</blockquote>")
    assert "<blockquote>" in caption
    assert "<s>Нет</s> → <b>АнглЯзык, Учитель, Англ.язык</b>" in caption


def test_changes_caption_strikes_removed_lesson():
    caption = format_changes_caption(["6 урок: АнглЯзык, Учитель, Англ.язык -> Нет"])
    assert "<s>Нет</s>" in caption
    assert "→ <s>Нет</s>" in caption
    assert "<b>Нет</b>" not in caption


def test_changes_caption_escapes_html():
    caption = format_changes_caption(["1 урок: Алгебра <и> & Ко -> Нет"])
    assert "&lt;и&gt;" in caption and "&amp; Ко" in caption
    assert "<и>" not in caption


def test_changes_caption_without_arrow_is_plain_escaped():
    caption = format_changes_caption(["что-то странное"])
    assert caption.startswith("<b>Что изменилось</b>")
    assert "что-то странное" in caption


def test_changes_caption_fits_media_limit():
    lines = [f"{index} урок: {'Длинный предмет ' * 12} -> {'Ещё длиннее ' * 12}" for index in range(1, 8)]
    assert len(format_changes_caption(lines)) <= 1024


def test_homework_caption_is_html_blockquote():
    caption = format_homework_caption(date(2026, 10, 10), 6, [("Алгебра", "§5 1-10")])
    assert caption == "<b>ДЗ на Субботу 10.10</b>\n<blockquote>Алгебра — §5 1-10</blockquote>"


def test_homework_caption_collapses_whitespace_and_escapes():
    caption = format_homework_caption(date(2026, 10, 9), 5, [("Англ & Язык", "п. 1\n\tп. 2")])
    assert "Англ &amp; Язык — п. 1 п. 2" in caption


def test_homework_caption_fits_media_limit():
    items = [(f"Предмет{i}", "Д" * 400) for i in range(10)]
    caption = format_homework_caption(date(2026, 10, 10), 6, items)
    assert len(caption) <= 1024
    assert caption.endswith("…</blockquote>")
