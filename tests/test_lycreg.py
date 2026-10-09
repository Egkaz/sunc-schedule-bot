from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest

from bot.formatter import format_homework
from bot.lycreg import (
    BASE_URL,
    HOMEWORK_MAX_AGE_DAYS,
    SUBJ_DEF,
    CaptchaRequired,
    LycregClient,
    LycregError,
    _norm_fio,
    date_position,
    journal_date_key,
    key_position,
    pick_homework,
    pos_date,
)
from bot.storage import KEY_LYCREG_TOKEN, Storage


def test_journal_date_key_positions():
    assert journal_date_key(date(2026, 9, 1)) == "d001"
    assert journal_date_key(date(2026, 10, 8)) == "d108"
    assert journal_date_key(date(2026, 12, 31)) == "d331"
    assert journal_date_key(date(2027, 1, 5)) == "d405"


def test_date_positions_across_year_boundary():
    assert date_position(date(2026, 9, 1)) == 901
    assert date_position(date(2026, 10, 10)) == 1010
    # январь следующего календарного года идёт после декабря в учебном году
    assert date_position(date(2026, 12, 31)) < date_position(date(2027, 1, 5))
    assert date_position(date(2027, 1, 5)) < date_position(date(2027, 5, 31))


def test_key_position_roundtrip():
    for day in (date(2026, 9, 1), date(2026, 10, 10), date(2027, 1, 5), date(2027, 5, 31)):
        assert key_position(journal_date_key(day)) == date_position(day)
    assert key_position("мусор") is None
    assert key_position("d9xx") is None


def test_pos_date_roundtrip_and_guards():
    for day in (date(2026, 9, 1), date(2026, 10, 10), date(2027, 1, 5), date(2027, 8, 31)):
        assert pos_date(date_position(day)) == day.replace(year=2000)
    assert pos_date(1300) is None  # «месяц 13» — мусорная позиция
    assert pos_date(12) is None


JOURNAL = {
    "10Н_10H01_Bondar1": {
        "d106": ["Тема", "ДЗ старое", "0", ""],
        "d108": ["Тема", "ДЗ пятничное", "0", ""],
        "d109": ["Тема", "ДЗ свежее", "0", ""],
    },
    "10Н_11H02_Sidorov": {
        "d109": ["Тема", "ДЗ по физике", "0", ""],
    },
    "9А_10H01_Bondar1": {
        "d109": ["Тема", "ДЗ не нашего класса", "0", ""],
    },
}

TEACH_BY_FIO = {
    _norm_fio("Бондарь А. А."): "Bondar1",
    _norm_fio("Сидоров П. П."): "Sidorov",
}

SUBJ = {**SUBJ_DEF, "10H01": "Алгебра", "11H02": "Физика"}


def test_pick_homework_takes_latest_before_target():
    hw = pick_homework(
        JOURNAL,
        "10Н",
        subject="Алгебра",
        teacher="Бондарь А. А.",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw == "ДЗ свежее"  # от 09.10 — свежее «пятничного»


def test_pick_homework_excludes_day_of_target_itself():
    hw = pick_homework(
        JOURNAL,
        "10Н",
        subject="Алгебра",
        teacher="Бондарь А. А.",
        target_pos=date_position(date(2026, 10, 9)),
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw == "ДЗ пятничное"  # задано 08.10; дзадание самой пятницы не для пятницы


def test_pick_homework_falls_back_to_subject_name():
    # преподавателя нет в журнале — ищем по похожести предмета (класс всё равно сверяем)
    hw = pick_homework(
        JOURNAL,
        "10Н",
        subject="Алгебра",
        teacher="Кто-то Неизвестный",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw == "ДЗ свежее"


def test_pick_homework_scopes_by_class():
    hw_foreign = pick_homework(
        JOURNAL,
        "9А",
        subject="Алгебра",
        teacher="Бондарь А. А.",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw_foreign == "ДЗ не нашего класса"


def test_pick_homework_teacher_with_slash():
    hw = pick_homework(
        JOURNAL,
        "10Н",
        subject="Алгебра",
        teacher="Бондарь А. А./Сидоров П. П.",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw == "ДЗ свежее"


def test_pick_homework_teacher_initials_without_spaces():
    """В расписании «Черемичкина И.С.», в журнале «Черемичкина И. С.»."""
    journal = {"10Н_s610_Chern1": {"d108": ["Тема", "ДЗ по физике", "0", ""]}}
    hw = pick_homework(
        journal,
        "10Н",
        subject="Физика",
        teacher="Черемичкина И.С.",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio={_norm_fio("Черемичкина И. С."): "Chern1"},
        subj_names=SUBJ,
    )
    assert hw == "ДЗ по физике"


def test_pick_homework_subject_fallback_via_subj_def():
    """Преподаватель-заглушка: предмет из расписания матчится со словарём subjDef."""
    journal = {"10Н_s430_nosuch": {"d108": ["Тема", "ДЗ анализа", "0", ""]}}
    hw = pick_homework(
        journal,
        "10Н",
        subject="Алгебра",  # в словаре s430 = «Алгебра и начала анализа»
        teacher="Учитель",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio={},
        subj_names=SUBJ,
    )
    assert hw == "ДЗ анализа"


def test_subj_def_covers_core_subjects():
    assert SUBJ_DEF["s610"] == "Физика"
    assert SUBJ_DEF["s210"] == "Английский язык"
    assert SUBJ_DEF["s570"] == "География"


def test_pick_homework_skips_when_latest_lesson_has_no_hw():
    """Свежая запись без ДЗ = «не задано»: старое трёхнедельное не показываем.

    Кейс физики: уроки 12/19/26.09 и 03.10, ДЗ записано только 26.09.
    """
    journal = {
        "10Н_s610_Chern1": {
            "d026": ["Работа и мощность", "задача 2 (пар11)", "0", ""],
            "d029": ["Электрический ток", "", "0", ""],
            "d103": ["Что-то", "", "0", ""],
        }
    }
    hw = pick_homework(
        journal,
        "10Н",
        subject="Физика",
        teacher="Черемичкина И. С.",
        target_pos=date_position(date(2026, 10, 10)),
        teach_by_fio={_norm_fio("Черемичкина И. С."): "Chern1"},
        subj_names=SUBJ,
    )
    assert hw is None  # последний урок (03.10) без ДЗ


def test_pick_homework_rejects_stale_hw_from_journal_gap():
    """Пропуск уроков в журнале: ДЗ старше лимита не показываем."""
    journal = {"10Н_10H01_Bondar1": {"d026": ["Тема", "ДЗ трёхнедельной давности", "0", ""]}}
    hw = pick_homework(
        journal,
        "10Н",
        subject="Алгебра",
        teacher="Бондарь А. А.",
        target_pos=date_position(date(2026, 10, 10)),  # 14 дней > лимита
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw is None

    fresh_pos = date_position(date(2026, 10, 5))  # 05.10 — в пределах лимита
    journal_fresh = {"10Н_10H01_Bondar1": {"d026": ["Тема", "ДЗ", "0", ""]}}
    hw = pick_homework(
        journal_fresh,
        "10Н",
        subject="Алгебра",
        teacher="Бондарь А. А.",
        target_pos=fresh_pos,
        teach_by_fio=TEACH_BY_FIO,
        subj_names=SUBJ,
    )
    assert hw == "ДЗ"  # 9 дней <= 10
    assert HOMEWORK_MAX_AGE_DAYS == 10


def test_format_homework_layout_and_truncation():
    text = format_homework(date(2026, 10, 10), 6, [("Алгебра", "§5  1-10")])
    lines = text.splitlines()
    assert lines[0] == "ДЗ на Субботу 10.10:"
    assert lines[1] == "• Алгебра — §5 1-10"  # косые пробелы схлопнуты

    text = format_homework(date(2026, 10, 9), 5, [("Технология", "х" * 400)])
    body = text.splitlines()[1]
    assert body.endswith("…") and len(body) < 330


def test_ssl_context_loads_pinned_intermediate():
    import ssl

    from bot.lycreg import CA_PIN_PATH, ssl_context

    context = ssl_context()
    assert isinstance(context, ssl.SSLContext)
    if CA_PIN_PATH.exists():
        # пин intermediate подгрузился в доверенный набор
        subjects = {c["subject"] for c in context.get_ca_certs()}
        assert any("YR2" in str(s) for s in subjects)


def _open_storage(tmp_path) -> Storage:
    return Storage(tmp_path / "bot.db")


def _client(handler, storage) -> LycregClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": BASE_URL + "/",
            "Origin": BASE_URL,
        },
    )
    return LycregClient("user42", "secret", storage=storage, http=http)


def test_captcha_required_when_no_token(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cpt.a"
        return httpx.Response(200, content=b"PNGDATA", headers={"X-Cpt": "ci-123"})

    async def main():
        storage = _open_storage(tmp_path)
        await storage.open()
        client = _client(handler, storage)
        try:
            with pytest.raises(CaptchaRequired) as caught:
                await client.journal()
            assert caught.value.ci == "ci-123"
            assert caught.value.image == b"PNGDATA"
        finally:
            await storage.close()

    asyncio.run(main())


def test_login_saves_token_and_journal_uses_it(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cpt.a":
            return httpx.Response(200, content=b"PNG", headers={"X-Cpt": "ci-1"})
        payload = json.loads(request.content)
        if payload.get("f") == "login":
            assert payload["l"] == "user42" and payload["p"] == "secret"
            assert payload["c"] == "1234" and payload["ci"] == "ci-1"
            return httpx.Response(200, text='{"token":"TOK1","roles":["pupil"]}')
        if payload.get("f") == "jrnGet":
            assert payload["p"] == "TOK1"
            return httpx.Response(200, text='{"10Н_10H01_Bondar1": {}}')
        raise AssertionError(f"неожиданный вызов {payload.get('f')}")

    async def main():
        storage = _open_storage(tmp_path)
        await storage.open()
        client = _client(handler, storage)
        try:
            token = await client.login("ci-1", " 1234 ")
            assert token == "TOK1"
            assert await storage.get(KEY_LYCREG_TOKEN) == "TOK1"
            journal = await client.journal()
            assert "10Н_10H01_Bondar1" in journal
        finally:
            await storage.close()

    asyncio.run(main())


def test_login_rejects_none_response(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="none")

    async def main():
        storage = _open_storage(tmp_path)
        await storage.open()
        client = _client(handler, storage)
        try:
            with pytest.raises(LycregError):
                await client.login("ci", "0000")
        finally:
            await storage.close()

    asyncio.run(main())


def test_expired_token_clears_storage_and_asks_captcha(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cpt.a":
            return httpx.Response(200, content=b"PNG", headers={"X-Cpt": "ci-2"})
        return httpx.Response(200, text="none")

    async def main():
        storage = _open_storage(tmp_path)
        await storage.open()
        await storage.set(KEY_LYCREG_TOKEN, "OLD")
        client = _client(handler, storage)
        try:
            with pytest.raises(CaptchaRequired):
                await client.journal()
            assert not await storage.get(KEY_LYCREG_TOKEN)
        finally:
            await storage.close()

    asyncio.run(main())


def test_homework_collects_only_lessons_with_assignments(tmp_path):
    journal = {
        "10Н_10H01_Bondar1": {"d109": ["Тема", "Выучить правило", "0", ""]},
        "10Н_11H02_Sidorov": {"d109": ["Тема", "", "0", ""]},  # пустое ДЗ пропускается
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("f") == "jrnGet":
            return httpx.Response(200, text=json.dumps(journal))
        if payload.get("f") == "teachList":
            return httpx.Response(
                200,
                text=json.dumps(
                    [
                        {"login": "Bondar1", "fio": "Бондарь А. А."},
                        {"login": "Sidorov", "fio": "Сидоров П. П."},
                    ]
                ),
            )
        if payload.get("f") == "subjList":
            return httpx.Response(200, text=json.dumps(SUBJ))
        raise AssertionError(payload.get("f"))

    async def main():
        storage = _open_storage(tmp_path)
        await storage.open()
        await storage.set(KEY_LYCREG_TOKEN, "TOK")
        client = _client(handler, storage)
        lessons = [
            {"subject": "Алгебра", "teacher": "Бондарь А. А."},
            {"subject": "Физика", "teacher": "Сидоров П. П."},
            {"subject": "Нет", "teacher": "—"},
            {"subject": "Информатика", "teacher": "Кто-то"},
        ]
        try:
            items = await client.homework("10Н", lessons, date(2026, 10, 10))
            assert items == [("Алгебра", "Выучить правило")]
        finally:
            await storage.close()

    asyncio.run(main())
