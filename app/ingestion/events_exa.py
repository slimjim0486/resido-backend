"""Tier B (Exa variant): pull current Dubai 'what's on' pages per lifestyle
category via Exa and store them as feed items.

Cheaper than a scraper and needs no extra infra — we reuse the Exa key. Lossy on
structured fields (date/venue/price are usually absent in guide/listicle pages),
so each item is essentially a current, real, tappable link tagged with our
category key. Because we query *per category*, the 6 lifestyle tiles bucket
correctly by construction. See backend/INGESTION.md (Tier B).
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import exa_client
from app.services import events as events_service

logger = get_logger(__name__)

# Our 6 lifestyle keys → the Exa query that finds current pages for each.
CATEGORY_QUERIES: dict[str, str] = {
    "dining": "best restaurants, new openings and brunches in Dubai this week",
    "events": "things to do in Dubai this weekend",
    "nightlife": "Dubai live music, shows and nightlife this week",
    "shopping": "Dubai shopping, malls, markets and deals this week",
    "family": "family and kids activities and days out in Dubai this weekend",
    "outdoors": "outdoor activities, beaches and desert experiences in Dubai this week",
}

_WS = re.compile(r"\s+")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").replace("www.", "")


def _snippet(text: str | None, limit: int = 240) -> str | None:
    if not text:
        return None
    s = _WS.sub(" ", text).strip()
    if not s:
        return None
    return s[: s[:limit].rfind(" ")] + "…" if len(s) > limit else s


def _to_event(r: dict, category: str, *, ttl_days: int = 14) -> dict | None:
    url = r.get("url")
    title = (r.get("title") or "").strip()
    if not url or not title:
        return None
    return {
        "title": title[:255],
        "description": _snippet(r.get("text")),
        "category": category,
        "url": url,
        "source": _host(url) or "exa",
        "starts_at": None,  # guides/listicles aren't single dated events
        # Rotate roughly weekly: re-ingested pages refresh this, stale ones expire.
        "expires_at": datetime.now(timezone.utc) + timedelta(days=ttl_days),
        "is_published": True,
    }


async def ingest_exa_events(
    session: AsyncSession,
    *,
    per_category: int = 4,
    categories: list[str] | None = None,
) -> int:
    """Search Exa per category and upsert results as feed items. Returns the
    number of newly inserted events (re-runs refresh existing rows)."""
    if not settings.EXA_API_KEY:
        raise RuntimeError("EXA_API_KEY not set")
    keys = categories or list(CATEGORY_QUERIES)
    inserted = 0
    for key in keys:
        query = CATEGORY_QUERIES.get(key)
        if not query:
            continue
        try:
            results = await exa_client.search(query, num_results=per_category, with_text=True)
        except Exception as exc:  # per-category resilience
            logger.warning("exa_events_search_failed", category=key, error=str(exc))
            continue
        rows = [e for e in (_to_event(r, key) for r in results) if e]
        n = await events_service.upsert_events(session, rows)
        inserted += n
        logger.info("exa_events_category", category=key, fetched=len(results), inserted=n)
    return inserted
