"""Authoritative source registry for the Tier A essentials KB.

A curated allowlist of (url, category, refresh_ttl_days). ``register_sources``
upserts these into the ``sources`` table **without fetching**, so the TTL refresh
worker (``scripts/refresh_kb.py``) picks them up on its own cadence. TTLs encode
volatility: fines/fees refresh faster than stable explainer pages.

These URLs are a curated starting point — refine them as the first refresh run
reports empties/failures. Exa-contents resolves each by URL from its index, so a
stale path simply yields no text (logged) rather than breaking the batch.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.source import Source

logger = get_logger(__name__)


@dataclass(frozen=True)
class SourceSeed:
    url: str
    category: str
    ttl_days: int


# Volatility tiers: rules ~60d · fines/fees ~30d · stable explainers ~90d.
REGISTRY: list[SourceSeed] = [
    # ── Visa & residency ──
    SourceSeed("https://u.ae/en/information-and-services/visa-and-emirates-id/residence-visas", "visa", 60),
    SourceSeed("https://u.ae/en/information-and-services/visa-and-emirates-id/getting-a-residence-visa", "visa", 60),
    SourceSeed("https://u.ae/en/information-and-services/visa-and-emirates-id/renew-residency-visa", "visa", 60),
    SourceSeed("https://u.ae/en/information-and-services/visa-and-emirates-id/types-of-visa/golden-visa", "visa", 60),
    # ── Emirates ID ──
    SourceSeed("https://u.ae/en/information-and-services/visa-and-emirates-id/emirates-id", "emirates_id", 60),
    # ── Health insurance ──
    SourceSeed("https://u.ae/en/information-and-services/health-and-fitness/getting-a-health-insurance", "insurance", 60),
    SourceSeed("https://u.ae/en/information-and-services/health-and-fitness/health-insurance-in-the-uae", "insurance", 90),
    # ── Housing & tenancy (Ejari) ──
    SourceSeed("https://u.ae/en/information-and-services/housing", "housing", 90),
    SourceSeed("https://u.ae/en/information-and-services/housing/renting-a-property", "housing", 60),
    # ── Utilities (DEWA) ──
    SourceSeed("https://u.ae/en/information-and-services/housing/connecting-to-utilities-water-electricity-gas", "utilities", 60),
    # ── Transport & traffic fines (RTA / Salik) ──
    SourceSeed("https://u.ae/en/information-and-services/transportation", "transport", 90),
    SourceSeed("https://u.ae/en/information-and-services/justice-safety-and-the-law/handling-traffic-violations", "fines", 30),
    # ── Banking & finance ──
    SourceSeed("https://u.ae/en/information-and-services/finance-and-investment/banking", "banking", 90),
    # ── Education & schooling ──
    SourceSeed("https://u.ae/en/information-and-services/education", "education", 90),
]


async def register_sources(
    session: AsyncSession, seeds: list[SourceSeed] | None = None
) -> int:
    """Idempotently upsert the registry into ``sources``. Returns rows added.
    Existing rows keep their fetch state but have their TTL synced to policy."""
    seeds = seeds or REGISTRY
    added = 0
    for seed in seeds:
        existing = (
            await session.execute(select(Source).where(Source.url == seed.url))
        ).scalar_one_or_none()
        if existing is None:
            session.add(Source(url=seed.url, category=seed.category, refresh_ttl_days=seed.ttl_days))
            added += 1
        else:
            existing.refresh_ttl_days = seed.ttl_days
            if not existing.category:
                existing.category = seed.category
    await session.commit()
    logger.info("sources_registered", total=len(seeds), added=added)
    return added
