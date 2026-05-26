"""Tier B (Exa variant): pull current Dubai 'what's on' pages per lifestyle
category via Exa and store them as feed items.

Cheaper than a scraper and needs no extra infra — we reuse the Exa key. Lossy on
structured fields (date/venue/price are usually absent in guide/listicle pages),
so each item is essentially a current, real, tappable link tagged with our
category key. Because we query *per category*, the 6 lifestyle tiles bucket
correctly by construction. See backend/INGESTION.md (Tier B).
"""

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import event_image, exa_client
from app.ingestion.events import _parse_dt
from app.ingestion.events_extract import extract_events
from app.services import events as events_service

logger = get_logger(__name__)

# Our 6 lifestyle keys → the Exa query that finds current pages for each.
CATEGORY_QUERIES: dict[str, str] = {
    "dining": "Dubai brunch bookings, dining events and food festivals this weekend with dates and prices",
    "events": "things to do in Dubai this weekend",
    "nightlife": "Dubai concerts, live gigs, club nights and comedy shows this month tickets",
    "shopping": "Dubai shopping, malls, markets and deals this week",
    "family": "family and kids activities and days out in Dubai this weekend",
    "outdoors": "outdoor activities, beaches and desert experiences in Dubai this week",
}

_WS = re.compile(r"\s+")
_SLUG = re.compile(r"[^a-z0-9]+")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").replace("www.", "")


def _slug(s: str) -> str:
    return _SLUG.sub("-", s.lower()).strip("-")[:60] or "event"


def _valid_http(u: str | None) -> bool:
    if not u:
        return False
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc)


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
        "image_url": r.get("image"),  # Exa's og:image; mirrored to R2 before upsert
        "source": _host(url) or "exa",
        "starts_at": None,  # guides/listicles aren't single dated events
        # Rotate roughly weekly: re-ingested pages refresh this, stale ones expire.
        "expires_at": datetime.now(timezone.utc) + timedelta(days=ttl_days),
        "is_published": True,
    }


def _finalize(raw: dict, page: dict, category: str) -> dict | None:
    """Turn one Claude-extracted event into an Event row. Drops past / far-future
    dated events; synthesizes a unique URL when there's no specific booking link."""
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    starts_at = _parse_dt(raw.get("starts_at"))
    ends_at = _parse_dt(raw.get("ends_at"))
    now = datetime.now(timezone.utc)
    if starts_at and (starts_at < now - timedelta(days=1) or starts_at > now + timedelta(days=60)):
        return None

    booking = raw.get("booking_url")
    url = booking if _valid_http(booking) else f"{page['url']}#{_slug(title)}"
    horizon = ends_at or starts_at
    expires_at = (horizon + timedelta(days=1)) if horizon else now + timedelta(days=14)
    return {
        "title": title[:255],
        "description": (raw.get("description") or None),
        "category": category,
        "venue": raw.get("venue"),
        "area": raw.get("area"),
        "url": url[:1024],
        # Events extracted from one page share that page's Exa image (no
        # per-event art exists); better than a gradient. Mirrored to R2 below.
        "image_url": page.get("image"),
        "price_from": (raw.get("price_from") or None),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "source": _host(url) or "exa",
        "expires_at": expires_at,
        "is_published": True,
    }


def _dedup_key(row: dict) -> tuple[str, str]:
    d = row.get("starts_at")
    return (row["title"].lower().strip(), d.date().isoformat() if d else "")


async def ingest_exa_events(
    session: AsyncSession,
    *,
    per_category: int = 4,
    categories: list[str] | None = None,
) -> int:
    """Search Exa per category and upsert events. When extraction is enabled, each
    discovered page is read by Claude into structured events; otherwise the page
    itself becomes a feed item. Returns newly inserted count (re-runs refresh)."""
    if not settings.EXA_API_KEY:
        raise RuntimeError("EXA_API_KEY not set")
    keys = categories or list(CATEGORY_QUERIES)
    use_extract = settings.EVENTS_EXTRACT and bool(settings.ANTHROPIC_API_KEY)
    today = date.today()

    client = None
    if use_extract:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    seen: set[tuple[str, str]] = set()
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

        rows: list[dict] = []
        if use_extract:
            for page in results[: settings.EVENTS_EXTRACT_MAX_PAGES]:
                extracted = await extract_events(
                    page.get("text") or "", url=page["url"], category=key, today=today, client=client
                )
                for raw in extracted:
                    row = _finalize(raw, page, key)
                    if row is None:
                        continue
                    dk = _dedup_key(row)
                    if dk in seen:
                        continue
                    seen.add(dk)
                    rows.append(row)
        else:
            rows = [e for e in (_to_event(r, key) for r in results) if e]

        # Give each event a distinct, renderable image: a per-event Exa search
        # (title+venue), falling back to the article og:image, mirrored to R2.
        await event_image.resolve_images(rows)
        n = await events_service.upsert_events(session, rows)
        inserted += n
        logger.info(
            "exa_events_category", category=key, fetched=len(results), kept=len(rows), inserted=n,
            mode="extract" if use_extract else "page",
        )
    return inserted
