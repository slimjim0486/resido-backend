"""Tier C — just-in-time web search for when the KB misses.

When `kb_search` returns nothing, the agent falls back to a live Exa search so it
can still answer the long tail and the freshest questions. Results are cached by
query (repeat asks are free), and any hit on an authoritative domain is *promoted*
into the `sources` registry so the next TTL refresh ingests it into the verified
Tier A KB — the KB grows from real demand instead of speculative scraping.
See backend/INGESTION.md (Tier C).
"""

import hashlib
import json
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.ingestion import exa_client
from app.models.source import Source

logger = get_logger(__name__)

_CACHE_PREFIX = "livesearch:"
_CACHE_TTL_SECONDS = 6 * 3600  # repeat questions are free for 6h

# Domains trusted enough to promote into the verified KB. Everything else can be
# shown as a live result but is never auto-ingested.
_GOV_SUFFIX = ".gov.ae"
_AUTHORITATIVE_HOSTS = {
    "u.ae", "rta.ae", "centralbank.ae", "dha.gov.ae", "mohap.gov.ae",
    "mohre.gov.ae", "khda.gov.ae", "gdrfa.gov.ae", "icp.gov.ae", "dewa.gov.ae",
}


def _is_authoritative(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(_GOV_SUFFIX):
        return True
    return host in _AUTHORITATIVE_HOSTS or any(host.endswith("." + h) for h in _AUTHORITATIVE_HOSTS)


async def live_search(
    session: AsyncSession,
    query: str,
    *,
    category: str | None = None,
    num_results: int = 4,
) -> list[dict]:
    """Cached just-in-time Exa search. Returns KB-tool-shaped result dicts marked
    ``verified: False``; promotes authoritative hits into ``sources``."""
    cache_key = _CACHE_PREFIX + hashlib.sha256(f"{category}:{query}".encode()).hexdigest()
    redis = get_redis()
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("livesearch_cache_read_failed", error=str(exc))

    try:
        hits = await exa_client.search(query, num_results=num_results, with_text=True)
    except Exception as exc:  # no key / network — degrade to "nothing found"
        logger.warning("livesearch_failed", error=str(exc))
        return []

    today = date.today().isoformat()
    results: list[dict] = []
    for hit in hits:
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        results.append(
            {
                "title": hit.get("title"),
                "url": hit["url"],
                "category": category or "web",
                "verified": False,  # live — not yet in the curated KB
                "fetched_at": today,
                "content": text[:1500],
            }
        )

    if redis is not None and results:
        try:
            await redis.set(cache_key, json.dumps(results), ex=_CACHE_TTL_SECONDS)
        except Exception as exc:  # pragma: no cover - cache is best-effort
            logger.warning("livesearch_cache_write_failed", error=str(exc))

    await _promote_authoritative(session, results, category)
    return results


async def _promote_authoritative(
    session: AsyncSession, results: list[dict], category: str | None
) -> int:
    """Queue authoritative live hits into the Tier A registry (last_fetched_at
    stays NULL, so the next refresh run ingests them). Returns rows promoted."""
    promoted = 0
    for r in results:
        url = r["url"]
        if not _is_authoritative(url):
            continue
        existing = (
            await session.execute(select(Source).where(Source.url == url))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Source(
                    url=url,
                    category=category or r.get("category") or "web",
                    title=r.get("title"),
                    refresh_ttl_days=60,
                )
            )
            promoted += 1
    if promoted:
        await session.commit()
        logger.info("livesearch_promoted", count=promoted)
    return promoted
