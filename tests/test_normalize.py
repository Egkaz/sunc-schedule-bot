from __future__ import annotations

from bot.days import api_weekday, day_name, lesson_time, next_weekday, parse_day, upcoming_dates
from bot.normalize import normalize_day


def test_parse_day_names():
    assert parse_day("пн") == 1
    assert parse_day("ПТ") == 5
    assert parse_day("суббота") == 6
    assert parse_day("Четверг") == 4
    assert parse_day("воскресенье") is None
    assert parse_day("вс") is None
    assert parse_day("") is None
    assert parse_day("хз") is None


def test_day_names_and_times():
    assert day_name(1) == "Понедельник"
    assert day_name(6) == "Суббота"
    assert day_name(7) == "Воскресенье"
    assert lesson_time(1) == "9:00-9:40"
    assert lesson_time(7) == "14:35-15:15"
    assert lesson_time(8) == ""


def test_upcoming_dates_skip_sunday():
    from datetime import date

    # 2026-10-08 — четверг
    days = upcoming_dates(date(2026, 10, 8), 7)
    assert len(days) == 6
    assert all(day.weekday() != 6 for day in days)
    assert api_weekday(days[0]) == 4  # сегодняшний четверг
    assert sorted(api_weekday(day) for day in days) == [1, 2, 3, 4, 5, 6]


def test_next_weekday_is_nearest():
    from datetime import date

    thursday = date(2026, 10, 8)
    assert next_weekday(thursday, 4) == thursday
    assert next_weekday(thursday, 5) == date(2026, 10, 9)
    assert next_weekday(thursday, 3) == date(2026, 10, 14)


def test_normalize_orders_and_times(load_fixture):
    data = load_fixture("group_10n_wd2.json")
    day = normalize_day(data["lessons"], data.get("diffs"))
    numbers = [entry["lesson"] for entry in day]
    assert numbers == sorted(numbers)
    assert set(numbers) <= {1, 2, 3, 4, 5, 6, 7}
    for entry in day:
        assert entry["time"] == lesson_time(entry["lesson"])


def test_normalize_merges_parallel_subgroups(load_fixture):
    data = load_fixture("group_10n_wd2.json")
    day = normalize_day(data["lessons"], data.get("diffs"))
    fourth = next(entry for entry in day if entry["lesson"] == 4)
    assert fourth["subject"] == "Алгебра / Геометрия"
    assert fourth["room"] == "303 / 308"
    assert fourth["teacher"] == "Бондарь А. А. / Ануфриенко С. А."


def test_normalize_empty_day(load_fixture):
    data = load_fixture("group_10n_wd7.json")
    assert normalize_day(data["lessons"], data.get("diffs")) == []


def test_normalize_marks_diffs(load_fixture):
    data = load_fixture("group_10n_wd4.json")
    first = data["lessons"][0]
    day = normalize_day(data["lessons"], [first])
    entries = {entry["lesson"]: entry for entry in day}
    assert entries[1]["diff"] is True
    assert entries[2]["diff"] is False


def test_normalize_drops_explicit_no_lesson(load_fixture):
    """diffs пятницы содержит «Нет» на 5,6,7 — это отмена, а не урок."""
    data = load_fixture("group_10n_wd5.json")
    assert any((item.get("subject") == "Нет") for item in data["diffs"])

    day = normalize_day(data["lessons"], data.get("diffs"))
    numbers = [entry["lesson"] for entry in day]
    assert numbers == [1, 2, 3, 4]
    assert all(entry["subject"] != "Нет" for entry in day)


def test_normalize_diff_overrides_whole_row(load_fixture):
    data = load_fixture("group_10n_wd5.json")
    day = normalize_day(data["lessons"], data.get("diffs"))
    first = next(entry for entry in day if entry["lesson"] == 1)
    assert first["subject"] == "Химия"
    assert first["room"] == "309"
    assert first["diff"] is True


def test_normalize_diff_replaces_content():
    lessons = [
        {"number": 1, "subgroup": 0, "subject": "Физика", "teacher": "Старый П. П.", "auditory": "201", "group": "10Н"}
    ]
    diffs = [
        {"number": 1, "subgroup": 0, "subject": "Алгебра", "teacher": "Новый Н. Н.", "auditory": "304", "group": "10Н"}
    ]
    day = normalize_day(lessons, diffs)
    assert len(day) == 1
    assert day[0]["subject"] == "Алгебра"
    assert day[0]["teacher"] == "Новый Н. Н."
    assert day[0]["diff"] is True


def test_normalize_diff_targets_single_subgroup():
    lessons = [
        {"number": 4, "subgroup": 1, "subject": "Алгебра", "teacher": "Бондарь А. А.", "auditory": "303", "group": "10Н"},
        {"number": 4, "subgroup": 2, "subject": "Геометрия", "teacher": "Ануфриенко С. А.", "auditory": "308", "group": "10Н"},
    ]
    diffs = [
        {"number": 4, "subgroup": 2, "subject": "Нет", "teacher": "Нет", "auditory": "Нет", "group": "10Н"},
    ]
    day = normalize_day(lessons, diffs)
    assert len(day) == 1
    assert day[0]["subject"] == "Алгебра"
    assert day[0]["diff"] is False


def test_normalize_group_field(load_fixture):
    data = load_fixture("teacher_227_wd1.json")
    day = normalize_day(data["lessons"], data.get("diffs"))
    assert all(entry["klass"] for entry in day)
