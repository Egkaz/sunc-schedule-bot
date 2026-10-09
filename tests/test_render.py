from __future__ import annotations

from io import BytesIO

from PIL import Image

from bot import render
from bot.normalize import normalize_day
from bot.render import entity_card, group_card, render_card, schedule_photo, SITE_TEACHER_FIELDS


def _entries(load_fixture, name: str) -> list[dict]:
    data = load_fixture(name)
    return normalize_day(data["lessons"], data.get("diffs"))


def _size(png: bytes) -> tuple[int, int]:
    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    return image.size


def test_group_card_covers_seven_lesson_numbers(load_fixture):
    card = group_card("10Н", 4, _entries(load_fixture, "group_10n_wd4.json"))
    assert card.title == "Расписание на Четверг 10Н"
    assert [row.number for row in card.rows] == [1, 2, 3, 4, 5, 6, 7]
    assert card.rows[0].text.startswith("Алгебра, 304")
    assert card.rows[6].text == ""  # седьмого урока нет — ячейка пустая, как на сайте
    assert card.has_lessons


def test_group_card_marks_site_changes(load_fixture):
    card = group_card("10Н", 5, _entries(load_fixture, "group_10n_wd5.json"))
    changed = [row.number for row in card.rows if row.change]
    assert changed == [1]
    assert card.rows[0].text.startswith("Химия, 309")


def test_group_card_without_lessons(load_fixture):
    card = group_card("10Н", 7, [])
    assert card.title == "Расписание на Воскресенье 10Н"
    assert not card.has_lessons
    assert "Занятий нет" in card.text


def test_entity_card_uses_site_field_order(load_fixture):
    card = entity_card("Бондарь А. А.", 4, _entries(load_fixture, "group_10n_wd4.json"), SITE_TEACHER_FIELDS)
    assert card.title == "Бондарь А. А., Четверг"
    assert card.rows[0].text == "Алгебра, 304, 10Н"  # предмет, аудитория, класс — как в custom.js


def test_render_card_returns_png_table(load_fixture):
    card = group_card("10Н", 4, _entries(load_fixture, "group_10n_wd4.json"))
    png = render_card(card)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = _size(png)
    assert width == render.WIDTH
    assert height > 400  # заголовок + шапка + семь строк


def test_render_card_empty_day(load_fixture):
    png = render_card(group_card("10Н", 7, []))
    assert _size(png)[0] == render.WIDTH
    assert len(png) > 1000


def test_render_card_grows_with_long_cells():
    long_entries = [
        {
            "lesson": 1,
            "time": "9:00-9:40",
            "subject": "Очень длинный предмет для проверки переноса строки в ячейке таблицы",
            "teacher": "Преподаватель С Невероятно Длинной Фамилией",
            "room": "999 / 998",
            "klass": "10Н",
            "diff": False,
        }
    ]
    tall = render_card(group_card("10Н", 3, long_entries))
    flat = render_card(group_card("10Н", 3, []))
    assert _size(tall)[1] > _size(flat)[1]
    assert _size(tall)[0] == _size(flat)[0]


def test_schedule_photo_returns_none_when_font_missing(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("шрифт не найден")

    monkeypatch.setattr(render, "_load_font", boom)
    card = group_card("10Н", 4, [])
    assert schedule_photo(card) is None
