"""Карточка расписания в виде таблицы — как на сайте СУНЦ (lyceum.urfu.ru).

Раскладка и цвета сняты со стилей `.schedule .schedule-block table`:

* шапка — зелёная ``#339C33``, белый текст, две колонки «Номер урока» и «Урок»;
* ячейки — рамка ``#dadada``, чередование строк, выравнивание по центру;
* изменённые уроки — оранжевый ``#ff8a37`` (класс ``attention``);
* строки всегда 1..7, как в разметке сайта; пустой день — «Занятий нет».
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from bot.days import lesson_time, day_name, day_name_acc
from bot.formatter import format_day, format_entity_day

log = logging.getLogger(__name__)

WIDTH = 900
PADDING = 26
LESSON_COUNT = 7

HEAD_BG = (51, 156, 55)  # #339C33
HEAD_FG = (255, 255, 255)
CELL_BORDER = (218, 218, 218)  # #dadada
STRIPE = (247, 247, 247)  # rgba(0,0,0,.05)
ATTENTION = (255, 138, 55)  # #ff8a37
TEXT_FILL = (32, 32, 32)
MUTED_FILL = (128, 128, 128)
TITLE_FILL = (27, 58, 116)
EMPTY_FILL = (250, 250, 250)

# порядок полей в ячейке — как в custom.js сайта (subject, auditory, teacher / group)
SITE_GROUP_FIELDS = ("subject", "room", "teacher")
SITE_TEACHER_FIELDS = ("subject", "room", "klass")
SITE_AUDITORY_FIELDS = ("subject", "teacher", "klass")

TITLE_SIZE = 30
HEAD_SIZE = 17
NUMBER_SIZE = 26
TIME_SIZE = 15
CELL_SIZE = 19
NOTE_SIZE = 15

NUMBER_COL = 210
HEAD_HEIGHT = 54
MIN_ROW_HEIGHT = 62
CELL_PAD = 14
LINE_STEP = 25

_REGULAR = ("segoeui.ttf", "arial.ttf", "tahoma.ttf", "DejaVuSans.ttf", "FreeSans.ttf")
_BOLD = ("segoeuib.ttf", "arialbd.ttf", "tahomabd.ttf", "DejaVuSans-Bold.ttf", "FreeSansBold.ttf")


@dataclass(frozen=True)
class LessonRow:
    number: int
    time: str
    text: str
    change: bool = False


@dataclass(frozen=True)
class ScheduleCard:
    title: str
    rows: tuple[LessonRow, ...]
    note: str = ""
    text: str = ""
    caption: str = ""

    @property
    def has_lessons(self) -> bool:
        return any(row.text for row in self.rows)


def _cell_text(entry: dict, fields: tuple[str, ...]) -> str:
    parts = [str(entry.get(name) or "").strip() for name in fields]
    return ", ".join(part for part in parts if part)


def _rows(entries: list[dict], fields: tuple[str, ...]) -> tuple[LessonRow, ...]:
    by_number = {}
    for entry in entries:
        try:
            by_number[int(entry.get("lesson", 0))] = entry
        except (TypeError, ValueError):
            continue
    rows: list[LessonRow] = []
    for number in range(1, LESSON_COUNT + 1):
        entry = by_number.get(number)
        if entry is None:
            rows.append(LessonRow(number, lesson_time(number), ""))
            continue
        rows.append(
            LessonRow(
                number=number,
                time=str(entry.get("time") or lesson_time(number)),
                text=_cell_text(entry, fields),
                change=bool(entry.get("diff")),
            )
        )
    return tuple(rows)


def group_card(
    klass: str,
    api_wd: int,
    entries: list[dict],
    *,
    note: str = "",
    caption: str = "",
) -> ScheduleCard:
    title = f"Расписание на {day_name_acc(api_wd)} {klass}".strip()
    text = format_day(klass, api_wd, entries)
    if note.strip():
        text = f"{text}\n{note.strip()}"
    return ScheduleCard(title, _rows(entries, SITE_GROUP_FIELDS), note.strip(), text, caption)


def entity_card(
    entity: str,
    api_wd: int,
    entries: list[dict],
    fields: tuple[str, ...],
    *,
    note: str = "",
) -> ScheduleCard:
    title = f"{entity}, {day_name(api_wd)}".strip()
    text = format_entity_day(entity, api_wd, entries, fields)
    if note.strip():
        text = f"{text}\n{note.strip()}"
    return ScheduleCard(title, _rows(entries, fields), note.strip(), text)


def changes_card(
    klass: str,
    api_wd: int,
    entries: list[dict],
    changed: set[int],
    *,
    caption: str = "",
    text: str = "",
) -> ScheduleCard:
    """Таблица дня с оранжевыми строками изменившихся уроков (заголовок — про изменения)."""
    card = group_card(klass, api_wd, entries)
    rows = tuple(
        LessonRow(row.number, row.time, row.text, change=True) if row.number in changed else row
        for row in card.rows
    )
    title = f"Изменения в расписании на {day_name_acc(api_wd)} {klass}".strip()
    return ScheduleCard(title, rows, card.note, text or card.text, caption)


def _font_dirs() -> list[Path]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return [
        windir / "Fonts",
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/truetype/freefont"),
        Path("/usr/share/fonts"),
    ]


def _load_font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    for directory in _font_dirs():
        for name in names:
            path = directory / name
            if path.is_file():
                return ImageFont.truetype(str(path), size)
    raise FileNotFoundError(f"шрифт не найден, перебрано: {', '.join(names)}")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _centered(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, font: ImageFont.FreeTypeFont, fill) -> None:
    width = draw.textlength(text, font=font)
    draw.text((cx - width / 2, y), text, font=font, fill=fill)


def render_card(card: ScheduleCard) -> bytes:
    title_font = _load_font(_BOLD, TITLE_SIZE)
    head_font = _load_font(_BOLD, HEAD_SIZE)
    number_font = _load_font(_BOLD, NUMBER_SIZE)
    time_font = _load_font(_REGULAR, TIME_SIZE)
    cell_font = _load_font(_REGULAR, CELL_SIZE)
    note_font = _load_font(_REGULAR, NOTE_SIZE)

    inner = WIDTH - 2 * PADDING
    number_col = min(NUMBER_COL, inner // 3)
    lesson_col = inner - number_col

    probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10), (255, 255, 255)))
    title_width = int(probe.textlength(card.title, font=title_font))
    note_lines = _wrap(probe, card.note, note_font, inner) if card.note else []

    # высоты строк: пустые — минимум, с текстом — по количеству переносов
    row_lines: list[list[str]] = []
    row_heights: list[int] = []
    for row in card.rows:
        lines = _wrap(probe, row.text, cell_font, lesson_col - 2 * CELL_PAD)
        row_lines.append(lines)
        row_heights.append(max(MIN_ROW_HEIGHT, len(lines) * LINE_STEP + 2 * CELL_PAD))

    title_height = int(TITLE_SIZE * 1.45)
    note_height = len(note_lines) * (NOTE_SIZE + 6)
    note_gap = 14 if note_lines else 0
    table_height = HEAD_HEIGHT + sum(row_heights)

    if not card.has_lessons:
        table_height = 0
        empty_height = 84
    else:
        empty_height = 0

    height = (
        PADDING
        + title_height
        + 14
        + table_height
        + empty_height
        + note_gap
        + note_height
        + PADDING
    )

    image = Image.new("RGB", (WIDTH, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    left, right = PADDING, WIDTH - PADDING

    draw.text((PADDING, PADDING), card.title, font=title_font, fill=TITLE_FILL)
    if title_width > inner:
        log.warning("заголовок карточки шире таблицы: %s", card.title)
    y = PADDING + title_height + 14

    if card.has_lessons:
        # шапка
        draw.rectangle((left, y, left + number_col, y + HEAD_HEIGHT), fill=HEAD_BG, outline=HEAD_FG, width=1)
        draw.rectangle((left + number_col, y, right, y + HEAD_HEIGHT), fill=HEAD_BG, outline=HEAD_FG, width=1)
        _centered(draw, left + number_col // 2, y + (HEAD_HEIGHT - HEAD_SIZE) // 2 - 2, "Номер урока", head_font, HEAD_FG)
        _centered(draw, left + number_col + lesson_col // 2, y + (HEAD_HEIGHT - HEAD_SIZE) // 2 - 2, "Урок", head_font, HEAD_FG)
        y += HEAD_HEIGHT

        for index, (row, lines, row_height) in enumerate(zip(card.rows, row_lines, row_heights)):
            fill = STRIPE if index % 2 == 0 else (255, 255, 255)
            draw.rectangle((left, y, left + number_col, y + row_height), fill=fill, outline=CELL_BORDER, width=1)
            draw.rectangle((left + number_col, y, right, y + row_height), fill=fill, outline=CELL_BORDER, width=1)

            _centered(draw, left + number_col // 2, y + 8, str(row.number), number_font, TEXT_FILL)
            if row.time:
                _centered(draw, left + number_col // 2, y + 8 + NUMBER_SIZE + 6, row.time, time_font, MUTED_FILL)

            color = ATTENTION if row.change else TEXT_FILL
            block_height = len(lines) * LINE_STEP
            text_y = y + max(CELL_PAD, (row_height - block_height) // 2)
            cell_center = left + number_col + lesson_col // 2
            if not lines and row.change:
                # урок отменили: пустая ячейка, но видим, что тронуто именно её
                _centered(draw, cell_center, y + (row_height - CELL_SIZE) // 2, "✕", cell_font, ATTENTION)
            for line in lines:
                _centered(draw, cell_center, text_y, line, cell_font, color)
                text_y += LINE_STEP

            y += row_height
    else:
        draw.rectangle((left, y, right, y + empty_height), fill=EMPTY_FILL, outline=CELL_BORDER, width=1)
        _centered(draw, WIDTH // 2, y + (empty_height - CELL_SIZE) // 2, "Занятий нет", cell_font, MUTED_FILL)
        y += empty_height

    if note_lines:
        y += note_gap
        for line in note_lines:
            draw.text((PADDING, y), line, font=note_font, fill=MUTED_FILL)
            y += NOTE_SIZE + 6

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def schedule_photo(card: ScheduleCard) -> bytes | None:
    """PNG-карточка или None, если отрисовка невозможна (нет шрифта и т.п.)."""
    try:
        return render_card(card)
    except Exception:  # noqa: BLE001 - отправка текстом важнее красивой картинки
        log.warning("не удалось отрисовать карточку расписания", exc_info=True)
        return None


__all__ = [
    "LessonRow",
    "ScheduleCard",
    "SITE_AUDITORY_FIELDS",
    "SITE_GROUP_FIELDS",
    "SITE_TEACHER_FIELDS",
    "WIDTH",
    "changes_card",
    "entity_card",
    "group_card",
    "render_card",
    "schedule_photo",
]
