"""Tier B ingestion: pull lifestyle events via Apify → normalize → upsert.

Apify is the right tool here — structured, recurring, JS-heavy listing sites
(Platinumlist / Time Out / Visit Dubai). The actor is configurable
(``APIFY_EVENTS_ACTOR``) and the normalizer is defensive about field names so we
can swap actors without touching the pipeline. No embeddings — these rows are a
self-expiring feed, not a RAG corpus.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import apify_client
from app.services import events as events_service

logger = get_logger(__name__)


def _first(item: dict, *keys: str):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value  # tolerate epoch ms
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _price(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return "Free" if value == 0 else f"AED {int(value)}"
    return str(value)[:80]


def normalize(item: dict, *, source: str, default_category: str = "events") -> dict | None:
    """Map a loosely-shaped actor item onto our Event fields. Returns None when
    the item lacks the minimum (title + url) to be useful."""
    title = _first(item, "title", "name", "eventName")
    url = _first(item, "url", "link", "eventUrl", "ticketUrl")
    if not title or not url:
        return None

    starts_at = _parse_dt(_first(item, "startDate", "start", "starts_at", "dateStart"))
    ends_at = _parse_dt(_first(item, "endDate", "end", "ends_at", "dateEnd"))
    # Self-expiry: keep showing until a day after it ends (or starts, if no end).
    horizon = ends_at or starts_at
    expires_at = horizon + timedelta(days=1) if horizon else None

    category = _first(item, "category", "type") or default_category
    return {
        "title": str(title)[:255],
        "description": _first(item, "description", "summary", "snippet"),
        "category": str(category).lower()[:80],
        "venue": _first(item, "venue", "venueName", "place", "location"),
        "area": _first(item, "area", "neighbourhood", "city"),
        "url": str(url)[:1024],
        "image_url": _first(item, "image", "imageUrl", "thumbnail", "photo"),
        "price_from": _price(_first(item, "price", "priceFrom", "minPrice")),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "source": source,
        "expires_at": expires_at,
        "is_published": True,
    }


async def ingest_apify_events(
    session: AsyncSession,
    *,
    actor_id: str | None = None,
    run_input: dict | None = None,
    source: str | None = None,
    default_category: str = "events",
) -> int:
    """Run the configured Apify events actor, normalize, and upsert. Returns the
    number of newly inserted events."""
    actor = actor_id or settings.APIFY_EVENTS_ACTOR
    if not actor:
        raise RuntimeError("No Apify events actor configured (set APIFY_EVENTS_ACTOR)")
    src = source or actor.split("/")[-1]

    raw = await apify_client.run_actor(actor, run_input or {})
    normalized = [
        n for n in (normalize(i, source=src, default_category=default_category) for i in raw) if n
    ]
    inserted = await events_service.upsert_events(session, normalized)
    logger.info(
        "events_ingested", source=src, fetched=len(raw), kept=len(normalized), inserted=inserted
    )
    return inserted
