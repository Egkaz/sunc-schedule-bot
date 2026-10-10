"""Клиент журнала lycreg.urfu.ru: вход с капчей, получение ДЗ по урокам дня."""

from __future__ import annotations

import json
import logging
import re
import ssl
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from bot.differ import NO_LESSON
from bot.storage import KEY_LYCREG_TOKEN, Storage

log = logging.getLogger(__name__)

BASE_URL = "https://lycreg.urfu.ru"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": BASE_URL + "/",
    "Origin": BASE_URL,
}
SUBJECT_MATCH_THRESHOLD = 0.55
SUBJECT_CONTAIN_MIN = 4  # мин. длина для совпадения «подстрока в названии»
HOMEWORK_MAX_AGE_DAYS = 10  # ДЗ старше этого срока не показываем («трёхнедельное»)
JOURNAL_TTL = 600.0  # секунд кэша журнала (ИИ-контекст не должен долбить lycreg)

# Словарь предметов сайта (subjDef из ini.js); дополняется ответом subjList
SUBJ_DEF = {
    "s110": "Русский язык",
    "s120": "Литература",
    "s210": "Английский язык",
    "s220": "Немецкий язык",
    "s230": "Французский язык",
    "s310": "Искусство",
    "s320": "МХК",
    "s330": "Музыка",
    "s410": "Математика",
    "s420": "Алгебра",
    "s430": "Алгебра и начала анализа",
    "s440": "Геометрия",
    "s450": "Вероятность и статистика",
    "s460": "Информатика",
    "s510": "История",
    "s520": "История России",
    "s530": "Всеобщая история",
    "s540": "Обществознание",
    "s550": "Экономика",
    "s560": "Право",
    "s570": "География",
    "s610": "Физика",
    "s620": "Астрономия",
    "s630": "Химия",
    "s640": "Биология",
    "s710": "Технология",
    "s810": "Физическая культура",
    "s820": "ОБЗР",
}


class LycregError(RuntimeError):
    """Ошибка журнала (сеть, битый ответ, неверный вход)."""


class CaptchaRequired(LycregError):
    """Токен невалиден — нужен вход с капчей."""

    def __init__(self, ci: str, image: bytes) -> None:
        super().__init__("требуется вход в журнал (капча)")
        self.ci = ci
        self.image = image


def journal_date_key(day: date) -> str:
    """Дата журнала: d + месяц (сентябрь=0) + день, как в dateConv сайта."""
    month_pos = day.month - 9 if day.month > 8 else day.month + 3
    return f"d{month_pos}{day.day:02d}"


def date_position(day: date) -> int:
    """Позиция даты внутри учебного года (сентябрь→декабрь перед январём)."""
    if day.month >= 9:
        return day.month * 100 + day.day
    return day.month * 100 + day.day + 1300


def key_position(key: str) -> int | None:
    """Обратное преобразование dMMDD → позиция в учебном году."""
    if len(key) != 4 or not key.startswith("d") or not key[1:].isdigit():
        return None
    month_pos = int(key[1])
    day = int(key[2:4])
    month = month_pos + 9 if month_pos <= 3 else month_pos - 3
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    return date_position(date(2000, month, day))


def pos_date(pos: int) -> date | None:
    """Обратное преобразование позиции учебного года → календарная дата."""
    if pos < 901:
        return None
    if pos >= 1400:
        pos -= 1300
    month, day = divmod(pos, 100)
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    try:
        return date(2000, month, day)
    except ValueError:
        return None


def _norm_fio(value: str) -> str:
    """ФИО без учёта регистра и пробелов: «И.С.» == «И. С.»."""
    return "".join(value.split()).casefold()


def _norm_login(value: str) -> str:
    return re.sub(r"\d+$", "", value.strip()).casefold()


def pick_homework(
    journal: dict,
    klass: str,
    *,
    subject: str,
    teacher: str,
    target_pos: int,
    teach_by_fio: dict[str, str],
    subj_names: dict[str, str],
) -> str | None:
    """ДЗ урока target: задано на последнем записанном уроке предмета и не старое.

    Пустая свежая запись означает «ДЗ не задано» — трёхнедельную старь не тащим.
    """
    want_login = teach_by_fio.get(_norm_fio(teacher))
    if want_login is None and "/" in teacher:
        for part in teacher.split("/"):
            want_login = teach_by_fio.get(_norm_fio(part))
            if want_login:
                break

    candidates: list[tuple[str, str]] = []  # (ключ, код предмета)
    for key in journal:
        parts = key.split("_", 2)
        if len(parts) != 3 or parts[0].split("-")[0] != klass:
            continue
        if want_login is not None and _norm_login(parts[2]) == _norm_login(want_login):
            candidates.append((key, parts[1]))

    if not candidates and subject:
        subject_cf = subject.casefold()
        scored: list[tuple[float, tuple[str, str]]] = []
        for key in journal:
            parts = key.split("_", 2)
            if len(parts) != 3 or parts[0].split("-")[0] != klass:
                continue
            name = subj_names.get(parts[1], "").casefold()
            if not name:
                continue
            ratio = SequenceMatcher(None, name, subject_cf).ratio()
            shorter, longer = sorted((name, subject_cf), key=len)
            if ratio >= SUBJECT_MATCH_THRESHOLD or (
                len(shorter) >= SUBJECT_CONTAIN_MIN and shorter in longer
            ):
                scored.append((ratio, (key, parts[1])))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            candidates = [scored[0][1]]

    if not candidates:
        return None
    if len(candidates) > 1 and subject:
        candidates = [
            max(
                candidates,
                key=lambda item: SequenceMatcher(
                    None, subj_names.get(item[1], "").casefold(), subject.casefold()
                ).ratio(),
            )
        ]

    best_pos, best_hw = -1, None
    last_pos = -1
    for dkey, values in journal[candidates[0][0]].items():
        pos = key_position(dkey)
        if pos is None or pos >= target_pos or not values:
            continue
        hw = str(values[1] if len(values) > 1 else "").strip()
        if pos > last_pos:
            last_pos = pos
        if hw and pos > best_pos:
            best_pos, best_hw = pos, hw
    if best_hw is None or best_pos != last_pos:
        return None  # на последнем уроке ДЗ не задано (или записей вовсе нет)
    target_date, hw_date = pos_date(target_pos), pos_date(best_pos)
    if target_date is None or hw_date is None:
        return None
    if (target_date - hw_date).days > HOMEWORK_MAX_AGE_DAYS:
        log.debug("журнал: ДЗ по %s задано %s дн. назад — пропускаю", subject, (target_date - hw_date).days)
        return None
    return best_hw


CA_PIN_PATH = Path(__file__).with_name("lycreg_ca.pem")


def ssl_context() -> ssl.SSLContext:
    """TLS для lycreg: стандартный набор + свои intermediate.

    Сервер отдаёт только leaf-сертификат (промежуточные не шлёт), поэтому вне
    Windows (без AIA-догрузки) нужен пин: см. bot/lycreg_ca.pem.
    """
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - certifi необязателен, берём системный набор
        context = ssl.create_default_context()
    if CA_PIN_PATH.exists():
        try:
            context.load_verify_locations(cafile=str(CA_PIN_PATH))
        except (ssl.SSLError, OSError):
            log.warning("не удалось подгрузить пин сертификата %s", CA_PIN_PATH)
    return context


class LycregClient:
    def __init__(
        self,
        login: str,
        password: str,
        *,
        storage: Storage | None = None,
        http: httpx.AsyncClient | None = None,
        category: str = "pupil",
    ) -> None:
        self.username = login
        self.password = password
        self.storage = storage
        self.category = category
        self._client = http
        self._teach_by_fio: dict[str, str] | None = None
        self._subj_names: dict[str, str] | None = None
        self._journal_cache: dict | None = None
        self._journal_at: float = 0.0

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=20.0, headers=HEADERS, verify=ssl_context()
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, payload: dict) -> str:
        try:
            response = await self._http().post(
                BASE_URL + "/",
                content=json.dumps(payload, ensure_ascii=False),
                headers={"Content-Type": "text/plain;charset=UTF-8"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LycregError(f"запрос к журналу не удался: {exc}") from exc
        return response.text

    async def _fetch_captcha(self) -> tuple[str, bytes]:
        try:
            response = await self._http().get(BASE_URL + "/cpt.a")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LycregError(f"не удалось получить капчу: {exc}") from exc
        ci = (response.headers.get("X-Cpt") or "").strip()
        if not ci:
            raise LycregError("в ответе на капчу нет заголовка X-Cpt")
        return ci, response.content

    async def login(self, ci: str, code: str) -> str:
        resp = await self._post(
            {
                "t": self.category,
                "l": self.username,
                "p": self.password,
                "f": "login",
                "ci": ci,
                "c": code.strip(),
            }
        )
        if resp.strip() == "none":
            raise LycregError("журнал не принял логин/пароль/код капчи")
        try:
            token = json.loads(resp)["token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LycregError("неожиданный ответ входа в журнал") from exc
        if self.storage is not None:
            await self.storage.set(KEY_LYCREG_TOKEN, str(token))
        log.info("журнал lycreg: вход выполнен")
        return str(token)

    async def _token(self) -> str | None:
        if self.storage is None:
            return None
        token = await self.storage.get(KEY_LYCREG_TOKEN)
        return token or None

    async def journal(self) -> dict:
        """Дневник (с кэшем JOURNAL_TTL); при истёкшем токене — CaptchaRequired."""
        if self._journal_cache is not None and time.monotonic() - self._journal_at < JOURNAL_TTL:
            return self._journal_cache
        data = await self._fetch_journal()
        self._journal_cache = data
        self._journal_at = time.monotonic()
        return data

    async def _fetch_journal(self) -> dict:
        token = await self._token()
        if token:
            resp = await self._post(
                {"t": self.category, "l": self.username, "p": token, "f": "jrnGet", "z": []}
            )
            if resp.strip() != "none":
                try:
                    data = json.loads(resp)
                except ValueError as exc:
                    raise LycregError("журнал вернул битый JSON") from exc
                if isinstance(data, dict):
                    return data
                raise LycregError("неожиданный формат дневника")
            if self.storage is not None:
                await self.storage.set(KEY_LYCREG_TOKEN, "")
            log.info("журнал lycreg: токен истёк, нужна капча")
        ci, image = await self._fetch_captcha()
        raise CaptchaRequired(ci, image)

    async def _load_meta(self) -> None:
        token = await self._token()
        if not token:
            raise LycregError("нет токена журнала")
        teach = await self._post({"t": self.category, "l": self.username, "p": token, "f": "teachList"})
        subj = await self._post({"t": self.category, "l": self.username, "p": token, "f": "subjList"})
        try:
            teach_list = json.loads(teach)
            subj_names = json.loads(subj)
        except ValueError as exc:
            raise LycregError("битый ответ справочников журнала") from exc
        self._teach_by_fio = {
            _norm_fio(item.get("fio", "")): item.get("login", "")
            for item in teach_list
            if isinstance(item, dict) and item.get("fio")
        }
        self._subj_names = {**SUBJ_DEF, **(dict(subj_names) if isinstance(subj_names, dict) else {})}

    async def ensure_meta(self) -> None:
        """Справочники преподавателей и предметов (лениво, один раз)."""
        if self._teach_by_fio is None or self._subj_names is None:
            await self._load_meta()

    def pick_for_day(
        self, journal: dict, klass: str, lessons: list[dict], target: date
    ) -> list[tuple[str, str]]:
        """[(предмет, ДЗ)] для уроков дня target; пустые ДЗ пропускаются.

        Журнал и справочники должны быть загружены (journal() + ensure_meta()).
        """
        assert self._teach_by_fio is not None and self._subj_names is not None

        target_pos = date_position(target)
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for lesson in lessons:
            subject = str(lesson.get("subject") or "").strip()
            if not subject or subject == NO_LESSON or subject in seen:
                continue
            teacher = str(lesson.get("teacher") or "").strip()
            hw = pick_homework(
                journal,
                klass,
                subject=subject,
                teacher=teacher,
                target_pos=target_pos,
                teach_by_fio=self._teach_by_fio,
                subj_names=self._subj_names,
            )
            if hw:
                items.append((subject, hw))
                seen.add(subject)
        return items

    async def homework(
        self, klass: str, lessons: list[dict], target: date
    ) -> list[tuple[str, str]]:
        """[(предмет, ДЗ)] для уроков дня target; пустые ДЗ пропускаются."""
        journal = await self.journal()
        await self.ensure_meta()
        return self.pick_for_day(journal, klass, lessons, target)
