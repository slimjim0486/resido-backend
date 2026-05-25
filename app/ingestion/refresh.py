"""TTL-based staggered refresh of Tier A sources. See backend/INGESTION.md.

Re-fetches only sources whose own ``refresh_ttl_days`` has elapsed, oldest
first, capped at a batch ``limit`` so a single run never re-fetches everything
at once (smooths cost, dodges rate limits). The content-hash gate in
``ingest_url`` means an unchanged page costs just the fetch — no re-embedding.
"""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.ingestion.pipeline import ingest_url
from app.models.source import Source

logger = get_logger(__name__)


async def due_sources(session: AsyncSession, *, limit: int = 20) -> list[Source]:
    """Sources never fetched, or whose per-row TTL window has elapsed."""
    # Per-row threshold: now() - (refresh_ttl_days * 1 day).
    threshold = func.now() - func.make_interval(0, 0, 0, Source.refresh_ttl_days)
    stmt = (
        select(Source)
        .where(or_(Source.last_fetched_at.is_(None), Source.last_fetched_at < threshold))
        .order_by(Source.last_fetched_at.asc().nullsfirst())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def refresh_sources(session: AsyncSession, *, limit: int = 20) -> dict:
    """Refresh a staggered batch of due sources. Returns per-run counts."""
    sources = await due_sources(session, limit=limit)
    embedded = skipped = failed = 0
    for source in sources:
        try:
            n = await ingest_url(session, source.url, source.category, title=source.title)
            # n == 0 means the hash gate skipped it (unchanged) or the page was empty.
            if n > 0:
                embedded += 1
            else:
                skipped += 1
        except Exception as exc:  # keep the session usable for the next source
            await session.rollback()
            failed += 1
            logger.warning("refresh_failed", url=source.url, error=str(exc))
    stats = {"due": len(sources), "embedded": embedded, "skipped": skipped, "failed": failed}
    logger.info("refresh_done", **stats)
    return stats
