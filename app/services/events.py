"""Lifestyle events feed — query + upsert. See backend/INGESTION.md (Tier B)."""

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event

# Fields an ingestion source is allowed to set, so a noisy actor payload can't
# write arbitrary attributes onto the row.
_WRITABLE = {
    "title", "description", "category", "venue", "area", "image_url",
    "price_from", "starts_at", "ends_at", "source", "expires_at", "is_published",
}


async def list_active_events(
    session: AsyncSession,
    *,
    category: str | None = None,
    limit: int = 20,
) -> list[Event]:
    """Published events that haven't expired, soonest first; undated ('ongoing')
    events sink to the bottom."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Event)
        .where(Event.is_published.is_(True))
        .where(or_(Event.expires_at.is_(None), Event.expires_at >= now))
    )
    if category:
        stmt = stmt.where(Event.category == category)
    stmt = stmt.order_by(Event.starts_at.is_(None), Event.starts_at.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def upsert_event(session: AsyncSession, data: dict) -> bool:
    """Insert or update one event keyed by URL. Returns True if newly inserted."""
    url = data.get("url")
    if not url or not data.get("title"):
        return False
    result = await session.execute(select(Event).where(Event.url == url))
    event = result.scalar_one_or_none()
    inserted = event is None
    if event is None:
        event = Event(url=url)
        session.add(event)
    for field, value in data.items():
        if field in _WRITABLE:
            setattr(event, field, value)
    event.fetched_at = datetime.now(timezone.utc)
    return inserted


async def upsert_events(session: AsyncSession, items: list[dict]) -> int:
    """Upsert a batch; returns the count of newly inserted rows."""
    inserted = 0
    for item in items:
        if await upsert_event(session, item):
            inserted += 1
    await session.commit()
    return inserted
