"""Клиент расписания СУНЦ (lyceum.urfu.ru).

Эндпоинт найден в JS-бандле /typo3conf/ext/sunc_schedule/Resources/Public/dist/custom.js
(функция ajaxSchedule):

    GET {base}/?type=11&scheduleType=group|teacher|auditory&weekday=1..6&group=<id>

Ответ — JSON в теле (Content-Type при этом text/html), без сессии, кук и заголовков:

    {"type":"group","lessons":[{...}],"diffs":[...]}

Справочники id классов/преподавателей/аудиторий лежат в <select data-name="...">
на самой странице /ucheba/raspisanie-zanjatii и парсятся оттуда.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

PAGE_PATH = "/ucheba/raspisanie-zanjatii"
DEFAULT_BASE_URL = "https://lyceum.urfu.ru"

_SELECT_RE = re.compile(r'<select[^>]*data-name="(\w+)"[^>]*>(.*?)</select>', re.S | re.I)
_OPTION_RE = re.compile(r'<option\s+value="([^"]*)"\s*>(.*?)</option>', re.S | re.I)

REFERENCE_TTL = 24 * 3600


class FetchError(RuntimeError):
    """Сайт расписания недоступен или ответ неожиданного вида."""


def _norm(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).lower().replace("ё", "е")


@dataclass(frozen=True)
class Reference:
    """Справочники id, полученные со страницы расписания."""

    groups: dict[str, int] = field(default_factory=dict)
    teachers: dict[str, int] = field(default_factory=dict)
    auditories: dict[str, int] = field(default_factory=dict)

    def group_id(self, name: str) -> int | None:
        key = _norm(name).replace(" ", "")
        for label, ident in self.groups.items():
            if _norm(label).replace(" ", "") == key:
                return ident
        return None

    def teacher_matches(self, query: str, limit: int = 6) -> list[tuple[str, int]]:
        """Преподаватели, у которых фамилия (или вся строка) совпала с запросом."""
        key = _norm(query)
        if not key:
            return []
        exact: list[tuple[str, int]] = []
        prefix: list[tuple[str, int]] = []
        for label, ident in self.teachers.items():
            norm_label = _norm(label)
            surname = norm_label.split(" ")[0] if norm_label else ""
            if norm_label == key or surname == key:
                exact.append((label, ident))
            elif norm_label.startswith(key) or key in norm_label:
                prefix.append((label, ident))
        found = exact or prefix
        return found[:limit]

    def teacher_id(self, query: str) -> int | None:
        matches = self.teacher_matches(query, limit=2)
        return matches[0][1] if len(matches) == 1 else None

    def auditory_id(self, room: str) -> int | None:
        key = _norm(room).replace(" ", "")
        for label, ident in self.auditories.items():
            if _norm(label).replace(" ", "") == key:
                return ident
        return None


def parse_reference(page_html: str) -> Reference:
    """Разбор <select data-name="group|teacher|auditory"> со страницы расписания."""
    selects: dict[str, list[tuple[str, int]]] = {}
    for match in _SELECT_RE.finditer(page_html):
        name = match.group(1).lower()
        if name not in {"group", "teacher", "auditory"}:
            continue
        items: list[tuple[str, int]] = []
        for option in _OPTION_RE.finditer(match.group(2)):
            raw_id, label = option.group(1), html.unescape(option.group(2)).strip()
            if not raw_id or raw_id == "0" or not label:
                continue
            try:
                items.append((label, int(raw_id)))
            except ValueError:
                continue
        selects[name] = items

    for required in ("group", "teacher", "auditory"):
        if required not in selects:
            raise FetchError(f"на странице расписания не найден <select data-name=\"{required}\">")

    return Reference(
        groups=dict(selects["group"]),
        teachers=dict(selects["teacher"]),
        auditories=dict(selects["auditory"]),
    )


class ScheduleClient:
    """Асинхронный клиент API расписания с ретраями и кэшем справочника."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 15.0,
        retries: int = 3,
        backoff: float = 0.7,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self._client = client
        self._owns_client = client is None
        self._reference: Reference | None = None
        self._reference_at: float = 0.0
        self._ref_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "sunc-schedule-bot/1.0 (+lyceum.urfu.ru)"},
            )
        return self._client

    async def _get(self, path: str, params: dict[str, object] | None = None) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = await self._http().get(
                    self.base_url + path,
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    delay = self.backoff * (2**attempt)
                    log.warning("запрос %s не удался (%s), повтор через %.1f с", path, exc, delay)
                    await asyncio.sleep(delay)
        raise FetchError(f"запрос {path} не удался: {last_error}") from last_error

    async def _get_json(self, path: str, params: dict[str, object] | None = None) -> dict:
        response = await self._get(path, params)
        try:
            data = response.json()
        except ValueError as exc:
            raise FetchError(f"не JSON в ответе на {path}: {response.text[:120]!r}") from exc
        if not isinstance(data, dict):
            raise FetchError(f"неожиданный формат ответа на {path}")
        return data

    async def fetch_page(self) -> str:
        response = await self._get(PAGE_PATH)
        return response.text

    async def reference(self, *, force: bool = False) -> Reference:
        """Справочник классов/преподавателей/аудиторий (кэш на 24 часа)."""
        now = time.monotonic()
        if not force and self._reference is not None and now - self._reference_at < REFERENCE_TTL:
            return self._reference
        async with self._ref_lock:
            if not force and self._reference is not None and time.monotonic() - self._reference_at < REFERENCE_TTL:
                return self._reference
            page = await self.fetch_page()
            self._reference = parse_reference(page)
            self._reference_at = time.monotonic()
            log.info(
                "справочник обновлён: классов=%d, преподавателей=%d, аудиторий=%d",
                len(self._reference.groups),
                len(self._reference.teachers),
                len(self._reference.auditories),
            )
            return self._reference

    async def _schedule(
        self,
        schedule_type: str,
        weekday: int,
        extra: dict[str, object],
    ) -> tuple[list[dict], list[dict]]:
        params: dict[str, object] = {
            "type": 11,
            "scheduleType": schedule_type,
            "weekday": weekday,
        }
        params.update(extra)
        data = await self._get_json("/", params)
        lessons = data.get("lessons") or []
        diffs = data.get("diffs") or []
        if not isinstance(lessons, list) or not isinstance(diffs, list):
            raise FetchError(f"неожиданный формат lessons/diffs: {data!r}")
        return lessons, diffs

    async def fetch_group_day(self, group_id: int, weekday: int) -> tuple[list[dict], list[dict]]:
        return await self._schedule("group", weekday, {"group": group_id})

    async def fetch_teacher_day(self, teacher_id: int, weekday: int) -> tuple[list[dict], list[dict]]:
        return await self._schedule("teacher", weekday, {"teacher": teacher_id})

    async def fetch_auditory_day(self, auditory_id: int, weekday: int) -> tuple[list[dict], list[dict]]:
        return await self._schedule("auditory", weekday, {"auditory": auditory_id})
