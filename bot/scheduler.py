"""Планировщик задач: опрос и ежедневная публикация."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from bot.config import Config
from bot.service import ScheduleService

log = logging.getLogger(__name__)

DAILY_DAYS = "sun,mon,tue,wed,thu,fri"


def build_scheduler(service: ScheduleService, config: Config) -> AsyncIOScheduler:
    try:
        timezone = ZoneInfo(config.tz)
    except Exception:  # noqa: BLE001
        log.warning("не удалось загрузить часовой пояс %s, беру UTC", config.tz)
        timezone = ZoneInfo("UTC")

    scheduler = AsyncIOScheduler(timezone=timezone, job_defaults={"coalesce": True, "max_instances": 1})

    scheduler.add_job(
        service.poll,
        trigger=IntervalTrigger(minutes=config.poll_minutes),
        id="poll",
        name="Опрос расписания",
        replace_existing=True,
        misfire_grace_time=config.poll_minutes * 60,
    )
    scheduler.add_job(
        service.publish_tomorrow,
        trigger=CronTrigger(hour=config.daily_hour, minute=0, day_of_week=DAILY_DAYS, timezone=timezone),
        id="daily",
        name="Пост расписания на завтра",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    return scheduler
