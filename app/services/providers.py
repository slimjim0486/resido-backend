"""Service providers — query + upsert + ranking. See backend/INGESTION.md.

The Services vertical is Resido's monetization surface. Providers are ranked by a
**Bayesian trust score** (so a 5.0 from 3 reviews doesn't outrank a 4.6 from 800),
with the Google Maps scrape order kept as a tiebreaker.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    "google_rank", "is_sponsored", "source",
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
    limit: int = 20,
) -> list[ServiceProvider]:
    """Providers filtered by category/area, best-trust first (score desc), with the
    Google Maps scrape order as a tiebreaker."""
    stmt = select(ServiceProvider)
    if category:
        stmt = stmt.where(ServiceProvider.category == category)
    if area:
        stmt = stmt.where(ServiceProvider.area == area)
    stmt = stmt.order_by(
        ServiceProvider.score.desc(),
        ServiceProvider.google_rank.asc().nullslast(),
    ).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


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
    return inserted


async def upsert_providers(session: AsyncSession, items: list[dict]) -> int:
    """Upsert a batch; returns the count of newly inserted rows."""
    inserted = 0
    for item in items:
        if await upsert_provider(session, item):
            inserted += 1
    await session.commit()
    return inserted


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
