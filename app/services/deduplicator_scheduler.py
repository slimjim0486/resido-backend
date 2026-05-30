"""In-app reliability fallback for daily content deduplication.

The Railway `deduplicator` cron service is still supported, but this prevents a
missing cron service from silently leaving duplicate events/providers visible.
An advisory lock keeps this safe alongside a real cron service or future
replicas.
"""

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.config import settings
from app.core.logging import get_logger
from app.database import async_session_maker
from app.services.deduplicator import run_deduplicator

logger = get_logger(__name__)

_DUBAI_TZ = ZoneInfo("Asia/Dubai")
_LOCK_KEY = 860_202_605_271


def _next_run_at(now: datetime | None = None) -> datetime:
    current = now or datetime.now(_DUBAI_TZ)
    target = current.replace(
        hour=settings.DEDUPLICATOR_RUN_HOUR_DUBAI,
        minute=0,
        second=0,
        microsecond=0,
    )
    if target <= current:
        target += timedelta(days=1)
    return target


async def run_deduplicator_locked(*, reason: str, scope: str = "all") -> bool:
    """Run dedupe once if no other dedupe currently holds the lock."""
    async with async_session_maker() as session:
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}
        )
        if not result.scalar_one():
            logger.info("deduplicator_skipped_locked", reason=reason)
            return False

        try:
            logger.info(
                "deduplicator_started",
                reason=reason,
                scope=scope,
                threshold=settings.DEDUPLICATOR_CONFIDENCE_THRESHOLD,
            )
            summary = await run_deduplicator(
                session,
                threshold=settings.DEDUPLICATOR_CONFIDENCE_THRESHOLD,
                dry_run=False,
                scope=scope,
            )
            logger.info(
                "deduplicator_finished_fallback",
                reason=reason,
                scope=scope,
                events_checked=summary.events_checked,
                event_duplicates=summary.event_duplicates,
                providers_checked=summary.providers_checked,
                provider_duplicates=summary.provider_duplicates,
            )
            return True
        except Exception as exc:
            logger.warning("deduplicator_failed", reason=reason, error=str(exc))
            return False
        finally:
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})


async def _run_loop() -> None:
    await run_deduplicator_locked(reason="startup", scope="events")

    while True:
        next_run = _next_run_at()
        delay = max(1.0, (next_run - datetime.now(_DUBAI_TZ)).total_seconds())
        logger.info("deduplicator_scheduled", next_run_at=next_run.isoformat())
        await asyncio.sleep(delay)
        await run_deduplicator_locked(reason="daily_schedule", scope="events")
        if datetime.now(_DUBAI_TZ).weekday() == settings.DEDUPLICATOR_PROVIDER_RUN_WEEKDAY_DUBAI:
            await run_deduplicator_locked(reason="weekly_provider_schedule", scope="providers")


def start_deduplicator_scheduler() -> asyncio.Task | None:
    if settings.BACKGROUND_JOBS_PAUSED:
        logger.info("deduplicator_paused")
        return None
    if not settings.DEDUPLICATOR_AUTORUN:
        logger.info("deduplicator_disabled")
        return None
    return asyncio.create_task(_run_loop(), name="content-deduplicator")


async def stop_deduplicator_scheduler(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
