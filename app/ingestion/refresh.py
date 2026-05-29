"""TTL-based staggered refresh of Tier A sources. See backend/INGESTION.md.

Re-fetches only sources whose own ``refresh_ttl_days`` has elapsed, oldest
first, capped at a batch ``limit`` so a single run never re-fetches everything
at once (smooths cost, dodges rate limits). The content-hash gate in
``ingest_url`` means an unchanged page costs just the fetch — no re-embedding.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.ingestion.pipeline import ingest_url
from app.models.source import Source

logger = get_logger(__name__)

_FAILURE_BASE_HOURS = 6
_FAILURE_MAX_HOURS = 48
_DUE_PREFETCH_MULTIPLIER = 5


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _failure_backoff(count: int | None) -> timedelta:
    if not count or count <= 0:
        return timedelta(0)
    hours = min(_FAILURE_MAX_HOURS, _FAILURE_BASE_HOURS * (2 ** (count - 1)))
    return timedelta(hours=hours)


def _in_failure_backoff(source: Source, now: datetime) -> bool:
    attempted_at = _as_aware(source.last_attempted_at)
    if not attempted_at or source.failure_count <= 0:
        return False
    return now - attempted_at < _failure_backoff(source.failure_count)


async def due_sources(session: AsyncSession, *, limit: int = 20) -> list[Source]:
    """Sources never fetched, or whose per-row TTL window has elapsed."""
    # Per-row threshold: now() - (refresh_ttl_days * 1 day).
    threshold = func.now() - func.make_interval(0, 0, 0, Source.refresh_ttl_days)
    stmt = (
        select(Source)
        .where(or_(Source.last_fetched_at.is_(None), Source.last_fetched_at < threshold))
        .order_by(Source.last_fetched_at.asc().nullsfirst())
        .limit(max(limit * _DUE_PREFETCH_MULTIPLIER, limit))
    )
    result = await session.execute(stmt)
    now = datetime.now(timezone.utc)
    due: list[Source] = []
    for source in result.scalars().all():
        if _in_failure_backoff(source, now):
            continue
        due.append(source)
        if len(due) >= limit:
            break
    return due


async def _mark_attempt(session: AsyncSession, source: Source) -> None:
    source.last_attempted_at = datetime.now(timezone.utc)
    await session.commit()


async def _mark_success(session: AsyncSession, source_id) -> None:
    source = await session.get(Source, source_id)
    if source is None:
        return
    source.failure_count = 0
    source.last_error = None
    await session.commit()


async def _mark_failure(session: AsyncSession, source_id, error: Exception) -> None:
    source = await session.get(Source, source_id)
    if source is None:
        return
    source.last_attempted_at = datetime.now(timezone.utc)
    source.failure_count = (source.failure_count or 0) + 1
    source.last_error = str(error)[:2000]
    await session.commit()


async def refresh_sources(session: AsyncSession, *, limit: int = 20) -> dict:
    """Refresh a staggered batch of due sources. Returns per-run counts."""
    sources = await due_sources(session, limit=limit)
    embedded = skipped = failed = 0
    for source in sources:
        source_id = source.id
        try:
            await _mark_attempt(session, source)
            n = await ingest_url(session, source.url, source.category, title=source.title)
            await _mark_success(session, source_id)
            # n == 0 means the hash gate skipped it (unchanged) or the page was empty.
            if n > 0:
                embedded += 1
            else:
                skipped += 1
        except Exception as exc:  # keep the session usable for the next source
            await session.rollback()
            await _mark_failure(session, source_id, exc)
            failed += 1
            logger.warning("refresh_failed", url=source.url, error=str(exc))
    stats = {"due": len(sources), "embedded": embedded, "skipped": skipped, "failed": failed}
    logger.info("refresh_done", **stats)
    return stats
