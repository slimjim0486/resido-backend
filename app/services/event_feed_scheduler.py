"""In-app reliability fallback for the daily "What's On" feed refresh.

Railway cron services are still the preferred operational shape, but this keeps
the feed fresh when the cron service is missing, disabled, or delayed. A Postgres
advisory lock makes this safe alongside a real cron service or future replicas.
"""

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from app.config import settings
from app.core.logging import get_logger
from app.database import async_session_maker
from app.models.event import Event

logger = get_logger(__name__)

_DUBAI_TZ = ZoneInfo("Asia/Dubai")
_LOCK_KEY = 860_202_605_270
_STALE_RETRY_SECONDS = 300


def _next_run_at(now: datetime | None = None) -> datetime:
    current = now or datetime.now(_DUBAI_TZ)
    target = current.replace(
        hour=settings.EVENTS_FEED_RUN_HOUR_DUBAI,
        minute=0,
        second=0,
        microsecond=0,
    )
    if target <= current:
        target += timedelta(days=1)
    return target


async def _feed_is_stale() -> bool:
    async with async_session_maker() as session:
        result = await session.execute(select(func.max(Event.fetched_at)))
        latest = result.scalar_one_or_none()
    if latest is None:
        return True
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - latest
    return age >= timedelta(hours=settings.EVENTS_FEED_STALE_AFTER_HOURS)


async def refresh_events_feed_locked(*, reason: str) -> bool:
    """Run scripts.seed_events once if no other refresh currently holds the lock."""
    async with async_session_maker() as lock_session:
        result = await lock_session.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}
        )
        if not result.scalar_one():
            logger.info("events_feed_refresh_skipped_locked", reason=reason)
            return False

        try:
            from scripts.seed_events import main as seed_events_main

            logger.info("events_feed_refresh_started", reason=reason)
            await seed_events_main()
            logger.info("events_feed_refresh_finished", reason=reason)
            return True
        except Exception as exc:
            logger.warning("events_feed_refresh_failed", reason=reason, error=str(exc))
            return False
        finally:
            await lock_session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY}
            )


async def _run_loop() -> None:
    while await _feed_is_stale():
        ran = await refresh_events_feed_locked(reason="startup_stale")
        if ran or not await _feed_is_stale():
            break
        logger.info(
            "events_feed_refresh_retry_scheduled",
            retry_in_seconds=_STALE_RETRY_SECONDS,
        )
        await asyncio.sleep(_STALE_RETRY_SECONDS)
    else:
        logger.info("events_feed_refresh_startup_fresh")

    while True:
        next_run = _next_run_at()
        delay = max(1.0, (next_run - datetime.now(_DUBAI_TZ)).total_seconds())
        logger.info("events_feed_refresh_scheduled", next_run_at=next_run.isoformat())
        await asyncio.sleep(delay)
        await refresh_events_feed_locked(reason="daily_schedule")


def start_event_feed_scheduler() -> asyncio.Task | None:
    if not settings.EVENTS_FEED_AUTORUN:
        logger.info("events_feed_refresh_disabled")
        return None
    return asyncio.create_task(_run_loop(), name="events-feed-refresh")


async def stop_event_feed_scheduler(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
