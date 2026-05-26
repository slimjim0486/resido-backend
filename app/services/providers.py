"""Service providers — query + upsert + ranking. See backend/INGESTION.md.

The Services vertical is Resido's monetization surface. Providers are ranked by a
**Bayesian trust score** (so a 5.0 from 3 reviews doesn't outrank a 4.6 from 800),
with the Google Maps scrape order kept as a tiebreaker.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pricing import parse_aed
from app.models.service import ServiceProvider

# Bayesian (IMDB-style) prior: until a provider has ~m reviews, its score is
# pulled toward the global mean rating C. Tunable, but these match the spec.
BAYES_M = 20  # "minimum reviews to trust the raw rating" weight
BAYES_C = 4.2  # assumed global mean rating across Dubai service providers

# Fields an ingestion source may set — a noisy actor payload can't write arbitrary
# attributes onto the row. (score is derived, not source-provided.)
_WRITABLE = {
    "name", "category", "area", "address", "lat", "lng", "rating", "reviews_count",
    "phone", "whatsapp", "website", "maps_url", "price_level", "photo_url", "hours",
    "highlights", "google_rank", "is_sponsored", "source",
    # Website-extracted pricing (see ingestion/providers_pricing.py).
    "price_from", "price_to", "price_unit", "price_notes", "price_fetched_at",
}


def bayesian_score(rating: float | None, reviews_count: int | None) -> float:
    """Weighted rating: (v/(v+m))·R + (m/(v+m))·C.

    With few reviews the score sits near the prior C; with many it converges on
    the provider's own rating R. Returns 0.0 only when there's no signal at all.
    """
    v = reviews_count or 0
    r = rating if rating is not None else 0.0
    if v <= 0 and not r:
        return 0.0
    denom = v + BAYES_M
    return (v / denom) * r + (BAYES_M / denom) * BAYES_C


async def list_providers(
    session: AsyncSession,
    *,
    category: str | None = None,
    area: str | None = None,
    min_rating: float | None = None,
    query: str | None = None,
    max_price: float | None = None,
    limit: int = 20,
) -> list[ServiceProvider]:
    """Active providers, best-trust first (Bayesian ``score`` desc, Google scrape
    order as tiebreaker), behind optional filters:

    - ``category`` exact grid key; ``area`` substring match on the neighbourhood.
    - ``min_rating`` floors the raw Google rating (unrated rows drop).
    - ``query`` substring match on the business name.
    - ``max_price`` keeps providers whose advertised ``price_from`` is at or under
      the AED budget, **plus** those with no advertised price (mostly null until a
      pricing pass runs — hiding them would gut results). Applied in Python since
      price is a sparse display string, not a numeric column.
    """
    stmt = select(ServiceProvider).where(ServiceProvider.is_active.is_(True))
    if category:
        stmt = stmt.where(ServiceProvider.category == category)
    if area:
        stmt = stmt.where(ServiceProvider.area.ilike(f"%{area.strip()}%"))
    if min_rating is not None:
        stmt = stmt.where(ServiceProvider.rating >= min_rating)
    if query and query.strip():
        stmt = stmt.where(ServiceProvider.name.ilike(f"%{query.strip()}%"))
    stmt = stmt.order_by(
        ServiceProvider.score.desc(),
        ServiceProvider.google_rank.asc().nullslast(),
    )
    # Over-fetch when budget-filtering in Python so we can still fill `limit`.
    stmt = stmt.limit(limit * 4 if max_price is not None else limit)
    rows = list((await session.execute(stmt)).scalars().all())

    if max_price is not None:
        rows = [
            p for p in rows
            if (amount := parse_aed(p.price_from)) is None or amount <= max_price
        ]
    return rows[:limit]


async def get_provider(
    session: AsyncSession, provider_id: UUID
) -> ServiceProvider | None:
    """Fetch one provider by id. The quote-draft CTA loads the provider
    server-side (authoritative name/area/contact) rather than trusting the
    client-passed fields. Returns None if it doesn't exist."""
    result = await session.execute(
        select(ServiceProvider).where(ServiceProvider.id == provider_id)
    )
    return result.scalar_one_or_none()


async def upsert_provider(session: AsyncSession, data: dict) -> bool:
    """Insert or update one provider keyed by place_id. Recomputes score from the
    latest rating/reviews. Returns True if newly inserted."""
    place_id = data.get("place_id")
    if not place_id or not data.get("name"):
        return False
    result = await session.execute(
        select(ServiceProvider).where(ServiceProvider.place_id == place_id)
    )
    provider = result.scalar_one_or_none()
    inserted = provider is None
    if provider is None:
        provider = ServiceProvider(place_id=place_id)
        session.add(provider)
    for field, value in data.items():
        if field in _WRITABLE:
            setattr(provider, field, value)
    provider.score = bayesian_score(data.get("rating"), data.get("reviews_count"))
    provider.fetched_at = datetime.now(timezone.utc)
    # Appearing in a scrape revives a previously-retired provider.
    provider.is_active = True
    return inserted


async def upsert_providers(session: AsyncSession, items: list[dict]) -> int:
    """Upsert a batch; returns the count of newly inserted rows."""
    inserted = 0
    for item in items:
        if await upsert_provider(session, item):
            inserted += 1
    await session.commit()
    return inserted


async def retire_stale(
    session: AsyncSession, category: str, area: str, *, grace_days: int
) -> int:
    """Soft-hide providers in a *just-scraped* cell that haven't been seen in a
    scrape for ``grace_days`` (≈ 2 monthly cycles). Run right after the cell's
    upsert: survivors have a fresh ``fetched_at`` and are safe; rows missing long
    enough flip to ``is_active = False`` (kept for revival + lead attribution, not
    deleted). Returns the number retired. Commits."""
    threshold = datetime.now(timezone.utc) - timedelta(days=grace_days)
    stmt = (
        update(ServiceProvider)
        .where(ServiceProvider.category == category)
        .where(ServiceProvider.area == area)
        .where(ServiceProvider.is_active.is_(True))
        .where(ServiceProvider.fetched_at < threshold)
        .values(is_active=False)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount or 0


async def cell_freshness(session: AsyncSession) -> dict[tuple[str, str], datetime]:
    """Most-recent ``fetched_at`` per (category, area) cell.

    The Tier A-style TTL refresh uses this to re-scrape only cells whose data has
    aged past the TTL — no separate bookkeeping table needed."""
    stmt = select(
        ServiceProvider.category,
        ServiceProvider.area,
        func.max(ServiceProvider.fetched_at),
    ).group_by(ServiceProvider.category, ServiceProvider.area)
    result = await session.execute(stmt)
    return {(cat, area or ""): ts for cat, area, ts in result.all()}
