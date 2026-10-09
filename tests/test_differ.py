from __future__ import annotations

from bot.differ import canonical, coerce_snapshot, diff_days, slots
from bot.formatter import format_changes
from bot.normalize import normalize_day


def _day(load_fixture, name: str = "group_10n_wd4.json"):
    data = load_fixture(name)
    return normalize_day(data["lessons"], data.get("diffs"))


def test_canonical_joins_fields():
    assert canonical({"subject": "Химия", "teacher": "Климова Л.И.", "room": "309"}) == "Химия, Климова Л.И., 309"


def test_canonical_skips_empty_parts():
    assert canonical({"subject": "", "teacher": "", "room": ""}) == "Нет"


def test_no_changes_when_snapshots_equal(load_fixture):
    day = _day(load_fixture)
    assert diff_days({4: day}, {4: [dict(entry) for entry in day]}) == {}


def test_removed_lesson(load_fixture):
    day = _day(load_fixture)
    reduced = [entry for entry in day if entry["lesson"] != 6]
    changes = diff_days({4: day}, {4: reduced})
    assert changes == {4: ["6 урок: АнглЯзык, Учитель, Англ.язык -> Нет"]}


def test_added_lesson(load_fixture):
    day = _day(load_fixture)
    extra = [
        *day,
        {"lesson": 7, "time": "14:35-15:15", "subject": "Алгебра", "teacher": "Бондарь А. А.", "room": "304", "klass": "10Н", "diff": False},
    ]
    changes = diff_days({4: day}, {4: extra})
    assert changes == {4: ["7 урок: Нет -> Алгебра, Бондарь А. А., 304"]}


def test_changed_lesson(load_fixture):
    day = _day(load_fixture)
    changed = [dict(entry) for entry in day]
    changed[0] = {**changed[0], "subject": "Математика", "room": "310"}
    changes = diff_days({4: day}, {4: changed})
    assert changes == {4: ["1 урок: Алгебра, Бондарь А. А., 304 -> Математика, Бондарь А. А., 310"]}


def test_new_day_appears(load_fixture):
    day = _day(load_fixture)
    changes = diff_days({}, {4: day})
    assert 4 in changes
    assert changes[4][0].startswith("1 урок: Нет -> Алгебра")


def test_slots_map(load_fixture):
    day = _day(load_fixture)
    assert set(slots(day)) == {1, 2, 3, 4, 5, 6}
    assert slots(day)[3] == "Русский, Корюкалова М.С., 324"


def test_coerce_snapshot_string_keys():
    raw = {"4": [{"lesson": 1, "subject": "Алгебра", "teacher": "Бондарь", "room": "304"}], "oops": []}
    snap = coerce_snapshot(raw)
    assert set(snap) == {4}
    assert snap[4][0]["lesson"] == 1


def test_spec_message_format():
    lines = [
        "1 урок: Химия, Климова Л.И., 309 -> Нет",
        "5 урок: Нет -> Алгебра, Бондарь А.А., 304",
    ]
    text = format_changes("10Н", 5, lines)
    assert text == (
        "Изменения в расписании на Пятницу 10Н\n"
        "1 урок: Химия, Климова Л.И., 309 -> Нет\n"
        "5 урок: Нет -> Алгебра, Бондарь А.А., 304"
    )
