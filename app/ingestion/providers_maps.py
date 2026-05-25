"""Services ingestion: run the Apify Google Maps actor per (category × area) cell,
normalize defensively, score, and upsert. See backend/INGESTION.md (Services).

Mirrors the events Apify pattern (`events.py`): the actor is configurable
(``APIFY_MAPS_ACTOR``, default the off-the-shelf ``compass/crawler-google-places``)
and the normalizer is defensive about field names so we can swap actors without
touching the pipeline. Each cell runs in its own try/except so one bad query can't
sink the grid run.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.ingestion import apify_client, r2_storage
from app.ingestion.services_registry import Category, grid, search_term
from app.services import providers as providers_service

logger = get_logger(__name__)


def _photo_key(row: dict) -> str:
    """Stable R2 key for a provider photo, keyed on the durable Google place_id so a
    monthly re-scrape overwrites the same object (bounded object count, fresh photo)."""
    digest = hashlib.sha1(row["place_id"].encode()).hexdigest()
    return f"providers/{digest}"

# Google Maps detail-page scrapes are slow; give a cell run plenty of headroom.
_CELL_TIMEOUT = 300.0


def _first(item: dict, *keys: str):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _latlng(item: dict) -> tuple[float | None, float | None]:
    loc = item.get("location")
    if isinstance(loc, dict):
        return _to_float(loc.get("lat")), _to_float(loc.get("lng"))
    return _to_float(item.get("lat")), _to_float(item.get("lng"))


def _whatsapp(phone_unformatted) -> str | None:
    """Derive a wa.me-ready number from a UAE *mobile* only (landlines can't do
    WhatsApp, so we'd rather show no button than a dead one)."""
    if not phone_unformatted:
        return None
    digits = "".join(ch for ch in str(phone_unformatted) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 10:  # local "05XXXXXXXX"
        digits = "971" + digits[1:]
    return digits if digits.startswith("9715") else None


def _photo(item: dict) -> str | None:
    url = _first(item, "imageUrl", "imageUrls", "images", "photo", "thumbnail")
    if isinstance(url, list):
        url = url[0] if url else None
    if isinstance(url, dict):  # some actors wrap as {"imageUrl": ...}
        url = url.get("imageUrl") or url.get("url")
    return str(url)[:1024] if url else None


def _hours(item: dict):
    """Opening hours as scraped (list of {day, hours}); stored verbatim as JSONB."""
    h = _first(item, "openingHours", "hours", "openingHoursBusiness")
    return h if isinstance(h, (list, dict)) else None


def normalize(
    item: dict, *, category: str, area: str, source: str, rank: int | None = None
) -> dict | None:
    """Map a loosely-shaped Maps actor item onto our ServiceProvider fields. Returns
    None when the item lacks the minimum (name + place_id) to be useful."""
    name = _first(item, "title", "name")
    place_id = _first(item, "placeId", "place_id", "cid", "fid")
    if not name or not place_id:
        return None

    lat, lng = _latlng(item)
    return {
        "name": str(name)[:255],
        "category": category,
        # Canonical grid area (so cell filters line up); fall back to the scraped
        # neighbourhood/city when we somehow lack it.
        "area": area or _first(item, "neighborhood", "neighbourhood", "city"),
        "address": _first(item, "address", "street"),
        "lat": lat,
        "lng": lng,
        "rating": _to_float(_first(item, "totalScore", "rating", "stars")),
        "reviews_count": _to_int(_first(item, "reviewsCount", "reviews", "userRatingCount")),
        "phone": _first(item, "phone", "phoneUnformatted"),
        "whatsapp": _whatsapp(_first(item, "phoneUnformatted", "phone")),
        "website": (str(_first(item, "website", "webUrl") or "")[:1024]) or None,
        "maps_url": (str(_first(item, "url", "mapsUrl", "googleMapsUrl") or "")[:1024]) or None,
        "place_id": str(place_id)[:255],
        "price_level": (str(_first(item, "price", "priceLevel") or "")[:16]) or None,
        "photo_url": _photo(item),
        "hours": _hours(item),
        "google_rank": _to_int(item.get("rank")) or rank,
        "is_sponsored": bool(_first(item, "isAdvertisement", "isAd", "sponsored") or False),
        "source": source,
    }


async def _scrape_cell(category: Category, area: str, *, per_cell: int) -> list[dict]:
    """Run the actor for one cell and return raw dataset items."""
    actor = settings.APIFY_MAPS_ACTOR
    if not actor:
        raise RuntimeError("No Apify maps actor configured (set APIFY_MAPS_ACTOR)")
    run_input = {
        "searchStringsArray": [search_term(category, area)],
        "maxCrawledPlacesPerSearch": per_cell,
        "language": "en",
        "countryCode": "ae",
        "skipClosedPlaces": True,
        # Opening hours require opening each place's detail page (the costliest
        # toggle); gated so re-seeds can run fast without it.
        "scrapePlaceDetailPage": settings.SERVICES_SCRAPE_DETAILS,
    }
    return await apify_client.run_actor(actor, run_input, timeout=_CELL_TIMEOUT)


def normalize_cell(
    raw: list[dict], *, category: str, area: str, source: str
) -> list[dict]:
    """Normalize + score a cell's raw items (no DB). Used by ingest and the dry run."""
    rows: list[dict] = []
    for i, item in enumerate(raw, start=1):
        row = normalize(item, category=category, area=area, source=source, rank=i)
        if row:
            row["score"] = providers_service.bayesian_score(
                row.get("rating"), row.get("reviews_count")
            )
            rows.append(row)
    return rows


async def ingest_cell(
    session: AsyncSession, category: Category, area: str, *, per_cell: int
) -> int:
    """Scrape one (category, area) cell, normalize, and upsert. Returns inserted."""
    source = (settings.APIFY_MAPS_ACTOR or "google_maps").split("/")[-1]
    raw = await _scrape_cell(category, area, per_cell=per_cell)
    rows = normalize_cell(raw, category=category.key, area=area, source="google_maps")
    # Re-host scraped Google photos in R2 (their source URLs are token-signed and
    # expire) before persisting. No-op when R2 isn't configured.
    await r2_storage.mirror_field(rows, src_field="photo_url", key_fn=_photo_key)
    inserted = await providers_service.upsert_providers(session, rows)
    # Reconcile this cell: providers not seen for ~2 monthly cycles get soft-hidden.
    # On a first scrape this is a no-op (survivors were just refreshed).
    retired = await providers_service.retire_stale(
        session, category.key, area, grace_days=settings.SERVICES_STALE_GRACE_DAYS
    )
    logger.info(
        "providers_cell",
        category=category.key, area=area, source=source,
        fetched=len(raw), kept=len(rows), inserted=inserted, retired=retired,
    )
    return inserted


async def ingest_grid(
    session: AsyncSession,
    *,
    cells: list[tuple[Category, str]] | None = None,
    per_cell: int | None = None,
) -> int:
    """Scrape a set of grid cells (default: the whole registry), resilient per-cell.
    Returns the total newly-inserted providers."""
    cells = cells if cells is not None else grid()
    per = per_cell or settings.SERVICES_PER_CELL
    inserted = 0
    for category, area in cells:
        try:
            inserted += await ingest_cell(session, category, area, per_cell=per)
        except Exception as exc:  # one bad cell must not sink the run
            await session.rollback()
            logger.warning(
                "providers_cell_failed", category=category.key, area=area, error=str(exc)
            )
    return inserted


async def dry_run_cell(category: Category, area: str, *, per_cell: int) -> list[dict]:
    """Read-only: scrape one cell and return normalized+scored rows. No DB write."""
    raw = await _scrape_cell(category, area, per_cell=per_cell)
    return normalize_cell(raw, category=category.key, area=area, source="google_maps")


def due_cells(
    freshness: dict[tuple[str, str], datetime], *, ttl_days: int
) -> list[tuple[Category, str]]:
    """Grid cells never scraped, or whose freshest row has aged past the TTL."""
    threshold = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    due: list[tuple[Category, str]] = []
    for category, area in grid():
        last = freshness.get((category.key, area))
        if last is None or last < threshold:
            due.append((category, area))
    return due
