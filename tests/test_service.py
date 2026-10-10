from __future__ import annotations

from datetime import date, datetime

import pytest

from bot.ai import ChatAnalysis, HomeworkDraft
from bot.config import Config
from bot.fetcher import FetchError, Reference
from bot.service import ScheduleService, _same_subject, resolve_thread_id
from bot.storage import KEY_FAIL_STREAK, KEY_LAST_OK, KEY_THREAD_ID, Storage


class FakeClient:
    """Заглушка ScheduleClient: отдаёт готовые «сырые» ответы API."""

    def __init__(self, reference: Reference, payload: dict[int, tuple[list, list]]):
        self._reference = reference
        self.payload = payload
        self.fail_with: Exception | None = None

    async def reference(self, *, force: bool = False) -> Reference:
        return self._reference

    async def fetch_group_day(self, group_id: int, weekday: int):
        if self.fail_with is not None:
            raise self.fail_with
        return self.payload.get(weekday, ([], []))


@pytest.fixture
def reference() -> Reference:
    return Reference(groups={"10Н": 22}, teachers={"Бондарь А. А.": 154}, auditories={"304": 87})


def _payload(load_fixture, days=(1, 2, 3, 4, 5, 6, 7)) -> dict[int, tuple[list, list]]:
    payload = {}
    for day in days:
        data = load_fixture(f"group_10n_wd{day}.json")
        payload[day] = (data["lessons"], data.get("diffs") or [])
    return payload


async def _service(
    tmp_path,
    reference,
    payload,
    *,
    send=None,
    admin=None,
    photo=None,
    lycreg=None,
    admin_photos=None,
    photo_id=None,
    ai=None,
):
    storage = Storage(tmp_path / "bot.db")
    await storage.open()
    config = Config(
        bot_token="123456:TESTTOKEN",
        chat_id="-1001234567890",
        klass="10Н",
        admin_id=1,
        db_path=tmp_path / "bot.db",
    )
    sent: list[str] = []
    notified: list[str] = []

    async def send_chat(text: str) -> None:
        (sent if send is None else send).append(text)

    async def notify_admin(text: str) -> None:
        (notified if admin is None else admin).append(text)

    async def send_photo_chat(card) -> None:
        assert photo is not None
        photo.append(card)
        return photo_id

    async def send_admin_photo(png: bytes, caption: str) -> None:
        assert admin_photos is not None
        admin_photos.append((png, caption))

    service = ScheduleService(
        config,
        FakeClient(reference, payload),
        storage,
        send_chat=send_chat,
        notify_admin=notify_admin,
        send_photo=send_photo_chat if photo is not None else None,
        admin_photo=send_admin_photo if admin_photos is not None else None,
        lycreg=lycreg,
        ai=ai,
    )
    return service, storage, sent, notified


async def test_thread_id_resolution(tmp_path, reference):
    service, storage, *_ = await _service(tmp_path, reference, {})
    try:
        cfg_env = Config(bot_token="t", chat_id="-1", klass="10Н", thread_id=42)
        cfg_plain = Config(bot_token="t", chat_id="-1", klass="10Н")

        assert await service.get_thread_id() is None
        assert await resolve_thread_id(storage, cfg_env) == 42

        await service.set_thread_id(777)
        assert await service.get_thread_id() == 777
        assert await resolve_thread_id(storage, cfg_env) == 777  # /тема важнее THREAD_ID
        assert await resolve_thread_id(storage, cfg_plain) == 777

        await storage.set(KEY_THREAD_ID, "мусор")
        assert await resolve_thread_id(storage, cfg_env) == 42
        assert await resolve_thread_id(storage, cfg_plain) is None
    finally:
        await storage.close()


async def test_first_poll_only_saves_snapshot(tmp_path, reference, load_fixture):
    service, storage, sent, notified = await _service(tmp_path, reference, _payload(load_fixture))
    try:
        result = await service.poll()
        assert result.ok and result.first_run
        assert sent == [] and notified == []
        snapshot = await storage.get_snapshot()
        assert set(snapshot) == {1, 2, 3, 4, 5, 6}  # воскресенье не опрашивается
        assert await storage.get_int(KEY_FAIL_STREAK, 0) == 0
        assert await storage.get(KEY_LAST_OK)
    finally:
        await storage.close()


async def test_no_duplicates_on_unchanged_schedule(tmp_path, reference, load_fixture):
    service, storage, sent, _ = await _service(tmp_path, reference, _payload(load_fixture))
    try:
        await service.poll()
        sent.clear()
        for _ in range(3):
            result = await service.poll()
            assert result.ok and not result.changes
        assert sent == []
    finally:
        await storage.close()


async def test_change_is_sent_once(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, sent, _ = await _service(tmp_path, reference, payload)
    try:
        await service.poll()
        sent.clear()

        # убрали шестой урок в четверг
        payload[4] = ([item for item in payload[4][0] if item["number"] != 6], [])

        result = await service.poll()
        assert result.ok and set(result.changes) == {4}
        assert len(sent) == 1
        assert sent[0].startswith("Изменения в расписании на Четверг 10Н")
        assert "6 урок: АнглЯзык, Учитель, Англ.язык -> Нет" in sent[0]

        sent.clear()
        result = await service.poll()
        assert not result.changes
        assert sent == []
    finally:
        await storage.close()


async def test_change_sent_as_table_with_caption(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    photos: list[object] = []
    service, storage, sent, _ = await _service(tmp_path, reference, payload, photo=photos)
    try:
        await service.poll()
        photos.clear()

        # убрали шестой урок в четверг
        payload[4] = ([item for item in payload[4][0] if item["number"] != 6], [])

        result = await service.poll()
        assert result.ok and set(result.changes) == {4}
        assert len(photos) == 1
        card = photos[0]
        assert card.title == "Изменения в расписании на Четверг 10Н"
        assert "<b>Что изменилось</b>" in card.caption and "<blockquote>" in card.caption
        assert "6 урок" in card.caption
        assert [row.number for row in card.rows if row.change] == [6]
        assert card.text.startswith("Изменения в расписании на Четверг 10Н")
        assert sent == []
    finally:
        await storage.close()


async def test_empty_response_is_treated_as_failure(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, sent, notified = await _service(tmp_path, reference, payload)
    try:
        await service.poll()
        before = await storage.get_snapshot()
        sent.clear()

        payload.clear()  # сайт вдруг отдал пустоту по всем дням
        result = await service.poll()

        assert not result.ok and result.fail_streak == 1
        assert sent == []
        assert await storage.get_snapshot() == before
        assert notified == []
    finally:
        await storage.close()


async def test_three_failures_notify_admin_once(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, sent, notified = await _service(tmp_path, reference, payload)
    try:
        await service.poll()
        service.client.fail_with = FetchError("site down")

        for expected in (1, 2, 3):
            result = await service.poll()
            assert not result.ok
            assert result.fail_streak == expected
        assert len(notified) == 1
        assert "3 раза подряд" in notified[0]

        result = await service.poll()
        assert result.fail_streak == 4
        assert len(notified) == 1, "после третьего сообщения молчим"

        service.client.fail_with = None
        result = await service.poll()
        assert result.ok
        assert await storage.get_int(KEY_FAIL_STREAK, 0) == 0
    finally:
        await storage.close()


async def test_publish_tomorrow_sends_formatted_day(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, sent, notified = await _service(tmp_path, reference, payload)
    try:
        # четверг 2026-10-08 14:00 -> пост про пятницу, воскресенье не мешает
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        text = await service.publish_tomorrow()
        assert text and text.startswith("Расписание на Пятницу 10Н")
        assert " урок (" in text
        assert sent == [text]
    finally:
        await storage.close()


async def test_publish_tomorrow_goes_through_photo_adapter(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    photos: list[object] = []
    service, storage, sent, notified = await _service(tmp_path, reference, payload, photo=photos)
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        text = await service.publish_tomorrow()
        assert text and text.startswith("Расписание на Пятницу 10Н")
        assert len(photos) == 1
        card = photos[0]
        assert card.title == "Расписание на Пятницу 10Н"
        assert [row.number for row in card.rows] == [1, 2, 3, 4, 5, 6, 7]
        assert card.text == text
        assert sent == [] and notified == []
    finally:
        await storage.close()


async def test_publish_tomorrow_skips_sunday(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, sent, notified = await _service(tmp_path, reference, payload)
    try:
        service.now = lambda: datetime(2026, 10, 10, 14, 0, tzinfo=service.tz)  # суббота -> завтра вс
        assert await service.publish_tomorrow() is None
        assert sent == []
    finally:
        await storage.close()


async def test_publish_tomorrow_reports_fetch_error(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, sent, notified = await _service(tmp_path, reference, payload)
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        service.client.fail_with = FetchError("site down")
        text = await service.publish_tomorrow()
        assert text is None
        assert sent == []
        assert len(notified) == 1 and "на завтра" in notified[0]
    finally:
        await storage.close()


async def test_status_text(tmp_path, reference, load_fixture):
    service, storage, _, _ = await _service(tmp_path, reference, _payload(load_fixture))
    try:
        await service.poll()
        text = await service.status_text()
        assert "Класс: 10Н" in text
        assert "Неудач подряд: 0" in text
        assert "Снимок: 6 дн." in text
        assert "пост в 15:00" in text
    finally:
        await storage.close()


def test_snapshot_digest_ignores_order():
    from bot.differ import snapshot_digest

    one = {1: [{"lesson": 2, "subject": "Б", "teacher": "", "room": ""}, {"lesson": 1, "subject": "А", "teacher": "", "room": ""}]}
    two = {1: [{"lesson": 1, "subject": "А", "teacher": "", "room": ""}, {"lesson": 2, "subject": "Б", "teacher": "", "room": ""}]}
    assert snapshot_digest(one) == snapshot_digest(two)


class FakeLycreg:
    """Заглушка LycregClient: отдаёт готовое ДЗ либо однократную ошибку."""

    def __init__(self, items=None, error: Exception | None = None, login_error: Exception | None = None) -> None:
        self.items = items if items is not None else []
        self.error = error
        self.login_error = login_error
        self.calls = 0
        self.logged_in: tuple[str, str] | None = None

    async def homework(self, klass: str, lessons: list[dict], target) -> list[tuple[str, str]]:
        self.calls += 1
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return self.items

    async def login(self, ci: str, code: str) -> str:
        self.logged_in = (ci, code)
        if self.login_error is not None:
            error, self.login_error = self.login_error, None
            raise error
        return "TOK"


class FakeAI:
    """Заглушка AIClient: готовый анализ, код капчи и ответ персонажа."""

    def __init__(
        self,
        analysis: ChatAnalysis | None = None,
        captcha_code: str | None = None,
        zov_reply: str = "база 🖤 ну типа да",
    ) -> None:
        self.analysis = analysis
        self.captcha_code = captcha_code
        self.zov_reply = zov_reply
        self.analyze_calls = 0
        self.captcha_calls = 0
        self.zov_calls: list[dict] = []

    async def analyze(self, **kwargs) -> ChatAnalysis | None:
        self.analyze_calls += 1
        return self.analysis

    async def solve_captcha(self, image: bytes) -> str | None:
        self.captcha_calls += 1
        return self.captcha_code

    async def zov(self, *, user_text: str, history: list[dict] | None = None, max_tokens: int = 600):
        self.zov_calls.append({"user_text": user_text, "history": list(history or [])})
        return self.zov_reply


async def test_publish_puts_homework_into_caption(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    photos: list[object] = []
    lycreg = FakeLycreg(items=[("Алгебра", "§ 5 № 1-10")])
    service, storage, sent, _ = await _service(
        tmp_path, reference, payload, photo=photos, lycreg=lycreg, photo_id=555
    )
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        text = await service.publish_tomorrow()
        assert text and len(photos) == 1
        card = photos[0]
        assert "<b>ДЗ на Пятницу 09.10</b>" in card.caption
        assert "Алгебра — § 5 № 1-10" in card.caption
        assert "<blockquote>" in card.caption
        assert "ДЗ на Пятницу 09.10" in text  # текст-фолбэк тоже знает про ДЗ
        assert lycreg.calls == 1
        assert sent == []
    finally:
        await storage.close()


async def test_publish_without_lycreg_sends_plain_card(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    photos: list[object] = []
    service, storage, sent, _ = await _service(tmp_path, reference, payload, photo=photos)
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        assert await service.publish_tomorrow()
        assert len(photos) == 1
        assert photos[0].caption == ""  # ни журнала — ни подписи
        assert sent == []
    finally:
        await storage.close()


async def test_publish_appends_homework_to_text_when_no_photo(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    sent: list[str] = []
    lycreg = FakeLycreg(items=[("Алгебра", "ДЗ")])
    service, storage, _, _ = await _service(tmp_path, reference, payload, send=sent, lycreg=lycreg)
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        text = await service.publish_tomorrow()  # пост ушёл текстом (фотоадаптера нет)
        assert text and "ДЗ на Пятницу 09.10" in text
        assert sent == [text]
        assert "• Алгебра — ДЗ" in text
        assert lycreg.calls == 1
    finally:
        await storage.close()


async def test_captcha_flow_admin_sends_code_then_homework_goes(tmp_path, reference, load_fixture):
    from bot.lycreg import CaptchaRequired

    payload = _payload(load_fixture)
    photos: list[object] = []
    admin_pics: list[tuple[bytes, str]] = []
    lycreg = FakeLycreg(
        items=[("Алгебра", "ДЗ после входа")],
        error=CaptchaRequired("ci-1", b"IMG"),
    )
    service, storage, sent, _ = await _service(
        tmp_path,
        reference,
        payload,
        photo=photos,
        lycreg=lycreg,
        admin_photos=admin_pics,
        photo_id=77,
    )

    async def admin_photo(png: bytes, caption: str) -> None:
        admin_pics.append((png, caption))
        await service.submit_captcha("4242")  # админ ответил кодом в личке

    service.admin_photo = admin_photo
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        assert await service.publish_tomorrow()  # пост ушёл, хотя журнал требовал капчу
        assert admin_pics and admin_pics[0][0] == b"IMG"
        assert "капч" in admin_pics[0][1].lower()
        assert lycreg.logged_in == ("ci-1", "4242")
        assert len(photos) == 1
        assert "ДЗ после входа" in photos[0].caption  # ДЗ дождалось входа и попало в подпись
        assert not service.captcha_pending
    finally:
        await storage.close()


async def test_captcha_without_admin_photo_gives_up_silently(tmp_path, reference, load_fixture):
    from bot.lycreg import CaptchaRequired

    payload = _payload(load_fixture)
    photos: list[object] = []
    lycreg = FakeLycreg(items=[("Алгебра", "ДЗ")], error=CaptchaRequired("ci", b"IMG"))
    service, storage, sent, _ = await _service(
        tmp_path, reference, payload, photo=photos, lycreg=lycreg, photo_id=9
    )
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        assert await service.publish_tomorrow()  # пост расписания не страдает
        assert photos[0].caption == ""  # без входа — без ДЗ
        assert lycreg.calls == 1  # без повторных попыток без админа
        assert not service.captcha_pending
    finally:
        await storage.close()


def test_target_date_for_homework_command():
    from datetime import date

    from bot.handlers import _target_date

    class _Now:
        def now(self):
            return datetime(2026, 10, 9, 12, 0)  # пятница

    service = _Now()
    assert _target_date(service) == date(2026, 10, 10)  # без аргумента — завтра
    assert _target_date(service, 6) == date(2026, 10, 10)  # сб — завтра же
    assert _target_date(service, 1) == date(2026, 10, 12)  # пн — через два дня
    assert _target_date(service, 5) == date(2026, 10, 9)  # пт — сегодня


def test_same_subject_matches_variants():
    assert _same_subject("математика", "математика")
    assert _same_subject("английский язык", "английский")
    assert not _same_subject("физика", "химия")
    assert not _same_subject("алгебра", "геометрия")


async def test_homework_items_merges_journal_with_chat(tmp_path, reference):
    lycreg = FakeLycreg(items=[("Алгебра", "§ 5 № 1-10")])
    service, storage, *_ = await _service(tmp_path, reference, {}, lycreg=lycreg)
    try:
        await storage.save_chat_homework("2026-10-09", "Информатика", "тетр. с. 5")
        await storage.save_chat_homework("2026-10-09", "Алгебра", "из чата")  # журнал приоритетнее
        lessons = [{"subject": "Алгебра"}, {"subject": "Информатика"}]
        items = await service.homework_items("10Н", lessons, date(2026, 10, 9))
        assert items == [("Алгебра", "§ 5 № 1-10"), ("Информатика", "тетр. с. 5")]
    finally:
        await storage.close()


async def test_homework_items_chat_only_when_journal_down(tmp_path, reference):
    from bot.lycreg import LycregError

    lycreg = FakeLycreg(error=LycregError("site down"))
    service, storage, *_ = await _service(tmp_path, reference, {}, lycreg=lycreg)
    try:
        await storage.save_chat_homework("2026-10-09", "Химия", "лаб. № 3")
        items = await service.homework_items("10Н", [], date(2026, 10, 9))
        assert items == [("Химия", "лаб. № 3")]
    finally:
        await storage.close()


async def test_homework_items_none_when_no_journal_and_no_chat(tmp_path, reference):
    from bot.lycreg import LycregError

    lycreg = FakeLycreg(error=LycregError("site down"))
    service, storage, *_ = await _service(tmp_path, reference, {}, lycreg=lycreg)
    try:
        assert await service.homework_items("10Н", [], date(2026, 10, 9)) is None
    finally:
        await storage.close()


async def test_publish_includes_chat_homework_when_journal_empty(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    photos: list[object] = []
    lycreg = FakeLycreg(items=[])  # вход есть, а ДЗ в журнале нет
    service, storage, *_ = await _service(
        tmp_path, reference, payload, photo=photos, lycreg=lycreg, photo_id=31
    )
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)  # пост про пятницу
        await storage.save_chat_homework("2026-10-09", "Обществознание", "конспект § 2")
        text = await service.publish_tomorrow()
        assert text and len(photos) == 1
        assert "Обществознание" in photos[0].caption
        assert "конспект § 2" in photos[0].caption
    finally:
        await storage.close()


async def test_captcha_auto_solved_by_ai(tmp_path, reference, load_fixture):
    from bot.lycreg import CaptchaRequired

    payload = _payload(load_fixture)
    photos: list[object] = []
    lycreg = FakeLycreg(items=[("Алгебра", "ДЗ после входа")], error=CaptchaRequired("ci-9", b"IMG"))
    ai = FakeAI(captcha_code="777")
    service, storage, *_ = await _service(
        tmp_path, reference, payload, photo=photos, lycreg=lycreg, ai=ai, photo_id=44
    )
    try:  # admin_photo не передан: раньше флоу был бы невозможен, теперь ИИ входит сам
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        assert await service.publish_tomorrow()
        assert ai.captcha_calls == 1
        assert lycreg.logged_in == ("ci-9", "777")
        assert "ДЗ после входа" in photos[0].caption
        assert not service.captcha_pending
    finally:
        await storage.close()


async def test_captcha_ai_wrong_code_falls_back_to_admin(tmp_path, reference, load_fixture):
    from bot.lycreg import CaptchaRequired, LycregError

    payload = _payload(load_fixture)
    photos: list[object] = []
    admin_pics: list[tuple[bytes, str]] = []
    lycreg = FakeLycreg(
        items=[("Алгебра", "ДЗ")],
        error=CaptchaRequired("ci-1", b"IMG"),
        login_error=LycregError("не тот код"),
    )
    ai = FakeAI(captcha_code="000")
    service, storage, *_ = await _service(
        tmp_path,
        reference,
        payload,
        photo=photos,
        lycreg=lycreg,
        ai=ai,
        admin_photos=admin_pics,
        photo_id=45,
    )

    async def admin_photo(png: bytes, caption: str) -> None:
        admin_pics.append((png, caption))
        await service.submit_captcha("4242")

    service.admin_photo = admin_photo
    try:
        service.now = lambda: datetime(2026, 10, 8, 14, 0, tzinfo=service.tz)
        assert await service.publish_tomorrow()
        assert ai.captcha_calls == 1
        assert admin_pics and admin_pics[0][0] == b"IMG"  # ИИ ошибся — пошли админу
        assert lycreg.logged_in == ("ci-1", "4242")
        assert "ДЗ" in photos[0].caption
    finally:
        await storage.close()


async def test_handle_chat_message_saves_homework_and_replies(tmp_path, reference):
    analysis = ChatAnalysis(
        homework=[HomeworkDraft(date(2026, 10, 12), "Алгебра", "§ 5 № 1-10")],
        reply="На понедельник: Алгебра § 5 № 1-10",
    )
    ai = FakeAI(analysis=analysis)
    service, storage, *_ = await _service(tmp_path, reference, {}, ai=ai)
    try:
        reply = await service.handle_chat_message(
            text="какое дз по алгебре на завтра?", msg_id=99, addressed=True
        )
        assert reply == "На понедельник: Алгебра § 5 № 1-10"
        assert ai.analyze_calls == 1
        assert await storage.get_chat_homework("2026-10-12") == [("Алгебра", "§ 5 № 1-10")]
    finally:
        await storage.close()


async def test_handle_chat_message_without_ai_is_noop(tmp_path, reference):
    service, *_ = await _service(tmp_path, reference, {})  # ai=None
    assert await service.handle_chat_message(text="какое дз?") is None


async def test_handle_chat_message_skips_too_short(tmp_path, reference):
    ai = FakeAI(analysis=ChatAnalysis(reply="ответ"))
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    assert await service.handle_chat_message(text="  a  ") is None
    assert await service.handle_chat_message(text="") is None
    assert ai.analyze_calls == 0, "короткий мусор не должен жечь вызовы ИИ"


async def test_handle_chat_message_no_reply_returns_none(tmp_path, reference):
    ai = FakeAI(analysis=ChatAnalysis(homework=[], reply=None))
    service, storage, *_ = await _service(tmp_path, reference, {}, ai=ai)
    try:
        reply = await service.handle_chat_message(text="вообще просто болтовня в чате", addressed=False)
        assert reply is None
        assert await storage.get_chat_homework("2026-10-12") == []
    finally:
        await storage.close()


async def test_handle_chat_message_fallback_when_ai_fails(tmp_path, reference):
    ai = FakeAI(analysis=None)  # все модели вернули мусор / сбой HTTP
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    reply = await service.handle_chat_message(text="мяу какое дз?", addressed=True)
    assert reply and "не получилось" in reply, "на обращение бот обязан ответить даже при сбое ИИ"


async def test_handle_chat_message_hint_when_addressed_without_content(tmp_path, reference):
    ai = FakeAI(analysis=ChatAnalysis(homework=[], reply=None))
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    reply = await service.handle_chat_message(text="мяу", addressed=True)
    assert reply and "На связи" in reply


async def test_handle_chat_message_busy_lock_stays_quiet_for_banter(tmp_path, reference):
    ai = FakeAI(analysis=ChatAnalysis(reply="ок"))
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    async with service._ai_lock:
        assert await service.handle_chat_message(text="просто болтовня какая-то", addressed=False) is None
        busy = await service.handle_chat_message(text="мяу как дела?", addressed=True)
        assert busy and "секунду" in busy


async def test_zov_reply_keeps_session_history(tmp_path, reference):
    ai = FakeAI(zov_reply="своих не бросаем 🔥")
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    assert not service.zov_active(-1001)
    reply = await service.zov_reply(chat_id=-1001, text="привет, Алина!")
    assert reply == "своих не бросаем 🔥"
    assert service.zov_active(-1001)
    await service.zov_reply(chat_id=-1001, text="как дела?")
    # второй запрос увидел историю первого диалога
    assert len(ai.zov_calls) == 2
    assert ai.zov_calls[1]["history"] == [
        {"role": "user", "content": "привет, Алина!"},
        {"role": "assistant", "content": "своих не бросаем 🔥"},
    ]


async def test_zov_reset_ends_session(tmp_path, reference):
    ai = FakeAI()
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    await service.zov_reply(chat_id=-1001, text="йоу")
    service.zov_reset(-1001)
    assert not service.zov_active(-1001)
    await service.zov_reply(chat_id=-1001, text="йоу снова")
    assert ai.zov_calls[1]["history"] == [], "после сброса история пуста"


async def test_zov_reply_without_ai_is_none(tmp_path, reference):
    service, *_ = await _service(tmp_path, reference, {})  # ai=None
    assert await service.zov_reply(chat_id=-1001, text="привет") is None
    assert not service.zov_active(-1001)


async def test_zov_session_expires_by_ttl(tmp_path, reference):
    ai = FakeAI()
    service, *_ = await _service(tmp_path, reference, {}, ai=ai)
    await service.zov_reply(chat_id=-1001, text="привет")
    service._zov[-1001].updated_at -= 10_000  # симулируем паузу дольше ZOZ_TTL
    assert not service.zov_active(-1001)
    await service.zov_reply(chat_id=-1001, text="снова")
    assert ai.zov_calls[1]["history"] == [], "история начинается заново после паузы"


async def test_ai_context_lists_subjects_and_hw(tmp_path, reference, load_fixture):
    payload = _payload(load_fixture)
    service, storage, *_ = await _service(tmp_path, reference, payload)
    try:
        await service.poll()  # снимок расписания — источник уроков
        service.now = lambda: datetime(2026, 10, 10, 12, 0, tzinfo=service.tz)  # суббота
        await storage.save_chat_homework("2026-10-10", "Физика", "№ 42")
        context = await service.ai_context(days=2)
        assert "10.10" in context
        assert "Физика: № 42" in context  # чат-ДЗ попало в контекст
        assert "География: —" in context  # урок без ДЗ помечается прочерком
        assert "занятий нет" in context  # 11.10 — воскресенье, уроков нет
    finally:
        await storage.close()
