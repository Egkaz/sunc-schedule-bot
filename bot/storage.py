"""Хранилище состояния бота (SQLite)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

from bot.differ import Snapshot, coerce_snapshot

log = logging.getLogger(__name__)

KEY_SNAPSHOT = "snapshot"
KEY_LAST_OK = "last_ok"
KEY_FAIL_STREAK = "fail_streak"
KEY_SNAPSHOT_AT = "snapshot_at"
KEY_THREAD_ID = "thread_id"
KEY_LYCREG_TOKEN = "lycreg_token"


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS chat_homework ("
            "day TEXT NOT NULL, subject TEXT NOT NULL, text TEXT NOT NULL, "
            "msg_id INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, "
            "PRIMARY KEY (day, subject))"
        )
        await self._db.commit()
        log.info("хранилище открыто: %s", self.path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("хранилище не открыто")
        return self._db

    async def get(self, key: str) -> str | None:
        async with self._conn().execute("SELECT value FROM kv WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
        return str(row["value"]) if row else None

    async def set(self, key: str, value: str) -> None:
        await self._conn().execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn().commit()

    async def get_int(self, key: str, default: int = 0) -> int:
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    async def get_optional_int(self, key: str) -> int | None:
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    async def set_int(self, key: str, value: int) -> None:
        await self.set(key, str(value))

    async def get_snapshot(self) -> Snapshot | None:
        raw = await self.get(KEY_SNAPSHOT)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("повреждён снимок в БД, пропускаю")
            return None
        return coerce_snapshot(data if isinstance(data, dict) else None)

    async def save_snapshot(self, snapshot: Snapshot, *, at: datetime | None = None) -> None:
        payload = {str(day): entries for day, entries in snapshot.items()}
        await self.set(KEY_SNAPSHOT, json.dumps(payload, ensure_ascii=False))
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        await self.set(KEY_SNAPSHOT_AT, stamp)

    async def save_chat_homework(self, day: str, subject: str, text: str, msg_id: int = 0) -> None:
        """ДЗ из чата класса: день (YYYY-MM-DD) + предмет -> текст."""
        await self._conn().execute(
            "INSERT INTO chat_homework (day, subject, text, msg_id, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(day, subject) DO UPDATE SET "
            "text = excluded.text, msg_id = excluded.msg_id, created_at = excluded.created_at",
            (day, subject, text, msg_id, datetime.now().isoformat(timespec="seconds")),
        )
        await self._conn().commit()

    async def get_chat_homework(self, day: str) -> list[tuple[str, str]]:
        """[(предмет, текст)] — ДЗ из чата на день (последняя запись предмета)."""
        async with self._conn().execute(
            "SELECT subject, text FROM chat_homework WHERE day = ? ORDER BY subject", (day,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [(str(row["subject"]), str(row["text"])) for row in rows]
