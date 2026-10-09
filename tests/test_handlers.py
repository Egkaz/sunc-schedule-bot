from __future__ import annotations

from bot.handlers import _parse_trailing_day, _split_args


def test_split_args():
    assert _split_args(None) == []
    assert _split_args("") == []
    assert _split_args("10Б вт") == ["10Б", "вт"]


def test_parse_trailing_day_detects_weekday():
    assert _parse_trailing_day(["Бондарь", "пт"]) == ("Бондарь", 5)
    assert _parse_trailing_day(["10Б", "вт"]) == ("10Б", 2)
    assert _parse_trailing_day(["Корюкалова", "М.С.", "ср"]) == ("Корюкалова М.С.", 3)


def test_parse_trailing_day_keeps_query_when_last_is_not_day():
    assert _parse_trailing_day(["Бондарь"]) == ("Бондарь", None)
    assert _parse_trailing_day(["Киселев"]) == ("Киселев", None)
    assert _parse_trailing_day([]) == ("", None)


def test_parse_trailing_day_does_not_eat_single_letter_surname():
    # «Ким» — не день недели
    assert _parse_trailing_day(["Ким"]) == ("Ким", None)
