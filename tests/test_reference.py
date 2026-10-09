from __future__ import annotations

import pytest

from bot.fetcher import FetchError, parse_reference


def test_reference_from_real_page(load_page):
    ref = parse_reference(load_page)
    assert ref.group_id("10Н") == 22
    assert ref.group_id("10н") == 22
    assert ref.group_id("11А") == 6
    assert ref.group_id("99Х") is None

    assert ref.auditory_id("304") == 87
    assert ref.auditory_id("309") == 81
    assert ref.auditory_id("999") is None

    assert len(ref.groups) == 33
    assert len(ref.teachers) >= 100
    assert len(ref.auditories) == 59


def test_teacher_lookup_by_surname(load_page):
    ref = parse_reference(load_page)
    matches = ref.teacher_matches("Бондарь")
    assert matches, "фамилия Бондарь должна найтись"
    assert matches[0][0].startswith("Бондарь")
    assert ref.teacher_id("Бондарь") == matches[0][1]

    assert ref.teacher_matches("НесуществующаяФамилия") == []
    assert ref.teacher_id("НесуществующаяФамилия") is None


def test_teacher_lookup_is_case_insensitive(load_page):
    ref = parse_reference(load_page)
    assert ref.teacher_matches("бОНДАРЬ") == ref.teacher_matches("Бондарь")


def test_reference_rejects_page_without_selects():
    with pytest.raises(FetchError):
        parse_reference("<html><body>ничего</body></html>")
