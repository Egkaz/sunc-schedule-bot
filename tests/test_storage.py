from __future__ import annotations

from datetime import datetime

from bot.storage import KEY_FAIL_STREAK, KEY_LAST_OK, Storage
from bot.normalize import normalize_day


async def test_snapshot_roundtrip(tmp_path, load_fixture):
    storage = Storage(tmp_path / "bot.db")
    await storage.open()
    try:
        assert await storage.get_snapshot() is None

        data = load_fixture("group_10n_wd4.json")
        day = normalize_day(data["lessons"], data.get("diffs"))
        await storage.save_snapshot({4: day}, at=datetime(2026, 10, 8, 18, 3))

        restored = await storage.get_snapshot()
        assert set(restored) == {4}
        assert restored[4][0]["subject"] == "Алгебра"
        assert await storage.get("snapshot_at") == "2026-10-08T18:03:00"
    finally:
        await storage.close()


async def test_int_values_and_defaults(tmp_path):
    storage = Storage(tmp_path / "bot.db")
    await storage.open()
    try:
        assert await storage.get_int(KEY_FAIL_STREAK, 0) == 0
        await storage.set_int(KEY_FAIL_STREAK, 3)
        assert await storage.get_int(KEY_FAIL_STREAK, 0) == 3

        assert await storage.get(KEY_LAST_OK) is None
        await storage.set(KEY_LAST_OK, "2026-10-08T18:03:00")
        assert await storage.get(KEY_LAST_OK) == "2026-10-08T18:03:00"
    finally:
        await storage.close()


async def test_optional_int_handles_missing_and_garbage(tmp_path):
    storage = Storage(tmp_path / "bot.db")
    await storage.open()
    try:
        assert await storage.get_optional_int("thread_id") is None
        await storage.set("thread_id", "777")
        assert await storage.get_optional_int("thread_id") == 777
        await storage.set("thread_id", "не число")
        assert await storage.get_optional_int("thread_id") is None
    finally:
        await storage.close()


async def test_storage_survives_reopen(tmp_path):
    path = tmp_path / "bot.db"
    first = Storage(path)
    await first.open()
    await first.set(KEY_LAST_OK, "2026-10-08T18:03:00")
    await first.close()

    second = Storage(path)
    await second.open()
    try:
        assert await second.get(KEY_LAST_OK) == "2026-10-08T18:03:00"
    finally:
        await second.close()
