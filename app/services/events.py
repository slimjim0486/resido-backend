"""Lifestyle events feed — query + upsert. See backend/INGESTION.md (Tier B).

Events are deliberately NOT embedded, so free-text search here is keyword
(`ilike`) over title/description/venue — not semantic. That's the right tool for
a small, time-bound feed: cheap, exact, and no stale vectors to maintain.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event

# Events are stored in UTC; day-of-week / date filters must read in Dubai local
# time or a Friday-evening event lands on Thursday (Dubai is UTC+4).
_DUBAI_TZ = "Asia/Dubai"

# Day name/abbreviation → Postgres extract('dow') value (Sunday=0 … Saturday=6).
_WEEKDAYS = {
    "sunday": 0, "sun": 0,
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 3, "wed": 3, "weds": 3,
    "thursday": 4, "thu": 4, "thur": 4, "thurs": 4,
    "friday": 5, "fri": 5,
    "saturday": 6, "sat": 6,
}


def weekday_index(value) -> int | None:
    """A weekday name/abbreviation (or a 0–6 int, Sun=0) → Postgres dow; else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    return _WEEKDAYS.get(str(value).strip().lower())

# Fields an ingestion source is allowed to set, so a noisy actor payload can't
# write arbitrary attributes onto the row.
_WRITABLE = {
    "title", "description", "category", "venue", "area", "image_url",
    "price_from", "price_min", "family_friendly",
    "starts_at", "ends_at", "source", "expires_at", "is_published",
}


async def search_events(
    session: AsyncSession,
    *,
    query: str | None = None,
    category: str | None = None,
    area: str | None = None,
    family_friendly: bool | None = None,
    max_price: float | None = None,
    free_only: bool = False,
    weekday=None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
) -> list[Event]:
    """The one query behind both the public feed and the agent's `find_events`.

    Always scoped to live rows (published & not expired), soonest-first with
    undated ('ongoing') events last. Every filter is optional and additive:

    - ``query``    keyword ``ilike`` over title/description/venue (no embeddings).
    - ``category`` exact lifestyle key (dining/events/nightlife/shopping/family/outdoors).
    - ``area``     substring match on the Dubai neighbourhood.
    - ``family_friendly=True`` keeps only rows positively flagged kid-friendly
      (unknown/false drop) — never promotes unknowns.
    - ``free_only`` keeps only free events; otherwise ``max_price`` keeps events at
      or under that AED budget **plus** those with no listed price (a thin feed
      shouldn't hide a possibly-free event — the caller flags the unknown).
    - ``weekday`` / ``date_from`` / ``date_to`` filter on the event's *Dubai-local*
      start; these imply a known start time (undated events drop).
    """
    now = datetime.now(timezone.utc)
    stmt = (
        select(Event)
        .where(Event.is_published.is_(True))
        .where(or_(Event.expires_at.is_(None), Event.expires_at >= now))
    )

    if category:
        stmt = stmt.where(func.lower(Event.category) == category.strip().lower())
    if area:
        stmt = stmt.where(Event.area.ilike(f"%{area.strip()}%"))
    if query and query.strip():
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Event.title.ilike(like),
                Event.description.ilike(like),
                Event.venue.ilike(like),
            )
        )
    if family_friendly:
        stmt = stmt.where(Event.family_friendly.is_(True))
    if free_only:
        stmt = stmt.where(Event.price_min == 0)
    elif max_price is not None:
        stmt = stmt.where(or_(Event.price_min <= max_price, Event.price_min.is_(None)))

    dow = weekday_index(weekday)
    if dow is not None or date_from or date_to:
        local_start = func.timezone(_DUBAI_TZ, Event.starts_at)
        stmt = stmt.where(Event.starts_at.is_not(None))
        if dow is not None:
            stmt = stmt.where(func.extract("dow", local_start) == dow)
        if date_from:
            stmt = stmt.where(func.date(local_start) >= date_from)
        if date_to:
            stmt = stmt.where(func.date(local_start) <= date_to)

    stmt = stmt.order_by(Event.starts_at.is_(None), Event.starts_at.asc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_active_events(
    session: AsyncSession,
    *,
    category: str | None = None,
    limit: int = 20,
) -> list[Event]:
    """Published, unexpired events soonest-first — the bare feed. A thin wrapper
    over :func:`search_events` so the feed and the agent never diverge."""
    return await search_events(session, category=category, limit=limit)


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


async def delete_expired_events(session: AsyncSession, *, retention_days: int = 30) -> int:
    """Physically remove events that have been hidden past the retention window.

    Visibility is controlled by ``expires_at`` in queries; this cleanup is only
    for storage hygiene after a short debugging/audit window.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await session.execute(
        delete(Event).where(Event.expires_at.is_not(None), Event.expires_at < cutoff)
    )
    await session.commit()
    return result.rowcount or 0
